"""Agent checklist progress (TodoWrite / TaskCreate / Codex update_plan),
the overview Todo list and folder registration without writing files."""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store as store_mod  # noqa: E402
from overview import build_overview, project_live  # noqa: E402
from sessions import _new_entry, codex_plan, ingest_codex_line, ingest_line, todo_of  # noqa: E402
from test_sessions import NOW, assistant, iso, make_index, user, write_log  # noqa: E402


def test_todo_of_counts_and_current_item():
    t = todo_of([("completed", "a"), ("in_progress", "b"), ("pending", "c"), ("deleted", "x")])
    assert (t["done"], t["total"], t["cur"]) == (1, 3, "b")
    assert [i["s"] for i in t["items"]] == ["completed", "in_progress", "pending"]
    assert todo_of([("completed", "a")])["cur"] == ""
    assert todo_of([]) is None


def test_claude_todowrite_and_task_tools():
    e = _new_entry("s", False)
    todos = [{"content": "读代码", "activeForm": "在读代码", "status": "completed"},
             {"content": "改接口", "activeForm": "在改接口", "status": "in_progress"},
             {"content": "跑测试", "activeForm": "在跑测试", "status": "pending"}]
    ingest_line(e, assistant(NOW, "m1", tool=("TodoWrite", {"todos": todos})), 0)
    assert (e["todo"]["done"], e["todo"]["total"], e["todo"]["cur"]) == (1, 3, "在改接口")
    assert e["todo_at"] > 0

    e = _new_entry("s", False)
    ingest_line(e, assistant(NOW, "m1", tool=("TaskCreate", {"subject": "A", "activeForm": "做 A"})), 0)
    ingest_line(e, assistant(NOW + 1, "m2", tool=("TaskCreate", {"subject": "B"})), 0)
    ingest_line(e, assistant(NOW + 2, "m3", tool=("TaskUpdate", {"taskId": "1", "status": "completed"})), 0)
    ingest_line(e, assistant(NOW + 3, "m4", tool=("TaskUpdate", {"taskId": "2", "status": "in_progress"})), 0)
    assert (e["todo"]["done"], e["todo"]["total"], e["todo"]["cur"]) == (1, 2, "B")
    assert e["step"] == "整理待办"


def test_finished_list_clears_on_next_request_but_open_list_stays():
    e = _new_entry("s", False)
    done = [{"content": "a", "status": "completed"}]
    ingest_line(e, assistant(NOW, "m1", tool=("TodoWrite", {"todos": done})), 0)
    ingest_line(e, user(NOW + 1, "下一件事"), 0)
    assert e["todo"] is None

    e = _new_entry("s", False)
    open_ = [{"content": "a", "status": "pending"}]
    ingest_line(e, assistant(NOW, "m1", tool=("TodoWrite", {"todos": open_})), 0)
    ingest_line(e, user(NOW + 1, "继续"), 0)
    assert e["todo"]["total"] == 1


def test_codex_update_plan_code_mode_and_json():
    code = ('const p = await tools.update_plan({explanation:"x",plan:[\n'
            '  {step:"读代码 \\"A\\"","status":"completed"},\n'
            '  {step:"改代码","status":"in_progress"},\n  {step:"测试","status":"pending"}]});'
            'text(await tools.exec_command({cmd:"ls"}))')
    t = codex_plan(code)
    assert (t["done"], t["total"], t["cur"]) == (1, 3, "改代码")
    assert t["items"][0]["t"] == '读代码 "A"'
    t = codex_plan('{"plan":[{"step":"a","status":"completed"},{"status":"pending","step":"b"}]}')
    assert (t["done"], t["total"], t["cur"]) == (1, 2, "b")

    e = _new_entry("c", False, "codex")
    ingest_codex_line(e, {"type": "response_item", "timestamp": iso(NOW), "payload": {
        "type": "custom_tool_call", "name": "exec", "input": code}}, 0)
    assert e["todo"]["total"] == 3 and e["step"] == "整理待办"


def test_task_progress_only_from_this_run(tmp_path):
    root = tmp_path / "projects"
    t = NOW - 60 * 1000
    todos = [{"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"}]
    write_log(root, "s1", [user(t, "go", cwd="/Users/x/ten"),
                           assistant(t + 1000, "m1", tool=("TodoWrite", {"todos": todos}),
                                     cwd="/Users/x/ten")])
    idx = make_index(tmp_path)
    idx.scan()
    started = datetime.fromtimestamp((t + 500) / 1000).isoformat()
    later = datetime.fromtimestamp((t + 5000) / 1000).isoformat()

    def project(started_at):
        return [{"id": 9, "name": "ten", "path": "/Users/x/ten", "tasks": [
            {"id": 1, "status": "in_progress", "session_id": "s1", "description": "x",
             "created_at": None, "started_at": started_at, "completed_at": None}]}]

    live = project_live(project(started), idx.sessions(), 9, set(), NOW)
    assert (live["tasks"]["s1"]["todo"]["done"], live["tasks"]["s1"]["todo"]["total"]) == (1, 2)
    # a resumed session whose list predates this task shows no stale progress
    assert project_live(project(later), idx.sessions(), 9, set(), NOW)["tasks"]["s1"]["todo"] is None
    card = build_overview(project(started), idx.sessions(), NOW)["now"][0]
    assert card["todo"] == {"done": 1, "total": 2, "cur": "b"}


def test_overview_lists_todo_tasks_newest_first():
    projects = [{"id": 1, "name": "a", "path": "/a", "tasks": [
        {"id": 1, "status": "todo", "description": "old", "agent": "",
         "created_at": "2026-11-01T10:00:00"},
        {"id": 2, "status": "pending", "description": "queued", "agent": "claude",
         "created_at": "2026-11-02T10:00:00"}]},
        {"id": 2, "name": "b", "path": "/b", "tasks": [
            {"id": 5, "status": "todo", "description": "mine", "agent": "me",
             "created_at": "2026-11-03T10:00:00"}]}]
    todos = build_overview(projects, [], NOW)["todos"]
    assert [(x["project_id"], x["task_id"], x["agent"]) for x in todos] == [(2, 5, "me"), (1, 1, "")]


@pytest.fixture
def bare_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "lock")
    return store_mod.ProjectStore()


def test_register_folder_without_writing_into_it(bare_store, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("mine\n")
    p = bare_store.create_project("repo", str(repo) + "/", inject=False)
    assert sorted(os.listdir(repo)) == ["CLAUDE.md"]
    assert (repo / "CLAUDE.md").read_text() == "mine\n"
    # same folder again (with or without trailing slash) returns the same project
    assert bare_store.create_project("repo", str(repo), inject=False)["id"] == p["id"]
    task = bare_store.add_task(p["id"], "do it")
    assert task["status"] == "pending"          # a task you send runs right away
