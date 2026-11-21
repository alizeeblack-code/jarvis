import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from overview import ProjectResolver, build_overview, session_page  # noqa: E402
from sessions import (LIVE_STALE_MS, SessionIndex, session_state,  # noqa: E402
                      union_ms)

NOW = int(time.time() * 1000)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def user(ms, text, cwd="/w/app"):
    return {"type": "user", "timestamp": iso(ms), "cwd": cwd, "sessionId": "s1",
            "message": {"role": "user", "content": text}}


def assistant(ms, mid, stop="tool_use", out=10, tool=None, cwd="/w/app"):
    content = [{"type": "tool_use", "name": tool[0], "input": tool[1]}] if tool else \
        [{"type": "text", "text": "done"}]
    return {"type": "assistant", "timestamp": iso(ms), "cwd": cwd, "sessionId": "s1",
            "message": {"id": mid, "model": "claude-opus-5-5", "stop_reason": stop,
                        "content": content,
                        "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2,
                                  "cache_read_input_tokens": 100, "output_tokens": out}}}


def write_log(root, sid, records, sub=False):
    d = root / "-w-app"
    path = d / sid / "subagents" / "agent-x.jsonl" if sub else d / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def make_index(tmp_path):
    return SessionIndex(tmp_path / "idx.json", tmp_path / "projects", tmp_path / "desktop",
                        tmp_path / "codex", [tmp_path / "agy"])


def test_tokens_counted_once_per_message_and_subagents_added(tmp_path):
    root = tmp_path / "projects"
    t = NOW - 10 * 60 * 1000
    write_log(root, "s1", [
        user(t, "fix the bug"),
        assistant(t + 1000, "m1", out=50, tool=("Read", {"file_path": "/w/app/a.py"})),
        assistant(t + 1001, "m1", out=50, tool=("Read", {"file_path": "/w/app/a.py"})),
        assistant(t + 5000, "m2", stop="end_turn", out=7),
    ])
    write_log(root, "s1", [assistant(t + 2000, "sub1", out=3)], sub=True)
    idx = make_index(tmp_path)
    assert idx.scan()
    [s] = idx.sessions()
    assert sum(d["out"] for d in s["days"].values()) == 50 + 7 + 3
    assert sum(d["prompts"] for d in s["days"].values()) == 1
    assert s["title"] == "fix the bug"
    assert s["kind"] == "end"


def test_incremental_scan_reads_only_appended_lines(tmp_path):
    root = tmp_path / "projects"
    t = NOW - 60 * 1000
    path = write_log(root, "s1", [user(t, "hello")])
    idx = make_index(tmp_path)
    idx.scan()
    assert idx.scan() is False          # nothing new
    with open(path, "a") as f:
        f.write(json.dumps(assistant(t + 1000, "m1", out=5)))   # no newline yet
    idx.scan()
    assert sum(d["out"] for d in idx.sessions()[0]["days"].values()) == 0
    with open(path, "a") as f:
        f.write("\n")
    assert idx.scan() is True
    assert sum(d["out"] for d in idx.sessions()[0]["days"].values()) == 5


def test_index_survives_restart(tmp_path):
    write_log(tmp_path / "projects", "s1", [user(NOW - 5000, "hi")])
    make_index(tmp_path).scan()
    again = make_index(tmp_path)
    assert again.scan() is False
    assert again.sessions()[0]["prompt"] == "hi"


def test_segments_merge_within_gap_and_split_beyond():
    from sessions import _add_seg
    segs = []
    for ts in (0, 60_000, 200_000, 2_000_000):
        _add_seg(segs, ts)
    assert segs == [[0, 200_000], [2_000_000, 2_000_000]]
    assert union_ms([[0, 10], [5, 20], [30, 40]]) == 30


