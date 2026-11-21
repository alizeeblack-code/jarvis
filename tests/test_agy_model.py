import json
import os

import session_send
import usage
from transcript import AgyLog, TranscriptReader, latest_settings, resume_args


def _tracker(tmp_path, windows):
    t = usage.UsageTracker(path=tmp_path / "usage.json", codex_home=tmp_path)
    t.data = {"antigravity": {"windows": windows, "at": 1}}
    return t


def test_default_is_hydrogen_and_argon_only_when_asked(tmp_path):
    t = _tracker(tmp_path, {})
    assert t.agy_models("") == ["gemini-4-hydrogen-high", "claude-sonnet-5-5-high"]
    assert t.agy_models("argon")[0] == "gemini-4-argon-high"
    assert t.agy_models("gemini-4-argon-high") == ["gemini-4-argon-high", "gemini-4-hydrogen-high",
                                                   "claude-sonnet-5-5-high"]
    assert t.agy_models("gemini-4-hydrogen-medium")[0] == "gemini-4-hydrogen-high"   # never below high
    assert t.agy_models("gemini-4-hydrogen-xhigh")[0] == "gemini-4-hydrogen-xhigh"
    assert t.agy_models("gpt-oss-120b-medium")[0] == "gpt-oss-120b-medium"           # has no high variant
    assert usage.agy_model_id("bad model; rm") == usage.AGY_DEFAULT


def test_used_up_group_goes_to_the_back(tmp_path):
    t = _tracker(tmp_path, {
        "gemini-weekly": {"group": "Gemini", "used": 100, "status": "rejected"},
        "gemini-argon-wtu-weekly": {"group": "Argon", "used": 40, "status": "allowed"}})
    assert t.agy_models("") == ["claude-sonnet-5-5-high", "gemini-4-hydrogen-high"]
    assert t.agy_models("argon")[0] == "gemini-4-argon-high"
    t.data["antigravity"]["windows"]["gemini-argon-wtu-weekly"]["status"] = "rejected"
    assert t.agy_models("argon")[0] == "claude-sonnet-5-5-high"


def test_quota_errors():
    assert usage.agy_quota_error("Your AI credits balance is too low to continue.")
    assert usage.agy_quota_error("RESOURCE_EXHAUSTED: quota")
    assert not usage.agy_quota_error("退出码 1：permission denied")


def test_agy_log_keeps_the_model_for_resume(tmp_path):
    log = AgyLog("hi", "c0ffee00-1111", log_dir=tmp_path, model="gemini-4-argon-medium")
    log.feed(json.dumps({"event": "step_update", "step_update": {
        "step_index": 1, "step_type": "agent_response", "state": "DONE", "text_delta": "ok"}}))
    path = str(tmp_path / "c0ffee00-1111.jsonl")
    assert latest_settings(path, "antigravity") == ("gemini-4-argon-medium", "")
    conv = TranscriptReader().read(path, "antigravity")
    assert conv["rounds"][-1]["model"] == "gemini-4-argon-medium"
    assert resume_args("antigravity", "gemini-4-argon-medium", "") == ["--model", "gemini-4-argon-medium"]


def test_panel_reply_retries_on_the_next_model(tmp_path, monkeypatch):
    calls = []

    def once(self, sid, agent, cmd, cwd, text, stdin_text, run, env):
        calls.append(cmd[cmd.index("--model") + 1])
        return "Your AI credits balance is too low" if len(calls) == 1 else ""

    monkeypatch.setattr(session_send.SessionSender, "_once", once)
    monkeypatch.setattr(session_send.usage_tracker, "agy_models",
                        lambda choice: ["gemini-4-argon-medium", "gemini-4-hydrogen-medium"])
    s = session_send.SessionSender()
    s.send("c0ffee00-2222", "antigravity", str(tmp_path), "hi", model="gemini-4-argon-medium")
    import time
    for _ in range(50):
        if s.status("c0ffee00-2222")["state"] != "running":
            break
        time.sleep(0.02)
    st = s.status("c0ffee00-2222")
    assert calls == ["gemini-4-argon-medium", "gemini-4-hydrogen-medium"]
    assert st["state"] == "done" and st["model"] == "gemini-4-hydrogen-medium"


