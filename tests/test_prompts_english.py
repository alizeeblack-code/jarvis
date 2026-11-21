"""What Jarvis tells the agents is in English (the user's own text stays as written)."""
import asyncio
import re

import mcp_task_server
from worker import WorkerProcess

CJK = re.compile(r"[一-鿿]")


class _Store:
    def __init__(self, tasks):
        self.tasks = {t["id"]: t for t in tasks}

    def get_task(self, project_id, task_id):
        return self.tasks.get(task_id)


def _worker(tmp_path, task, others=(), progress=True):
    w = WorkerProcess.__new__(WorkerProcess)
    w.task, w.project_id, w.project_path, w.agent = task, 1, str(tmp_path), "claude"
    w.store = _Store([task, *others])
    if progress:
        (tmp_path / "PROGRESS.md").write_text("# log\n")
    return w


def test_worker_prompts(tmp_path):
    base = {"id": 2, "description": "fix the login page", "images": [{"path": "/tmp/a.png"}]}
    prompts = [
        _worker(tmp_path, dict(base))._build_prompt(),
        _worker(tmp_path, {**base, "plan_mode": True})._build_prompt(),
        _worker(tmp_path, {**base, "plan_mode": True, "plan_feedback": "smaller steps", "session_id": "s"})._build_prompt(),
        _worker(tmp_path, {**base, "predecessor_id": 1},
                others=[{"id": 1, "description": "x", "session_id": "s1", "agent": "claude"}])._build_prompt(),
    ]
    for p in prompts:
        assert "fix the login page" in p or "smaller steps" in p
        assert not CJK.search(p), p
    assert "Continue from the context of task #1" in prompts[3]
    assert '"subtasks"' in prompts[1] and "Plan only" in prompts[1]


def test_mcp_tools_described_in_english():
    tools = asyncio.run(mcp_task_server.mcp.list_tools())
    assert len(tools) >= 10
    for t in tools:
        assert not CJK.search(t.description or ""), t.name