def test_session_states():
    base = {"archived": False, "awaiting": False}
    assert session_state({**base, "kind": "run", "last_at": NOW - 1000}, NOW) == "run"
    assert session_state({**base, "kind": "run", "last_at": NOW - LIVE_STALE_MS - 1}, NOW) == "quiet"
    assert session_state({**base, "kind": "end", "last_at": NOW - 60_000}, NOW) == "done"
    assert session_state({**base, "kind": "end", "last_at": NOW - 60_000, "asks": True}, NOW) == "wait"
    # a pending AskUserQuestion leaves the turn "running" in the log but waits on you
    assert session_state({**base, "kind": "run", "last_at": NOW - 600_000, "asks": True}, NOW) == "wait"
    assert session_state({**base, "kind": "end", "last_at": NOW - 5 * 3600_000}, NOW) == "idle"
    assert session_state({**base, "kind": "error", "last_at": NOW - 1000}, NOW) == "run"
    assert session_state({**base, "kind": "error", "last_at": NOW - 600_000}, NOW) == "error"
    assert session_state({**base, "archived": True, "kind": "run", "last_at": NOW}, NOW) == "archived"


def test_resolver_maps_worktrees_and_scratch(tmp_path, monkeypatch):
    import overview
    # a prefix tmp_path never has, wherever the temp dir is (/var/folders, /tmp, /private/tmp…)
    monkeypatch.setattr(overview, "_SCRATCH", ("/scratch-for-test/",))
    r = ProjectResolver([{"id": 4, "name": "demo", "path": "/Users/x/code/demo"}])
    assert r.resolve("/Users/x/code/demo/frontend")["project_id"] == 4
    assert r.resolve("/Users/x/code/demo/.claude/worktrees/brave-1/src")["project_id"] == 4
    assert r.resolve("/scratch-for-test/claude-501/foo")["key"] == "other"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "sub").mkdir()
    got = r.resolve(str(repo / "sub"))
    assert got["key"] == "d:" + str(repo) and got["project_id"] is None


def test_overview_links_worker_task_and_counts_today(tmp_path):
    root = tmp_path / "projects"
    t = NOW - 2 * 60 * 1000
    write_log(root, "s1", [user(t, "do it", cwd="/elsewhere"),
                           assistant(t + 1000, "m1", out=40)])
    idx = make_index(tmp_path)
    idx.scan()
    projects = [{"id": 9, "name": "ten", "path": "/Users/x/ten", "tasks": [
        {"id": 3, "status": "in_progress", "session_id": "s1", "description": "x",
         "created_at": None, "started_at": None, "completed_at": None}]}]
    ov = build_overview(projects, idx.sessions(), NOW)
    [card] = [c for c in ov["now"] if c["sid"] == "s1"]
    assert card["project"] == "p9" and card["task"]["task_id"] == 3
    assert ov["today"]["out_tokens"] == 40 and ov["today"]["sessions"] == 1
    [p] = [p for p in ov["projects"] if p["key"] == "p9"]
    assert p["today_out"] == 40 and p["spark"][-1] >= 60_000


def test_project_live_reports_task_state_and_resumable(tmp_path):
    from overview import existing_session_ids, project_live
    root = tmp_path / "projects"
    t = NOW - 30 * 1000
    write_log(root, "s1", [user(t, "go", cwd="/Users/x/ten"),
                           assistant(t + 1000, "m1", out=12,
                                     tool=("Edit", {"file_path": "/Users/x/ten/a.swift"}))])
    idx = make_index(tmp_path)
    idx.scan()
    projects = [{"id": 9, "name": "ten", "path": "/Users/x/ten", "tasks": [
        {"id": 1, "status": "in_progress", "session_id": "s1", "description": "x",
         "created_at": None, "started_at": None, "completed_at": None},
        {"id": 2, "status": "done", "session_id": "gone", "description": "y",
         "created_at": None, "started_at": None, "completed_at": None}]}]
    live = project_live(projects, idx.sessions(), 9, existing_session_ids(root), NOW)
    assert live["tasks"]["s1"]["state"] == "run"
    assert live["tasks"]["s1"]["step"] == "改 a.swift"
    assert live["resumable"] == ["s1"]          # "gone" has no log on disk
    assert [c["sid"] for c in live["now"]] == ["s1"]


