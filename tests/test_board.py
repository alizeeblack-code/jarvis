"""Board: one card per line of work, columns by who acts next."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from board import project_board  # noqa: E402

NOW = 1_800_000_000_000
H = 3600 * 1000


def iso(ms):
    return datetime.fromtimestamp(ms / 1000).isoformat()


def task(i, status, sid=None, **kw):
    return {"id": i, "status": status, "session_id": sid, "agent": "claude",
            "created_at": iso(NOW - 10 * H), **kw}


def sess(sid, last=NOW - H, **kw):
    return {"sid": sid, "agent": "claude", "title": f"title {sid}", "last_at": last,
            "path": f"/logs/{sid}.jsonl", "turns": 3, **kw}


def cols(board):
    return {c: [(x["head"], x["sid"], x["tasks"]) for x in v] for c, v in board.items() if v}


def test_follow_ups_and_replies_share_one_card():
    tasks = [task(1, "done", "s1", completed_at=iso(NOW - 5 * H)),
             task(2, "done", "s1", completed_at=iso(NOW - 4 * H)),
             task(3, "in_progress", "s2", source="session", started_at=iso(NOW - 60000))]
    b = project_board(tasks, [(sess("s1"), "idle"), (sess("s2"), "run")], NOW)
    assert cols(b) == {"done": [(2, "s1", [1, 2])], "in_progress": [(3, "s2", [3])]}
    assert b["in_progress"][0]["title"] == "title s2"       # a panel reply shows its session
    assert b["done"][0]["title"] == ""


def test_columns_follow_who_acts_next():
    tasks = [task(1, "todo"), task(2, "pending"), task(3, "review", "s3"),
             task(4, "failed", "s4", started_at=iso(NOW - H)),
             task(5, "failed", "s5", started_at=iso(NOW - 5 * 86400 * 1000), created_at=iso(NOW - 6 * 86400 * 1000)),
             task(6, "cancelled"),
             task(7, "done", "s7", source="session", completed_at=iso(NOW - H)),
             task(8, "done", "s8", completed_at=iso(NOW - H))]
    b = project_board(tasks, [(sess("s7"), "wait"), (sess("s8"), "wait")], NOW)
    assert [x["head"] for x in b["todo"]] == [1]
    assert [x["head"] for x in b["pending"]] == [2]
    assert sorted(x["head"] for x in b["wait"]) == [3, 4, 7]   # plan, recent failure, your reply answered
    assert sorted(x["head"] for x in b["done"]) == [5, 6, 8]   # a worker task that finished is done


def test_outside_sessions_join_the_board():
    tasks = [task(1, "done", "mcp", session_linked="mcp"), task(2, "done", "mcp", session_linked="mcp")]
    b = project_board(tasks, [(sess("mcp"), "wait"), (sess("desk"), "wait"),
                              (sess("term"), "run"), (sess("old", last=NOW - 60 * 86400 * 1000), "idle")], NOW)
    assert sorted(x["head"] for x in b["done"] if x["head"]) == [1, 2]   # MCP tasks stay separate cards
    assert [(x["sid"], x["title"], x["turns"]) for x in b["wait"]] == [("desk", "title desk", 3)]
    assert [x["sid"] for x in b["in_progress"]] == ["term"]
    assert "old" in [x["sid"] for x in b["done"]]                  # history stays while its log is there


def test_which_sessions_get_a_card():
    ss = [(sess("gone", path=""), "idle"),                 # log cleaned up: can't read or resume
          (sess("empty", turns=0), "idle"),                # never asked anything
          (sess("script", entry="codex_exec"), "idle"),    # headless run
          (sess("put-away", last=NOW - 2 * H), "idle"),    # hidden after its last activity
          (sess("back", last=NOW - H), "wait"),            # hidden, then active again
          (sess("arch", archived=True), "archived"),
          ({"sid": "agy", "agent": "antigravity", "title": "a", "last_at": NOW - H, "turns": None}, "idle")]
    hidden = {"put-away": NOW - H, "back": NOW - 2 * H}
    b = project_board([], ss, NOW, hidden)
    shown = {x["sid"]: c for c, v in b.items() for x in v}
    assert shown == {"back": "wait", "agy": "done"}
    assert not any(x["sid"] == "arch" for c in b.values() for x in c)    # archived: off the board


def test_headless_runs_are_not_waiting_on_you():
    from sessions import headless, session_state
    end = {"kind": "end", "last_at": NOW - 60_000}
    desk = {**end, "agent": "claude", "entry0": "claude-desktop", "local_id": "local_1"}
    assert session_state(desk, NOW) == "done" and not headless(desk)
    assert session_state({**desk, "asks": True}, NOW) == "wait"
    # `claude -p` from a shell inside the desktop app: same entrypoint, not in the app's list
    assert session_state({**desk, "local_id": ""}, NOW) == "idle"
    # a desktop session you replied to from the panel later logs sdk-cli; how it began counts
    assert not headless({**desk, "entry": "sdk-cli"})
    assert session_state({**end, "agent": "claude", "entry0": "sdk-cli"}, NOW) == "idle"
    assert session_state({**end, "agent": "codex", "entry": "codex_exec"}, NOW) == "idle"
    assert session_state({**end, "agent": "codex", "entry": "codex-tui"}, NOW) == "done"
    assert session_state({**end, "agent": "claude", "entry0": "sdk-cli", "asks": True}, NOW) == "idle"
    assert session_state({**end, "agent": "antigravity", "entry0": "agy-print"}, NOW) == "idle"


def test_asks_you_from_the_closing_box_question_tools_or_a_question_mark():
    from sessions import _new_entry, asks_you, ingest_line
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_sessions import assistant, user
    T = 1_800_000_000_000

    def run(*recs):
        e = _new_entry("s", False)
        for r in recs:
            ingest_line(e, r, 0)
        return asks_you(e)

    wrap = lambda ask, blocked=False: assistant(T + 2, "w", stop="end_turn", tool=(
        "mcp__ccd_turn__wrap_up", {"recap": "r", **({"ask": ask} if ask else {}), "blocked": blocked}))
    assert run(user(T, "做"), wrap("要合并吗？")) is True
    assert run(user(T, "做"), wrap("")) is False
    assert run(user(T, "做"), wrap("", True)) is True
    q = assistant(T + 1, "q", tool=("AskUserQuestion", {"questions": []}))
    assert run(user(T, "做"), q) is True
    answered = {"type": "user", "timestamp": "2027-01-15T08:00:03Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "A"}]}}
    assert run(user(T, "做"), q, answered) is False
    # no closing box (terminal / Codex): a reply ending in a question mark
    ask_text = assistant(T + 3, "t", stop="end_turn")
    ask_text["message"]["content"] = [{"type": "text", "text": "要我现在部署吗？"}]
    assert run(user(T, "做"), ask_text) is True
    # a new prompt starts over
    assert run(user(T, "做"), wrap("要合并吗？"), user(T + 9, "合并")) is False


def test_queued_followup_keeps_in_progress_head():
    tasks = [
        task(1, "in_progress", "s1", started_at=iso(NOW - 60000)),
        task(2, "pending", "s1", predecessor_id=1),
        task(3, "done", "s2", completed_at=iso(NOW - H)),
        task(4, "pending", "s2", predecessor_id=3),
    ]
    b = project_board(tasks, [(sess("s1"), "run"), (sess("s2"), "idle")], NOW)
    assert [(x["head"], x["tasks"]) for x in b["in_progress"]] == [(1, [1, 2])]
    assert [(x["head"], x["tasks"]) for x in b["pending"]] == [(4, [3, 4])]



def test_failed_panel_reply_follows_the_session_once_it_moves_on():
    from board import project_board
    now = 1_800_000_000_000
    t = {"id": 3, "status": "failed", "source": "session", "session_id": "s1", "agent": "codex",
         "completed_at": "2027-01-15T08:00:00"}
    import datetime
    at = int(datetime.datetime.fromisoformat(t["completed_at"]).timestamp() * 1000)
    sess = {"sid": "s1", "agent": "codex", "path": "/x", "last_at": at - 1000}
    cols = project_board([t], [(sess, "done")], at + 60_000)
    assert [c["head"] for c in cols["wait"]] == [3]
    sess["last_at"] = at + 5000                   # continued in the terminal
    cols = project_board([t], [(sess, "done")], at + 60_000)
    assert [c["head"] for c in cols["done"]] == [3] and not cols["wait"]


def test_archived_sessions_leave_the_board_and_the_all_list(tmp_path, monkeypatch):
    import hidden
    import sessions as sess
    from board import project_board
    from overview import session_page
    store = hidden.HiddenSessions(tmp_path / "a.json")
    monkeypatch.setattr(hidden, "archived_sessions", store)
    s = {"sid": "s1", "agent": "claude", "path": "/x", "last_at": 1000, "turns": 2}
    assert not sess._still_archived(store.snapshot(), s)
    store.hide("s1")
    snap = store.snapshot()
    assert sess._still_archived(snap, s)
    assert not sess._still_archived(snap, {**s, "last_at": snap["s1"] + 120_000})   # used again: back
    cols = project_board([], [({**s, "archived": True}, "archived")], 2000)
    assert not any(cols.values())
