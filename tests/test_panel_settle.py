"""Panel replies whose run was lost (server restarted mid-send) are settled from the session log."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


class FakeIndex:
    def __init__(self, sessions):
        self._s = sessions

    def sessions(self):
        return self._s


class FakeStore:
    def __init__(self):
        self.updates = []

    def update_task(self, pid, tid, **kw):
        self.updates.append((tid, kw))
        return {"id": tid, **kw}

    def get_project_path(self, pid):
        return ""


def run(monkeypatch, session, started_ago_ms):
    now = int(datetime.now().timestamp() * 1000)
    store = FakeStore()
    monkeypatch.setattr(server, "session_index", FakeIndex([session] if session else []))
    monkeypatch.setattr(server, "store", store)
    monkeypatch.setattr(server, "_last_reply", lambda sid, agent: "好了")
    task = {"id": 5, "session_id": "s", "agent": "claude", "source": "session",
            "started_at": datetime.fromtimestamp((now - started_ago_ms) / 1000).isoformat()}
    return server._settle_panel_task(1, task), now


def test_finished_turn_closes_the_task(monkeypatch):
    now = int(datetime.now().timestamp() * 1000)
    t, _ = run(monkeypatch, {"sid": "s", "kind": "end", "last_at": now - 1000}, 60_000)
    assert t["status"] == "done" and t["summary"] == "好了"


def test_mid_turn_waits_and_silence_fails(monkeypatch):
    now = int(datetime.now().timestamp() * 1000)
    t, _ = run(monkeypatch, {"sid": "s", "kind": "run", "last_at": now - 200_000}, 600_000)
    assert t is None                                   # a long tool call: still working
    t, _ = run(monkeypatch, None, 11 * 60_000)
    assert t["status"] == "failed"                     # nothing happened after the reply
    t, _ = run(monkeypatch, None, 60_000)
    assert t is None                                   # too early to tell


def lost(monkeypatch, desc, session=None):
    store = FakeStore()
    monkeypatch.setattr(server, "session_index", FakeIndex([session] if session else []))
    monkeypatch.setattr(server, "store", store)
    monkeypatch.setattr(server, "_last_reply", lambda sid, agent: "合并好了")
    task = {"id": 70, "session_id": "s", "agent": "claude", "description": desc,
            "started_at": datetime.fromtimestamp(datetime.now().timestamp() - 600).isoformat()}
    return server._settle_lost_worker_task(1, task)


def test_restart_ending_its_own_run_counts_as_done(monkeypatch):
    t = lost(monkeypatch, "请合并并重启服务")
    assert t["status"] == "done" and "服务已重启" in t["summary"]


def test_run_cut_off_by_a_restart_says_so(monkeypatch):
    t = lost(monkeypatch, "改一下首页")
    assert t["status"] == "failed" and "服务重启打断" in t["error"]


def test_finished_turn_before_the_restart_is_done(monkeypatch):
    now = int(datetime.now().timestamp() * 1000)
    t = lost(monkeypatch, "改一下首页", {"sid": "s", "last_at": now - 5 * 60 * 1000, "kind": "end"})
    assert t["status"] == "done" and t["summary"] == "合并好了"


def test_dropping_a_failed_send_cancels_its_reply_task(monkeypatch):
    class Store(FakeStore):
        def get_tasks(self, pid):
            return [{"id": 8, "session_id": "s", "source": "session", "status": "failed", "description": "接着做"},
                    {"id": 9, "session_id": "s", "source": "session", "status": "done", "description": "别的"}]
    store = Store()
    monkeypatch.setattr(server, "store", store)
    sender = server.session_send.SessionSender() if hasattr(server, "session_send") else server.SessionSender()
    sender.runs["s"] = {"state": "error", "ended_at": 100, "text": "接着做", "error": "x"}
    monkeypatch.setattr(server, "sender", sender)
    assert not server._drop_failed_send("s", {"id": 1}, before_ms=50)   # nothing newer yet
    assert server._drop_failed_send("s", {"id": 1}, before_ms=500)
    assert store.updates == [(8, {"status": "cancelled", "summary": "没发出去，已忽略"})]
    assert sender.status("s") == {}


def test_session_used_again_after_its_task_keeps_its_own_state():
    task = {"completed_at": "2027-01-15T08:00:00"}
    ended = server._iso_ms(task["completed_at"])
    assert not server._moved_on({"last_at": ended - 5000}, task)       # the worker's own last writes
    assert server._moved_on({"last_at": ended + 10 * 60_000}, task)    # continued in the terminal
    assert not server._moved_on(None, task)


def test_executor_still_running_after_the_restart_is_waited_for(monkeypatch):
    monkeypatch.setattr(server, "_agent_alive", lambda task: True)
    assert lost(monkeypatch, "改一下首页") is None


def test_commits_left_on_the_branch_count_as_done_and_wait_to_merge(monkeypatch):
    class Store(FakeStore):
        def get_project_path(self, pid):
            return "/repo"
    store = Store()
    monkeypatch.setattr(server, "session_index", FakeIndex([]))
    monkeypatch.setattr(server, "store", store)
    monkeypatch.setattr(server, "_last_reply", lambda sid, agent: "")
    monkeypatch.setattr(server, "_agy_finished_since", lambda sid, ms: False)
    monkeypatch.setattr(server.gitflow, "is_repo", lambda p: True)
    monkeypatch.setattr(server.gitflow, "ahead", lambda repo, line: {"branch": "task-59", "commits": 1, "ids": ["abc"]})
    task = {"id": 82, "session_id": "s", "agent": "antigravity", "description": "改一下额度", "line": 59,
            "started_at": datetime.fromtimestamp(datetime.now().timestamp() - 600).isoformat()}
    t = server._settle_lost_worker_task(1, task)
    assert t["status"] == "done" and t["merge_state"] == "pending" and t["commit_ids"] == ["abc"]
    assert "task-59" in t["summary"]