def test_codex_rollout_and_antigravity_summaries(tmp_path):
    import sqlite3
    t = NOW - 5 * 60 * 1000
    sid = "01a1d052-80f8-7460-9c7f-f3dab0be052d"
    day_dir = tmp_path / "codex" / "sessions" / "2026" / "11" / "12"
    day_dir.mkdir(parents=True)
    tok = lambda out, inp: {"type": "event_msg", "timestamp": iso(t + 3000),
                            "payload": {"type": "token_count", "info": {"total_token_usage": {
                                "input_tokens": inp, "cached_input_tokens": 0, "output_tokens": out}}}}
    recs = [
        {"type": "session_meta", "timestamp": iso(t), "payload": {"session_id": sid, "cwd": "/w/app",
                                                                  "originator": "codex_exec"}},
        {"type": "turn_context", "timestamp": iso(t), "payload": {"model": "gpt-5.5", "cwd": "/w/app"}},
        {"type": "event_msg", "timestamp": iso(t + 1000), "payload": {"type": "task_started"}},
        {"type": "response_item", "timestamp": iso(t + 1000), "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
        {"type": "response_item", "timestamp": iso(t + 1100), "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "add a test"}]}},
        {"type": "response_item", "timestamp": iso(t + 2000), "payload": {
            "type": "function_call", "name": "exec_command", "arguments": '{"cmd": "pytest -q"}'}},
        tok(40, 100), tok(90, 300),            # cumulative totals → 90 out in total
        {"type": "event_msg", "timestamp": iso(t + 4000), "payload": {
            "type": "task_complete", "last_agent_message": "added"}},
    ]
    (day_dir / f"rollout-2026-11-12T08-13-53-{sid}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in recs))
    (tmp_path / "codex" / "session_index.jsonl").write_text(
        json.dumps({"id": sid, "thread_name": "补测试"}) + "\n")
    (tmp_path / "agy").mkdir(exist_ok=True)
    con = sqlite3.connect(tmp_path / "agy" / "conversation_summaries.db")
    con.execute("create table conversation_summaries (conversation_id text, title text, preview text, "
                "workspace_uris text, status text, last_modified_time datetime, step_count integer)")
    con.execute("insert into conversation_summaries values (?,?,?,?,?,?,?)",
                ("conv-1", "重构", "p", '["file:///w/app"]', "CASCADE_RUN_STATUS_IDLE",
                 datetime.fromtimestamp((NOW - 60_000) / 1000, tz=timezone.utc).isoformat(sep=" "), 12))
    con.commit()
    con.close()
    idx = make_index(tmp_path)
    idx.scan()
    by = {s["sid"]: s for s in idx.sessions()}
    cx = by[sid]
    assert cx["agent"] == "codex" and cx["title"] == "补测试" and cx["kind"] == "end"
    assert sum(d["out"] for d in cx["days"].values()) == 90
    assert sum(d["prompts"] for d in cx["days"].values()) == 1
    assert cx["step"] == "$ pytest -q" and cx["model"] == "gpt-5.5"
    ag = by["conv-1"]
    assert ag["agent"] == "antigravity" and ag["origin"] == "/w/app" and ag["segs"] == []
    ov = build_overview([{"id": 1, "name": "app", "path": "/w/app", "tasks": []}], idx.sessions(), NOW)
    assert ov["today"]["by_agent"]["codex"]["out"] == 90
    page = session_page([{"id": 1, "name": "app", "path": "/w/app", "tasks": []}], idx.sessions(), now_ms=NOW)
    # the Codex session is a `codex exec` run: scripted, so not listed under "all"
    assert {c["agent"] for c in page["rows"]} == {"antigravity"}
    assert ov["all_count"] == page["total"] == 1 and "all" not in ov
    assert session_page([{"id": 1, "name": "app", "path": "/w/app", "tasks": []}], idx.sessions(),
                        agent="codex", now_ms=NOW)["total"] == 0


def test_codex_code_mode_steps_and_headless_state():
    from sessions import _codex_step
    assert _codex_step({"name": "exec", "input": 'text(await tools.exec_command({cmd:"git status --short","max_output_tokens":3000}));'}) == "$ git status --short"
    assert _codex_step({"name": "exec", "input": 'text(await tools.apply_patch("*** Begin Patch\\n*** Update File: /w/app/main.py\\n@@"));'}) == "改 main.py"
    assert _codex_step({"name": "exec_command", "arguments": '{"cmd": "ls"}'}) == "$ ls"
    base = {"archived": False, "awaiting": False, "kind": "end", "last_at": NOW - 60_000}
    assert session_state({**base, "entry": "codex_exec"}, NOW) == "idle"
    assert session_state({**base, "entry": "codex_tui"}, NOW) == "done"
