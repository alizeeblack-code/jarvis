"""Conversation view (transcript), MCP task ↔ session links, sending to a session."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session_send  # noqa: E402
import transcript  # noqa: E402
from overview import link_mcp_sessions  # noqa: E402
from sessions import _new_entry, ingest_line  # noqa: E402
from test_sessions import NOW, assistant, iso, user  # noqa: E402
from transcript import TranscriptReader  # noqa: E402


def tool_result(ms, tid, err):
    return {"type": "user", "timestamp": iso(ms), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": err, "content": "x"}]}}


def write(path, records):
    with open(path, "a") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_claude_rounds_steps_errors_and_reply(tmp_path):
    p = tmp_path / "s.jsonl"
    a1 = assistant(NOW + 1, "m1", tool=("Bash", {"command": "npm test"}))
    a1["message"]["content"][0]["id"] = "t1"
    write(p, [
        user(NOW, "修一下登录"),
        a1, tool_result(NOW + 2, "t1", True),
        assistant(NOW + 3, "m2", stop="end_turn"),
        user(NOW + 4, "<system-reminder>ignore me</system-reminder>"),
        {**user(NOW + 5, "x"), "isMeta": True},
        user(NOW + 6, "再跑一次测试"),
        user(NOW + 7, "[Request interrupted by user]"),
    ])
    r = TranscriptReader()
    d = r.read(str(p), "claude")
    assert d["total"] == 2
    first, second = d["rounds"]
    assert first["ask"] == "修一下登录"
    assert [(i["k"], i.get("err", False)) for i in first["items"]] == [("tool", True), ("text", False)]
    assert first["items"][0]["t"] == "$ npm test"
    assert second["ask"] == "再跑一次测试" and second["interrupted"]
    # incremental: appended lines extend the last round / add new ones
    write(p, [assistant(NOW + 8, "m3", stop="end_turn")])
    assert r.read(str(p), "claude")["rounds"][-1]["items"][-1]["k"] == "text"
    page = r.read(str(p), "claude", before=1, limit=6)
    assert (page["start"], len(page["rounds"])) == (0, 1)


def test_codex_rounds(tmp_path):
    p = tmp_path / "rollout.jsonl"
    ts = iso(NOW)
    write(p, [
        {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "启动服务"}]}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "function_call", "name": "shell",
         "call_id": "c1", "arguments": json.dumps({"command": ["npm", "start"]})}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "function_call_output",
         "call_id": "c1", "output": "Exit code: 1\nboom"}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "启动失败"}]}},
    ])
    d = TranscriptReader().read(str(p), "codex")
    assert d["total"] == 1
    r = d["rounds"][0]
    assert r["ask"] == "启动服务"
    assert [(i["k"], i["t"], i.get("err", False)) for i in r["items"]] == \
        [("tool", "$ npm start", True), ("text", "启动失败", False)]


def test_mcp_task_calls_link_tasks_to_the_session():
    e = _new_entry("s", False)
    ingest_line(e, user(NOW, "go", cwd="/w/app"), 0)
    ingest_line(e, assistant(NOW + 1, "m1", cwd="/w/app/sub", tool=(
        "mcp__task-manager__complete_task", {"task_id": 7, "summary": "ok"})), 0)
    ingest_line(e, assistant(NOW + 2, "m2", cwd="/w/app/sub", tool=(
        "mcp__task-manager__log_progress", {"project_id": 2, "task_id": 3, "message": "x"})), 0)
    assert e["mcp"] == [[0, 7], [2, 3]] and e["cwd0"] == "/w/app"
    s = {"sid": "s", "mcp": e["mcp"], "origin": "/w/app", "cwd": "/w/app/sub", "last_at": NOW}
    projects = [{"id": 1, "name": "app", "path": "/w/app", "tasks": [
                    {"id": 7, "status": "done"}, {"id": 8, "status": "done", "session_id": "own"}]},
                {"id": 2, "name": "b", "path": "/w/b", "tasks": [{"id": 3, "status": "done"}]}]
    link_mcp_sessions(projects, [s])
    assert projects[0]["tasks"][0]["session_id"] == "s"          # project resolved from cwd
    assert projects[0]["tasks"][0]["session_linked"] == "mcp"
    assert projects[0]["tasks"][1]["session_id"] == "own"        # a worker's own id wins
    assert projects[1]["tasks"][0]["session_id"] == "s"


def test_build_cmd_per_executor():
    cmd, stdin = session_send.build_cmd("claude", "sid1", "/w", "hi")
    assert cmd[:1] == ["claude"] and cmd[-2:] == ["--resume", "sid1"] and stdin is None
    cmd, stdin = session_send.build_cmd("codex", "sid2", "/w", "hi")
    assert cmd[-3:] == ["resume", "sid2", "-"] and "-C" in cmd and stdin == "hi"
    assert "workspace-write" in cmd
    cmd, _ = session_send.build_cmd("antigravity", "c3", "/w", "hi", skip_perms=True)
    assert cmd[-2:] == ["--conversation", "c3"] and "--sandbox" not in cmd


def test_sender_runs_once_at_a_time_and_reports(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text("import sys,time; sys.stdin.read(); time.sleep(0.3); print('{}')\n")
    monkeypatch.setattr(session_send, "build_cmd",
                        lambda agent, sid, cwd, text, skip=False, *a: ([sys.executable, str(script)], text))
    done = []
    s = session_send.SessionSender(on_done=done.append)
    assert s.send("x", "codex", str(tmp_path), "hello")["state"] == "running"
    try:
        s.send("x", "codex", str(tmp_path), "again")
        raise AssertionError("second send should be refused")
    except ValueError:
        pass
    for _ in range(50):
        if done:
            break
        time.sleep(0.05)
    assert done == ["x"] and s.status("x")["state"] == "done" and not s.busy("x")
    try:
        s.send("y", "codex", str(tmp_path / "missing"), "hi")
        raise AssertionError("missing folder should be refused")
    except ValueError as e:
        assert "不存在" in str(e)


def test_task_rounds_located_by_mcp_report(tmp_path):
    p = tmp_path / "s.jsonl"
    write(p, [user(NOW, "做任务 5"),
              assistant(NOW + 1, "m1", tool=("mcp__task-manager__complete_task", {"task_id": 5})),
              user(NOW + 2, "别的事 1"), user(NOW + 3, "别的事 2")])
    d = TranscriptReader().read(str(p), "claude", limit=4, task_id=5)
    assert [r["ask"] for r in d["rounds"]] == ["做任务 5"]
    assert d["rounds"][0]["items"][0]["task"] == 5


def test_rounds_carry_model_and_effort(tmp_path):
    p = tmp_path / "s.jsonl"
    a1 = assistant(NOW + 1, "m1", stop="end_turn")
    a1["message"]["model"], a1["effort"] = "claude-opus-5-5", "high"
    a2 = assistant(NOW + 3, "m2", stop="end_turn")
    a2["message"]["model"], a2["effort"] = "claude-haiku-5-5", "low"
    stub = assistant(NOW + 4, "m3", stop="end_turn")
    stub["message"]["model"] = "<synthetic>"
    write(p, [user(NOW, "一"), a1, user(NOW + 2, "二"), a2, stub])
    d = TranscriptReader().read(str(p), "claude")
    assert [(r["model"], r["effort"]) for r in d["rounds"]] == \
        [("claude-opus-5-5", "high"), ("claude-haiku-5-5", "low")]
    assert (d["model"], d["effort"]) == ("claude-haiku-5-5", "low")
    page = TranscriptReader().read(str(p), "claude", before=2, limit=1)
    assert (page["prev_model"], page["prev_effort"]) == ("claude-opus-5-5", "high")
    assert transcript.latest_settings(str(p), "claude") == ("claude-haiku-5-5", "low")


def test_codex_turn_context_settings(tmp_path):
    p = tmp_path / "rollout.jsonl"
    ts = iso(NOW)
    write(p, [
        {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "跑一下"}]}},
        {"type": "turn_context", "timestamp": ts, "payload": {"model": "gpt-6.1-sol", "effort": "high"}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "好了"}]}},
    ])
    d = TranscriptReader().read(str(p), "codex")
    assert (d["rounds"][0]["model"], d["rounds"][0]["effort"]) == ("gpt-6.1-sol", "high")
    assert transcript.latest_settings(str(p), "codex") == ("gpt-6.1-sol", "high")


def test_resume_keeps_effort_and_model():
    cmd, _ = session_send.build_cmd("claude", "sid1", "/w", "hi", model="claude-opus-5-5", effort="xhigh")
    assert cmd[-4:] == ["--effort", "xhigh", "--resume", "sid1"] and "--model" not in cmd
    cmd, _ = session_send.build_cmd("claude", "sid1", "/w", "hi", effort="weird")
    assert cmd[-4:] == ["--effort", "high", "--resume", "sid1"]        # unknown -> high
    cmd, _ = session_send.build_cmd("claude", "sid1", "/w", "hi", effort="medium")
    assert cmd[-4:] == ["--effort", "high", "--resume", "sid1"]        # never below high
    cmd, _ = session_send.build_cmd("codex", "sid2", "/w", "hi", model="gpt-6.1-sol", effort="high")
    assert cmd[-7:] == ["resume", "-m", "gpt-6.1-sol", "-c", "model_reasoning_effort=high", "sid2", "-"]
    assert transcript.resume_args("antigravity", "gemini-4-hydrogen-medium", "") == ["--model", "gemini-4-hydrogen-medium"]
    assert transcript.resume_args("antigravity", "", "high") == []


def test_agy_events_saved_and_read_as_rounds(tmp_path):
    ev = [
        {"event": "init", "conversation_id": "c-1", "init": {"cwd": "/w", "tools": ["x"] * 50}},
        {"event": "step_update", "step_update": {"conversation_id": "c-1", "step_index": 0,
                                                 "state": "DONE", "step_type": "user_input"}},
        {"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE", "step_type": "tool",
                                                 "tool_name": "run_command",
                                                 "tool_info": {"parameters": {"CommandLine": "npm test"}}}},
        {"event": "step_update", "step_update": {"step_index": 2, "state": "ERROR", "step_type": "tool"}},
        {"event": "step_update", "step_update": {"step_index": 3, "state": "ACTIVE",
                                                 "step_type": "agent_response", "text_delta": "测试"}},
        {"event": "step_update", "step_update": {"step_index": 3, "state": "DONE",
                                                 "step_type": "agent_response", "text_delta": "失败了\n"}},
        {"event": "result", "result": {"conversation_id": "c-1", "status": "SUCCESS", "response": "测试失败了"}},
    ]
    log = transcript.AgyLog("跑一下测试", log_dir=str(tmp_path))
    assert log.feed("not json") is None
    for e in ev:
        log.feed(json.dumps(e))
    assert log.conversation == "c-1"
    path = tmp_path / "c-1.jsonl"
    assert "tools" not in path.read_text()                       # the long tool list isn't kept
    transcript.AgyLog("再跑一次", "c-1", log_dir=str(tmp_path))   # a resumed run appends its ask
    d = TranscriptReader().read(str(path), "antigravity")
    assert [r["ask"] for r in d["rounds"]] == ["跑一下测试", "再跑一次"]
    assert [(i["k"], i["t"], i.get("err", False)) for i in d["rounds"][0]["items"]] == \
        [("tool", "$ npm test", True), ("text", "测试失败了", False)]
    cmd, _ = session_send.build_cmd("antigravity", "c-1", "/w", "hi")
    assert "stream-json" in cmd


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def test_images_pasted_and_returned_by_tools(tmp_path):
    p = tmp_path / "s.jsonl"
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}
    a1 = assistant(NOW + 1, "m1", tool=("mcp__Claude_Browser__computer", {"action": "screenshot"}))
    a1["message"]["content"][0]["id"] = "t1"
    write(p, [
        {"type": "user", "timestamp": iso(NOW), "message": {"role": "user", "content": [
            img, {"type": "text", "text": "看这张图 [Image: source: /private/tmp/x/images/1.jpg]"}]}},
        a1,
        {"type": "user", "timestamp": iso(NOW + 2), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}, img]}]}},
    ])
    r = TranscriptReader().read(str(p), "claude")["rounds"][0]
    assert r["ask"] == "看这张图" and r["images"] == 1
    step = r["items"][0]
    assert step["imgs"] == [{"o": step["imgs"][0]["o"], "i": 0, "j": 1}]
    mime, data = transcript.image_at(str(p), "claude", **{"off": r["imgs"][0]["o"], "i": 0})
    assert mime == "image/png" and data.startswith(b"\x89PNG")
    assert transcript.image_at(str(p), "claude", step["imgs"][0]["o"], 0, 1)[0] == "image/png"
    assert transcript.image_at(str(p), "claude", 0, 1) is None          # a text block isn't an image
    assert transcript.image_at(str(p), "claude", 10 ** 9, 0) is None    # past the end of the log


def test_codex_images(tmp_path):
    p = tmp_path / "rollout.jsonl"
    url = f"data:image/png;base64,{PNG}"
    ts = iso(NOW)
    write(p, [
        {"type": "response_item", "timestamp": ts, "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "看图"}, {"type": "input_image", "image_url": url}]}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "custom_tool_call", "name": "exec",
         "call_id": "c1", "input": "view"}},
        {"type": "response_item", "timestamp": ts, "payload": {"type": "custom_tool_call_output", "call_id": "c1",
         "output": [{"type": "input_text", "text": "done"}, {"type": "input_image", "image_url": url}]}},
    ])
    r = TranscriptReader().read(str(p), "codex")["rounds"][0]
    assert r["imgs"][0]["i"] == 1
    ref = r["items"][0]["imgs"][0]
    assert transcript.image_at(str(p), "codex", ref["o"], None, ref["j"])[0] == "image/png"
    assert transcript.image_at(str(p), "codex", r["imgs"][0]["o"], r["imgs"][0]["i"])[0] == "image/png"


def test_sender_queue_and_unqueue(tmp_path):
    s = session_send.SessionSender()
    cwd = str(tmp_path)
    q1 = s.enqueue("sid1", "claude", cwd, "first queued", model="claude-opus-5-5", effort="high")
    q2 = s.enqueue("sid1", "claude", cwd, "second queued")
    assert [x["text"] for x in s.queue("sid1")] == ["first queued", "second queued"]
    assert s.queued_sids() == ["sid1"]
    removed = s.unqueue("sid1", q1["id"])
    assert removed and removed["text"] == "first queued"
    assert [x["text"] for x in s.queue("sid1")] == ["second queued"]
    popped = s.unqueue("sid1")
    assert popped and popped["id"] == q2["id"]
    assert s.queue("sid1") == [] and s.queued_sids() == []


def test_session_in_flight_round_and_direct_queue(tmp_path, monkeypatch):
    import asyncio
    import server
    import store as store_mod

    p = tmp_path / "s-direct.jsonl"
    write(p, [user(NOW, "第一轮"), assistant(NOW + 1, "m1", stop="end_turn")])

    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    monkeypatch.setattr(server, "store", store_mod.ProjectStore())
    monkeypatch.setattr(server, "transcripts", TranscriptReader())
    monkeypatch.setattr(server, "sender", session_send.SessionSender())
    monkeypatch.setattr(
        server.session_index, "sessions",
        lambda: [{"sid": "s-direct", "agent": "claude", "cwd": str(tmp_path), "path": str(p),
                  "title": "Direct Session", "last_at": NOW + 1000, "kind": "end", "turns": 1}],
    )

    started = []

    def fake_start_next(sid, rounds_before=None, model="", effort=""):
        q = server.sender.queues.get(sid) or []
        if not q or server.sender.busy(sid):
            return None
        item = q.pop(0)
        run = {
            "agent": item["agent"], "text": item["text"], "state": "running",
            "started_at": NOW + 2000, "rounds_before": rounds_before,
        }
        server.sender.runs[sid] = run
        started.append(item["text"])
        return item, server.sender.status(sid)

    def fake_send(sid, agent, cwd, text, skip_perms=False, model="", effort="", rounds_before=None):
        run = {
            "agent": agent, "text": text, "state": "running",
            "started_at": NOW + 2000, "rounds_before": rounds_before,
        }
        server.sender.runs[sid] = run
        started.append(text)
        return server.sender.status(sid)

    monkeypatch.setattr(server.sender, "send", fake_send)
    monkeypatch.setattr(server.sender, "start_next", fake_start_next)

    async def go():
        # 1. Send first instruction while idle -> starts immediately
        r1 = await server.send_to_session("s-direct", {"text": "第二轮指令"})
        assert r1["mode"] == "direct" and r1["queued"] is False
        # Before the CLI writes to .jsonl, get_session synthesizes the in-flight round
        d1 = await server.get_session("s-direct")
        assert d1["session"]["state"] == "run"
        assert [r["ask"] for r in d1["conversation"]["rounds"]] == ["第一轮", "第二轮指令"]

        # 2. Send two more instructions while running -> both enter queue
        r2 = await server.send_to_session("s-direct", {"text": "排队指令 A", "queue": True})
        r3 = await server.send_to_session("s-direct", {"text": "排队指令 B", "queue": True})
        assert r2["queued"] is True and r3["queued"] is True
        d2 = await server.get_session("s-direct")
        assert [q["text"] for q in d2["send"]["queue"]] == ["排队指令 A", "排队指令 B"]

        # 3. Withdraw first queued instruction -> only B remains
        uq = await server.unqueue_session_send("s-direct", {"id": r2["item"]["id"]})
        assert uq["ok"] and uq["text"] == "排队指令 A"
        d3 = await server.get_session("s-direct")
        assert [q["text"] for q in d3["send"]["queue"]] == ["排队指令 B"]

        # 4. Once .jsonl records the second round, get_session does not duplicate it
        write(p, [
            user(NOW + 2, "第二轮指令"), assistant(NOW + 3, "m2", stop="end_turn"),
        ])
        d4 = await server.get_session("s-direct")
        assert [r["ask"] for r in d4["conversation"]["rounds"]] == ["第一轮", "第二轮指令"]

        # 5. When the first run finishes, _drain_direct_queue automatically starts B
        server.sender.runs["s-direct"]["state"] = "done"
        await server._drain_direct_queue("s-direct")
        assert started == ["第二轮指令", "排队指令 B"]
        d5 = await server.get_session("s-direct")
        assert d5["send"]["queue"] == []
        assert [r["ask"] for r in d5["conversation"]["rounds"]] == ["第一轮", "第二轮指令", "排队指令 B"]

    asyncio.run(go())


def test_session_task_mode_queue_and_unqueue(tmp_path, monkeypatch):
    import asyncio
    import server
    import store as store_mod

    p = tmp_path / "s-task.jsonl"
    write(p, [user(NOW, "初始任务"), assistant(NOW + 1, "m1", stop="")])

    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    st = store_mod.ProjectStore()
    proj = st.create_project("P", str(tmp_path), inject=False)
    t1 = st.add_task(proj["id"], "初始任务", agent="claude")
    st.update_task(proj["id"], t1["id"], status="in_progress", session_id="s-task", line=t1["id"])

    monkeypatch.setattr(server, "store", st)
    monkeypatch.setattr(server, "transcripts", TranscriptReader())
    monkeypatch.setattr(server, "sender", session_send.SessionSender())
    monkeypatch.setattr(server.worker_manager, "wake", lambda: None)
    monkeypatch.setattr(
        server.session_index, "sessions",
        lambda: [{"sid": "s-task", "agent": "claude", "cwd": str(tmp_path), "path": str(p),
                  "title": "Task Session", "last_at": NOW + 1000, "kind": "text", "turns": 1}],
    )

    async def go():
        # "排队" while t1 is in_progress queues follow-up tasks chained by predecessor_id
        r1 = await server.send_to_session("s-task", {"text": "续接指令 1", "queue": True})
        r2 = await server.send_to_session("s-task", {"text": "续接指令 2", "queue": True})
        assert r1["mode"] == "task" and r1["queued"] is True
        assert r2["mode"] == "task" and r2["queued"] is True
        assert r1["task"]["predecessor_id"] == t1["id"]
        assert r2["task"]["predecessor_id"] == r1["task"]["id"]

        d = await server.get_session("s-task")
        assert [q["text"] for q in d["send"]["queue"]] == ["续接指令 1", "续接指令 2"]

        # Unqueueing r1 deletes it and relinks r2's predecessor_id to t1
        uq = await server.unqueue_session_send("s-task", {"id": f"t{r1['task']['id']}"})
        assert uq["ok"] and uq["text"] == "续接指令 1"
        assert st.get_task(proj["id"], r1["task"]["id"]) is None
        assert st.get_task(proj["id"], r2["task"]["id"])["predecessor_id"] == t1["id"]

        d2 = await server.get_session("s-task")
        assert [q["text"] for q in d2["send"]["queue"]] == ["续接指令 2"]

    asyncio.run(go())


def test_sending_while_a_worker_runs_interrupts_it(tmp_path, monkeypatch):
    import asyncio
    import server
    import store as store_mod

    p = tmp_path / "s-run.jsonl"
    write(p, [user(NOW, "初始任务"), assistant(NOW + 1, "m1", stop="")])
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    st = store_mod.ProjectStore()
    proj = st.create_project("P", str(tmp_path), inject=False)
    t1 = st.add_task(proj["id"], "初始任务", agent="claude")
    st.update_task(proj["id"], t1["id"], status="in_progress", session_id="s-run", line=t1["id"])
    stopped, woke = [], []

    async def cancel_task(pid, tid):
        stopped.append(tid)
        st.update_task(pid, tid, status="cancelled")
        return True

    monkeypatch.setattr(server, "store", st)
    monkeypatch.setattr(server, "transcripts", TranscriptReader())
    monkeypatch.setattr(server, "sender", session_send.SessionSender())
    monkeypatch.setattr(server.worker_manager, "cancel_task", cancel_task)
    monkeypatch.setattr(server.worker_manager, "wake", lambda: woke.append(1))
    monkeypatch.setattr(server.session_index, "sessions",
                        lambda: [{"sid": "s-run", "agent": "claude", "cwd": str(tmp_path), "path": str(p),
                                  "title": "Run", "last_at": NOW + 1000, "kind": "run", "turns": 1}])

    async def go():
        d = await server.get_session("s-run")
        assert d["send"]["ours"] is True                      # Jarvis runs it: stop / interrupt offered
        r = await server.send_to_session("s-run", {"text": "改成这样做"})
        assert stopped == [t1["id"]] and r["steered"] is True and r["queued"] is False and woke
        assert r["task"]["predecessor_id"] == t1["id"]          # resumes the same session right away

    asyncio.run(go())



def test_tool_steps_carry_a_category(tmp_path):
    p = tmp_path / "s.jsonl"
    a1 = assistant(NOW + 1, "m1", tool=("Bash", {"command": "npm test"}))
    a2 = assistant(NOW + 2, "m2", tool=("Edit", {"file_path": "/w/a.py"}))
    a3 = assistant(NOW + 3, "m3", tool=("mcp__ccd_turn__wrap_up", {"recap": "x"}))
    a4 = assistant(NOW + 4, "m4", tool=("mcp__Claude_Browser__computer", {"action": "screenshot"}))
    write(p, [user(NOW, "做"), a1, a2, a3, a4])
    items = TranscriptReader().read(str(p), "claude")["rounds"][0]["items"]
    assert [i["c"] for i in items] == ["cmd", "edit", "internal", "web"]
    assert transcript.tool_cat("exec", "$ ls") == "cmd" and transcript.tool_cat("apply_patch", "改 x") == "edit"
    assert transcript.tool_cat("mcp__task-manager__log_progress", "log_progress") == "internal"
    assert transcript.tool_cat("whatever", "读 a.py") == "read"


def test_task_without_session_id_fallback_and_send(tmp_path, monkeypatch):
    import asyncio
    import config
    import server
    import store as store_mod

    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    monkeypatch.setattr(config, "TASK_LOGS_DIR", tmp_path / "task_logs")
    st = store_mod.ProjectStore()
    proj = st.create_project("LegacyProj", str(tmp_path), inject=False)
    t1 = st.add_task(proj["id"], "实现登录接口", agent="claude")
    st.append_log(proj["id"], t1["id"], type="init", content="model=claude-opus-4-6 tools=40")
    st.append_log(proj["id"], t1["id"], type="tool_use", content="Edit: /app/auth.py")
    st.append_log(proj["id"], t1["id"], type="tool_use", content="Bash: pytest -q")
    st.append_log(proj["id"], t1["id"], type="message", content="测试已通过")
    st.update_task(proj["id"], t1["id"], status="done", summary="已完成登录接口并验证通过。")

    t_todo = st.add_task(proj["id"], "待办事项测试", agent="todo")

    monkeypatch.setattr(server, "store", st)
    monkeypatch.setattr(server, "transcripts", TranscriptReader())
    monkeypatch.setattr(server, "sender", session_send.SessionSender())
    monkeypatch.setattr(server.worker_manager, "wake", lambda: None)
    monkeypatch.setattr(server.session_index, "sessions", lambda: [])

    async def go():
        sid = f"task:{proj['id']}:{t1['id']}"
        d = await server.get_session(sid)
        assert d["log"] is True
        assert d["task"]["fallback"] is True
        assert d["session"]["model"] == "claude-opus-4-6"
        rounds = d["conversation"]["rounds"]
        assert len(rounds) == 1
        assert rounds[0]["ask"] == "实现登录接口"
        assert [x["c"] for x in rounds[0]["items"] if x["k"] == "tool"] == ["edit", "cmd"]
        assert rounds[0]["items"][-1] == {"k": "text", "t": "已完成登录接口并验证通过。"}
        assert d["send"]["mode"] == "task"

        # Sending a follow-up to a task without session_id creates a chained task on the same line
        res = await server.send_to_session(sid, {"text": "加上限流保护"})
        assert res["mode"] == "task" and res["task"]["predecessor_id"] == t1["id"]
        assert res["task"].get("session_id") is None

        # Todo task opens cleanly with log=True and empty rounds
        d_todo = await server.get_session(f"task:{proj['id']}:{t_todo['id']}")
        assert d_todo["log"] is True
        assert d_todo["task"]["status"] == "todo"
        assert d_todo["conversation"]["rounds"] == []
        assert d_todo["send"]["mode"] == ""

    asyncio.run(go())



def test_questions_left_for_you_are_kept_for_the_panel():
    from transcript import claude_prompt
    w = claude_prompt("mcp__ccd_turn__wrap_up", {"recap": "x", "ask": "按推荐开工吗？", "blocked": True,
                                               "actions": [{"label": "开工"}, {"action": "commit", "label": "提交"},
                                                           {"label": "先不"}]})
    assert w == {"kind": "wrap", "ask": "按推荐开工吗？", "options": ["开工", "先不"], "blocked": True}
    assert claude_prompt("mcp__ccd_turn__wrap_up", {"recap": "done"}) is None
    c = claude_prompt("AskUserQuestion", {"questions": [{"question": "用哪个库？", "multiSelect": False,
                                                         "options": [{"label": "A", "description": "快"}, {"label": "B"}]}]})
    assert c["kind"] == "choice" and c["questions"][0]["options"][0] == {"label": "A", "desc": "快"}
    assert claude_prompt("ExitPlanMode", {"plan": "## 计划"})["plan"] == "## 计划"
    assert claude_prompt("Bash", {"command": "ls"}) is None


def test_a_turn_waiting_on_background_work_is_still_running():
    import time
    from sessions import _new_entry, background_running, ingest_line, session_state
    now = int(time.time() * 1000)
    iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + "Z"
    e = _new_entry("s", False)
    ingest_line(e, {"type": "user", "timestamp": iso(now - 60_000), "message": {"content": "重新生成预览"}}, 0)
    ingest_line(e, {"type": "assistant", "timestamp": iso(now - 50_000), "message": {
        "id": "m1", "content": [{"type": "tool_use", "id": "toolu_A", "name": "Bash",
                                 "input": {"command": "sleep 600", "run_in_background": True}}]}}, 0)
    ingest_line(e, {"type": "user", "timestamp": iso(now - 49_000), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_A",
         "content": "Command running in background with ID: bcriy92ni. Output is being written to: /tmp/x"}]}}, 0)
    ingest_line(e, {"type": "assistant", "timestamp": iso(now - 40_000), "message": {
        "id": "m2", "stop_reason": "end_turn", "content": [{"type": "text", "text": "等三份预览重新生成完毕。"}]}}, 0)
    s = {**e, "has_main": True, "asks": False}
    assert e["kind"] == "end" and background_running(s, now) == 1
    assert session_state(s, now) == "run"
    ingest_line(e, {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(now - 1000),
                    "content": "<task-notification>\n<task-id>bcriy92ni</task-id>\n<tool-use-id>toolu_A</tool-use-id>\n<status>completed</status>"}, 0)
    s = {**e, "has_main": True, "asks": False}
    assert background_running(s, now) == 0 and session_state(s, now) == "done"
    stale = {**s, "bg": {"toolu_B": [now - 7 * 3600 * 1000, ""]}, "last_at": now - 7 * 3600 * 1000}
    assert session_state(stale, now) != "run"                    # never reported back: gone


def test_stopped_or_read_background_tasks_are_not_waited_on():
    import time
    from sessions import _new_entry, background_running, ingest_line
    now = int(time.time() * 1000)
    iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + "Z"
    e = _new_entry("s", False)
    for i, tid in enumerate(("b1", "b2")):
        ingest_line(e, {"type": "assistant", "timestamp": iso(now - 9000 + i), "message": {
            "id": f"m{i}", "content": [{"type": "tool_use", "id": f"toolu_{tid}", "name": "Bash", "input": {}}]}}, 0)
        ingest_line(e, {"type": "user", "timestamp": iso(now - 8000 + i), "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{tid}",
             "content": f"Command running in background with ID: {tid}. Output is being written to: /tmp/{tid}"}]}}, 0)
    assert background_running(e, now) == 2
    ingest_line(e, {"type": "assistant", "timestamp": iso(now - 5000), "message": {
        "id": "m9", "content": [{"type": "tool_use", "id": "toolu_stop", "name": "TaskStop", "input": {"task_id": "b1"}}]}}, 0)
    ingest_line(e, {"type": "assistant", "timestamp": iso(now - 4000), "message": {
        "id": "m10", "content": [{"type": "tool_use", "id": "toolu_read", "name": "TaskOutput", "input": {"task_id": "b2"}}]}}, 0)
    ingest_line(e, {"type": "user", "timestamp": iso(now - 3000), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_read", "content": "<retrieval_status>success</retrieval_status>\n<status>completed</status>"}]}}, 0)
    assert background_running(e, now) == 0


def test_a_stopped_turn_reads_as_interrupted_not_as_a_reply(tmp_path):
    p = tmp_path / "s.jsonl"
    stub = {"type": "assistant", "timestamp": "2026-11-14T08:58:16.207Z", "message": {
        "id": "x", "model": "<synthetic>", "role": "assistant", "stop_reason": "stop_sequence",
        "content": [{"type": "text", "text": "No response requested."}]}}
    write(p, [user(NOW, "跑 40 秒"), assistant(NOW + 1, "m1", tool=("Bash", {"command": "sleep 40"})), stub,
              user(NOW + 3000, "换成这个"), assistant(NOW + 4000, "m2", "SECOND")])
    rounds = TranscriptReader().read(str(p), "claude")["rounds"]
    assert rounds[0]["interrupted"] and not any(i["k"] == "text" for i in rounds[0]["items"])
    assert rounds[1]["ask"] == "换成这个" and rounds[1]["items"][-1]["k"] == "text" and not rounds[1]["interrupted"]


def test_questions_answered_elsewhere_are_marked_with_the_choice(tmp_path):
    p = tmp_path / "s.jsonl"
    wrap = assistant(NOW + 1, "m1", tool=("mcp__ccd_turn__wrap_up", {"recap": "r", "ask": "开工吗？",
                                                                     "actions": [{"label": "开工"}, {"label": "先不"}]}))
    ask = assistant(NOW + 3000, "m2", tool=("AskUserQuestion", {"questions": [{"question": "用哪个库？",
                                                                              "options": [{"label": "A"}, {"label": "B"}]}]}))
    tid = ask["message"]["content"][-1]["id"] = "toolu_ask1"
    wrap["message"]["content"][-1]["id"] = "toolu_wrap1"
    answered = {"type": "user", "timestamp": "2026-11-14T09:00:00Z", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid,
         "content": 'User has answered your questions: "用哪个库？"="B". You can now continue.'}]}}
    write(p, [user(NOW, "规划一下"), wrap, user(NOW + 2000, "开工"), ask, answered])
    rounds = TranscriptReader().read(str(p), "claude")["rounds"]
    assert rounds[0]["prompt"]["answered"] and rounds[0]["prompt"]["answer"] == "开工"
    assert rounds[1]["prompt"]["answered"] and rounds[1]["prompt"]["answer"] == {"用哪个库？": "B"}


def test_session_image_upload_and_send(tmp_path, monkeypatch):
    import asyncio
    import base64
    import io
    from fastapi import UploadFile
    import server
    import store as store_mod

    uploads_dir = tmp_path / "uploads"
    monkeypatch.setattr(server, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")

    img_bytes = base64.b64decode(PNG)

    async def go():
        # Upload without project (for external / Dashboard sessions)
        bad = UploadFile(filename="x.txt", file=io.BytesIO(b"nope"), headers={"content-type": "text/plain"})
        assert "error" in await server.upload_session_image(bad)

        good = UploadFile(filename="shot.png", file=io.BytesIO(img_bytes), headers={"content-type": "image/png"})
        up = await server.upload_session_image(good)
        assert up["url"].startswith("/api/uploads/s/")
        assert os.path.isfile(up["path"])

        # _clean_images rejects paths outside UPLOADS_DIR and accepts valid uploads
        outside = tmp_path / "secret.png"
        outside.write_bytes(img_bytes)
        assert server._clean_images([{"url": "/api/uploads/s/nope.png", "path": str(outside)}]) == []
        cleaned = server._clean_images([{"url": up["url"], "path": up["path"]}])
        assert cleaned == [up]

        # build_cmd with images for codex & antigravity
        cmd_cx, stdin_cx = session_send.build_cmd("codex", "sid2", str(tmp_path), "", images=cleaned)
        assert "-i" in cmd_cx and up["path"] in cmd_cx and stdin_cx == "请查看附带的图片"
        cmd_ag, _ = session_send.build_cmd("antigravity", "c3", str(tmp_path), "看图", images=cleaned)
        assert "--add-dir" in cmd_ag and str(uploads_dir / "sessions") in cmd_ag
        assert "[Attached Images]" in cmd_ag[2]

        # _claude_protocol sends base64 image block + text block
        buf = io.BytesIO()
        class FakeProc:
            stdin = buf
            stdout = [json.dumps({"type": "result", "is_error": False}).encode() + b"\n"]
        # Prevent buf.close() from discarding contents
        buf.close = lambda: None
        err = session_send.SessionSender._claude_protocol(FakeProc(), "看看", cleaned)
        assert err == ""
        lines = [json.loads(x) for x in buf.getvalue().decode().splitlines()]
        user_msg = lines[1]["message"]["content"]
        assert user_msg[0]["type"] == "image" and user_msg[0]["source"]["media_type"] == "image/png"
        assert user_msg[1] == {"type": "text", "text": "看看"}

        # AgyLog records image_urls and TranscriptReader reads them back
        log = transcript.AgyLog("看这张图", "agy-img-1", log_dir=str(tmp_path), images=cleaned)
        assert log.conversation == "agy-img-1"
        r = TranscriptReader().read(str(tmp_path / "agy-img-1.jsonl"), "antigravity")["rounds"][0]
        assert r["image_urls"] == [up["url"]] and r["images"] == 1

        # send_to_session with image-only message in direct mode + queue/unqueue
        s_file = tmp_path / "s-img.jsonl"
        write(s_file, [user(NOW - 5000, "hi"), assistant(NOW - 4000, "m1", stop="end_turn")])
        st = store_mod.ProjectStore()
        st.create_project("app", str(tmp_path))
        monkeypatch.setattr(server, "store", st)
        monkeypatch.setattr(server, "session_index", type("Idx", (), {
            "sessions": lambda self: [{
                "sid": "s-img", "agent": "claude", "title": "hi", "path": str(s_file),
                "origin": str(tmp_path), "cwd": str(tmp_path), "kind": "end", "last_at": NOW - 4000,
                "archived": False, "turns": 1, "ctx": 0, "ctx_max": 0,
            }],
        })())
        monkeypatch.setattr(server, "sender", session_send.SessionSender())
        monkeypatch.setattr(server.sender, "send", lambda sid, agent, cwd, text, skip=False, model="", effort="", rounds_before=None, images=None: {
            "state": "running", "started_at": NOW, "ended_at": 0, "error": "", "text": text, "images": list(images or [])
        })
        monkeypatch.setattr(server.sender, "busy", lambda sid: True)
        monkeypatch.setattr(server, "ws_manager", type("WS", (), {"broadcast": staticmethod(lambda *a, **k: asyncio.sleep(0))})())

        r1 = await server.send_to_session("s-img", {"text": "", "images": cleaned})
        assert r1["mode"] == "direct" and r1["queued"] is True and r1["item"]["images"] == [up["url"]]
        uq = await server.unqueue_session_send("s-img", {"id": r1["item"]["id"]})
        assert uq["ok"] is True and uq["images"] == cleaned

    asyncio.run(go())
