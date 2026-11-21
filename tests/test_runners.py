import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents  # noqa: E402
from agents import AgentRunnersMixin  # noqa: E402


class FakeStore:
    def __init__(self):
        self.logs, self.updates = [], {}

    def append_log(self, pid, tid, content=None, type=None, meta=None):
        self.logs.append((type, content))

    def update_task(self, pid, tid, **kw):
        self.updates.update(kw)

    def get_task(self, pid, tid):
        return None


class Fake(AgentRunnersMixin):
    def __init__(self, tmp):
        self.store, self.project_id, self.task_id = FakeStore(), 1, 1
        self.task, self.project_path, self.project_settings = {}, str(tmp), {}
        self.cancelled, self.session_id, self.model = False, "", ""
        self.result_summary = self._last_message = ""
        self.tool_calls, self.turn_count, self.error_count = [], 0, 0
        self.events = []

    async def _broadcast_event(self, name, data):
        self.events.append(name)

    async def _broadcast_log(self, msg):
        pass

    async def _broadcast_status(self, *a):
        pass


def test_codex_events_map_to_task_logs(tmp_path):
    f, st = Fake(tmp_path), {"usage": {}, "fatal": "", "texts": []}
    evs = [
        {"type": "thread.started", "thread_id": "th-1"},
        {"type": "item.completed", "item": {"id": "e", "type": "error", "message": "hook warning"}},
        {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "working"}},
        {"type": "item.started", "item": {"id": "c", "type": "command_execution", "command": "ls"}},
        {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": "ls",
                                            "exit_code": 2, "aggregated_output": "nope"}},
        {"type": "item.completed", "item": {"id": "f", "type": "file_change",
                                            "changes": [{"path": "a.py", "kind": "update"}]}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}},
    ]
    for e in evs:
        asyncio.run(f._handle_codex_event(e, st))
    assert f.session_id == "th-1" and f.store.updates["session_id"] == "th-1"
    assert st["fatal"] == ""                       # item-level error is only a notice
    assert f._last_message == "working" and st["usage"] == {"input_tokens": 10, "output_tokens": 3}
    assert [t["tool"] for t in f.tool_calls] == ["Bash", "Edit"] and f.tool_calls[0]["is_error"]
    asyncio.run(f._handle_codex_event({"type": "turn.failed", "error": {"message": "quota"}}, st))
    assert st["fatal"] == "quota"


def test_stream_kills_runaway_process(tmp_path, monkeypatch):
    f = Fake(tmp_path)
    try:
        asyncio.run(f._stream(["sleep", "30"], hard_limit_min=0.05))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "已终止" in str(e)


def test_stream_feeds_stdin_and_lines(tmp_path):
    f, seen = Fake(tmp_path), []

    async def on_line(line):
        seen.append(line)
    proc = asyncio.run(f._stream(["cat"], stdin_text="-flag looking prompt\nsecond", on_line=on_line))
    assert proc.returncode == 0 and seen == ["-flag looking prompt", "second"]