def test_codex_thread_open_elsewhere_reads_plainly(monkeypatch):
    monkeypatch.setattr(session_send, "codex_tui_ttys", lambda cwd: ["ttys002"])
    msg = session_send.codex_busy_message("/w")
    assert "终端 ttys002" in msg and "/quit" in msg
    monkeypatch.setattr(session_send, "codex_tui_ttys", lambda cwd: [])
    assert "别处" in session_send.codex_busy_message("/w")


def test_codex_fork_returns_the_new_session_and_finishes(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text("#!/bin/sh\n"
                    "echo \"$@\" > \"$(dirname \"$0\")/args\"\n"
                    "cat > /dev/null\n"
                    "echo '{\"type\":\"thread.started\",\"thread_id\":\"0new-thread-1\"}'\n"
                    "echo '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"ok\"}}'\n"
                    "echo '{\"type\":\"turn.completed\",\"usage\":{}}'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(session_send.gitflow, "codex_optional_mcp", lambda cwd: [])
    done = []
    s = session_send.SessionSender(on_done=done.append)
    new = s.fork("old-thread", str(tmp_path), "接着做", model="gpt-6.1-sol")
    assert new == "0new-thread-1"
    import time
    for _ in range(100):
        if done:
            break
        time.sleep(0.02)
    assert done == ["0new-thread-1"] and s.status(new)["state"] == "done"
    args = (bin_dir / "args").read_text()
    assert "fork -m gpt-6.1-sol old-thread -" in args and "workspace-write" in args


def test_clear_drops_only_finished_runs():
    s = session_send.SessionSender()
    s.runs["a"] = {"state": "error", "ended_at": 100, "text": "x"}
    s.runs["b"] = {"state": "running", "ended_at": 0}
    assert not s.clear("a", before_ms=50)        # ended after the cut-off: keep
    assert s.clear("a", before_ms=200) and s.status("a") == {}
    assert not s.clear("b")


def test_long_agy_conversation_is_measured_from_its_last_turn(tmp_path, monkeypatch):
    import transcript
    monkeypatch.setattr(transcript, "AGY_LOG_DIR", tmp_path)
    (tmp_path / "c1.jsonl").write_text(
        json.dumps({"event": "result", "result": {"status": "SUCCESS", "usage": {"input_tokens": 900}}}) + "\n"
        + json.dumps({"event": "prompt", "text": "x"}) + "\n"
        + json.dumps({"event": "result", "result": {"status": "ERROR", "usage": {"input_tokens": 3166340}}}) + "\n")
    assert transcript.agy_last_input_tokens("c1") == 3166340
    assert transcript.agy_last_input_tokens("missing") == 0


def test_fresh_conversation_carries_recent_tasks():
    from agents import AgentRunnersMixin

    class Store:
        def get_tasks(self, pid):
            return [{"id": 81, "session_id": "old", "description": "默认收起\n更多", "summary": "已改好（b8c85e4）"},
                    {"id": 82, "session_id": "old", "description": "额度显示整体剩余", "summary": ""},
                    {"id": 83, "session_id": "old", "description": "这一条", "summary": ""},
                    {"id": 7, "session_id": "other", "description": "别的", "summary": ""}]

    r = AgentRunnersMixin()
    r.store, r.project_id, r.task_id = Store(), 1, 83
    note = r._agy_carry_note("old")
    assert "#81 默认收起 → 已改好（b8c85e4）" in note and "#82 额度显示整体剩余" in note
    assert "#83" not in note and "#7" not in note and "the previous one grew too long" in note


def test_context_levels_per_executor():
    from sessions import context_level
    assert context_level({"agent": "claude", "ctx": 120_000}) == 0
    assert context_level({"agent": "claude", "ctx": 420_000}) == 1
    assert context_level({"agent": "claude", "ctx": 650_000}) == 2
    assert context_level({"agent": "codex", "ctx": 235_521, "ctx_max": 475_000}) == 0
    assert context_level({"agent": "codex", "ctx": 300_000, "ctx_max": 475_000}) == 1
    assert context_level({"agent": "codex", "ctx": 400_000, "ctx_max": 475_000}) == 2
    assert context_level({"agent": "antigravity", "ctx": 3_166_340}) == 2
    assert context_level({"agent": "antigravity", "ctx": 0}) == 0


def test_index_records_context_size():
    from sessions import _new_entry, ingest_codex_line, ingest_line
    e = _new_entry("s", False)
    ingest_line(e, {"type": "assistant", "timestamp": "2026-11-14T05:00:00Z", "message": {
        "id": "m1", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "usage": {"input_tokens": 2, "cache_read_input_tokens": 419237, "cache_creation_input_tokens": 179,
                  "output_tokens": 10}}}, 0)
    assert e["ctx"] == 419418
    c = _new_entry("t", False, "codex")
    ingest_codex_line(c, {"type": "event_msg", "timestamp": "2026-11-14T05:00:00Z", "payload": {
        "type": "token_count", "info": {"model_context_window": 475000,
                                        "last_token_usage": {"input_tokens": 235521},
                                        "total_token_usage": {"input_tokens": 9, "output_tokens": 1}}}}, 0)
    assert (c["ctx"], c["ctx_max"]) == (235521, 475000)


def test_effort_picked_for_a_session_is_used_as_is(tmp_path, monkeypatch):
    import session_prefs
    import transcript
    from transcript import claude_effort, session_settings
    prefs = session_prefs.SessionPrefs(tmp_path / "prefs.json")
    monkeypatch.setattr(session_prefs, "prefs", prefs)
    monkeypatch.setattr(transcript, "latest_settings", lambda path, agent: (
        {"claude": ("claude-opus-5-5", "high"), "codex": ("gpt-6.1-sol", "high"),
         "antigravity": ("gemini-4-hydrogen-high", "")}[agent]))
    monkeypatch.setattr(transcript, "find_log", lambda sid, agent: "/x")
    assert session_settings("c", "claude") == ("claude-opus-5-5", "high")         # nothing picked
    assert prefs.set_effort("c", "claude", "medium") and not prefs.set_effort("c", "claude", "turbo")
    m, e = session_settings("c", "claude")
    assert e == "!medium" and claude_effort(e) == "medium"                        # below high, because you chose it
    assert resume_args("codex", "gpt-6.1-sol", "!low")[-1] == "model_reasoning_effort=low"
    prefs.set_effort("a", "antigravity", "medium")
    m, _ = session_settings("a", "antigravity")
    assert m == "!gemini-4-hydrogen-medium" and usage.agy_model_id(m) == "gemini-4-hydrogen-medium"
    assert resume_args("antigravity", m, "") == ["--model", "gemini-4-hydrogen-medium"]
    prefs.set_effort("c", "claude", "")
    assert session_settings("c", "claude")[1] == "high" and session_prefs.SessionPrefs(tmp_path / "prefs.json").effort("a") == "medium"


def test_models_json_overrides_the_builtin(tmp_path, monkeypatch):
    f = tmp_path / "models.json"
    f.write_text(json.dumps({"antigravity": {
        "default": {"model": "gemini-9-neon-high", "label": "Neon"},
        "fallback": "gpt-9-high",
        "choices": {"fast": "gemini-9-lite-high", "bad key!": "x-high"}}}))
    cfg = usage._agy_config(f)
    monkeypatch.setattr(usage, "AGY_DEFAULT", usage.AGY_DEFAULT)      # restored after the test
    monkeypatch.setattr(usage, "AGY_FALLBACK", usage.AGY_FALLBACK)
    monkeypatch.setattr(usage, "AGY_CHOICES", usage.AGY_CHOICES)
    monkeypatch.setattr(usage, "AGY_MENU", usage.AGY_MENU)
    monkeypatch.setattr(usage, "_AGY_HIGH_FAMILIES", usage._AGY_HIGH_FAMILIES)
    usage._load_agy(cfg)
    assert usage.AGY_MENU == [{"value": "", "label": "Neon"}, {"value": "fast", "label": "gemini-9-lite-high"}]
    assert usage.agy_chain("") == ["gemini-9-neon-high", "gpt-9-high"]
    assert usage.agy_model_id("fast") == "gemini-9-lite-high"
    assert usage.agy_model_id("gemini-9-neon-low") == "gemini-9-neon-high"     # high floor follows config
    assert usage.agy_model_id("argon") == "gemini-9-neon-high"                  # no longer offered → default


def test_broken_models_json_keeps_the_builtin(tmp_path):
    f = tmp_path / "models.json"
    f.write_text('{"antigravity": {"default": "bad model; rm"}}')
    assert usage._agy_config(f) == usage._AGY_BUILTIN
    f.write_text("not json")
    assert usage._agy_config(f) == usage._AGY_BUILTIN
    assert usage._agy_config(tmp_path / "missing.json") == usage._AGY_BUILTIN
