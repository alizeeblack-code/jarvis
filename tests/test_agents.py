import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store as store_mod  # noqa: E402
from config import task_agent  # noqa: E402


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "lock")
    s = store_mod.ProjectStore()
    s._ensure_claude_md = lambda *a, **k: None
    p = s.create_project("demo", str(tmp_path / "repo"))
    return s, p["id"]


def test_new_tasks_run_right_away_unless_parked(st):
    s, pid = st
    t = s.add_task(pid, "write docs")
    assert t["status"] == "pending"                      # sent = it runs
    assert s.add_task(pid, "later", agent="todo")["status"] == "todo"   # Todo only when chosen
    assert s.claim_any_task("worker-1")["id"] == t["id"]


def test_auto_mode_and_explicit_agent_queue_immediately(st):
    s, pid = st
    assert s.add_task(pid, "a", agent="codex")["status"] == "pending"
    s.update_project(pid, settings={"new_task_mode": "auto"})
    assert s.add_task(pid, "b")["status"] == "pending"
    assert s.add_task(pid, "c", agent="me")["status"] == "todo"


def test_assign_moves_todo_to_queue_and_me_stays(st):
    s, pid = st
    t = s.add_task(pid, "x")
    assert s.assign_task(pid, t["id"], "me")["status"] == "todo"
    got = s.assign_task(pid, t["id"], "antigravity")
    assert got["status"] == "pending" and got["agent"] == "antigravity"
    assert s.assign_task(pid, t["id"], "nobody") is None
    claimed = s.claim_any_task("worker-1")
    assert claimed["id"] == t["id"] and claimed["agent"] == "antigravity"
    assert s.assign_task(pid, t["id"], "codex") is None   # already running


def test_mcp_claim_filters_by_executor_and_legacy_gemini(st):
    s, pid = st
    s.add_task(pid, "for codex", agent="codex")
    s.add_task(pid, "legacy", model="gemini")
    assert s.claim_next_task(pid, model="claude") is None
    assert s.claim_next_task(pid, model="gemini")["description"] == "legacy"
    got = s.claim_next_task(pid)                      # any executor
    assert got["description"] == "for codex" and got["agent"] == "codex"


def test_task_agent_fallbacks():
    assert task_agent({"agent": "codex"}) == "codex"
    assert task_agent({"model": "gemini"}) == "antigravity"
    assert task_agent({"worker_id": "worker-ab12"}) == "claude"
    assert task_agent({"worker_id": "mcp-worker"}) == ""


def test_analytics_by_agent(st):
    s, pid = st
    a = s.add_task(pid, "a", agent="codex")
    s.update_task(pid, a["id"], status="done", started_at="2026-11-12T10:00:00",
                  completed_at="2026-11-12T10:10:00")
    b = s.add_task(pid, "b", agent="codex")
    s.update_task(pid, b["id"], status="failed")
    row = s.get_analytics(pid)["by_agent"]["codex"]
    assert row == {"done": 1, "failed": 1, "avg_seconds": 600.0, "success_rate": 0.5}


def test_git_project_runs_multiple_lines_in_parallel_while_nongit_stays_serial(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "lock")
    s = store_mod.ProjectStore()
    s._ensure_claude_md = lambda *a, **k: None

    # Non-git project stays strictly serial
    nongit_dir = tmp_path / "plain"
    nongit_dir.mkdir()
    p_plain = s.create_project("plain", str(nongit_dir))["id"]
    t_p1 = s.add_task(p_plain, "p1")
    s.add_task(p_plain, "p2")
    assert s.claim_any_task("w1")["id"] == t_p1["id"]
    assert s.claim_any_task("w2") is None

    # Finish plain project task so it doesn't interfere
    s.update_task(p_plain, t_p1["id"], status="done")
    s.update_task(p_plain, 2, status="done")

    # Git repo project runs up to default max_parallel=3 independent lines concurrently
    git_dir = tmp_path / "repo"
    (git_dir / ".git").mkdir(parents=True)
    pid = s.create_project("repo", str(git_dir))["id"]
    t1 = s.add_task(pid, "task 1")
    t2 = s.add_task(pid, "task 2")
    t3 = s.add_task(pid, "task 3")
    t4 = s.add_task(pid, "task 4")
    # Follow-up chained onto t1 must wait for t1 even when parallel slots exist
    t1_next = s.add_task(pid, "follow-up 1", predecessor_id=t1["id"])

    c1 = s.claim_any_task("w1")
    c2 = s.claim_any_task("w2")
    c3 = s.claim_any_task("w3")
    assert [c1["id"], c2["id"], c3["id"]] == [t1["id"], t2["id"], t3["id"]]
    # Cap of 3 reached -> t4 and t1_next stay pending
    assert s.claim_any_task("w4") is None

    # Complete t1 -> follow-up t1_next (still line 1) can run, OR t4 if max_parallel allows
    s.update_task(pid, t1["id"], status="done", line=t1["id"])
    c4 = s.claim_any_task("w4")
    assert c4["id"] == t4["id"]
    assert s.claim_any_task("w5") is None

    # Same-line follow-up does not run concurrently with another task on the same line
    s.update_task(pid, t2["id"], status="done")
    c5 = s.claim_any_task("w5")
    assert c5["id"] == t1_next["id"]
    assert c5["branch"] == f"task-{t1['id']}"

    # Fair scheduling: second git project with 0 running tasks is claimed before adding more to pid
    git_dir2 = tmp_path / "repo2"
    (git_dir2 / ".git").mkdir(parents=True)
    pid2 = s.create_project("repo2", str(git_dir2))["id"]
    s.update_task(pid, t3["id"], status="done")
    s.add_task(pid, "extra on repo1")
    t_r2 = s.add_task(pid2, "first on repo2")
    assert s.claim_any_task("w6")["id"] == t_r2["id"]
