"""Project list view: active, recent sessions, and unified archived items."""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from hidden import HiddenSessions  # noqa: E402
from overview import _agy_seg_cache, project_work_list  # noqa: E402
import server  # noqa: E402
import store as store_mod  # noqa: E402
from store import ProjectStore  # noqa: E402

NOW = 1_800_000_000_000
H = 3600 * 1000


def iso(ms):
    return datetime.fromtimestamp(ms / 1000).isoformat()


def task(i, status, sid=None, **kw):
    return {"id": i, "status": status, "session_id": sid, "agent": "claude",
            "description": f"task {i}", "created_at": iso(NOW - 10 * H), **kw}


def sess(sid, last=NOW - H, **kw):
    return {
        "sid": sid, "agent": "claude", "title": f"title {sid}",
        "cwd": "/repo/proj", "origin": "/repo/proj",
        "last_at": last, "first_at": last - 60_000,
        "segs": [[last - 60_000, last]], "days": {},
        "path": f"/logs/{sid}.jsonl", "turns": 3, "kind": "end",
        **kw,
    }


def test_project_work_list_groups_active_recent_and_archived():
    proj = {
        "id": 1, "name": "Proj", "path": "/repo/proj",
        "tasks": [
            task(1, "todo"),
            task(2, "in_progress", "s-run", started_at=iso(NOW - 30_000)),
            task(3, "done", "s-done", completed_at=iso(NOW - 2 * H)),
            task(4, "archived", "s-arch-task", archived_from="done", completed_at=iso(NOW - 5 * H)),
        ],
    }
    sessions = [
        sess("s-run", last=NOW - 10_000, kind="run"),
        sess("s-done", last=NOW - 2 * H),
        sess("s-arch-task", last=NOW - 5 * H),
        sess("s-recent-desk", last=NOW - 30 * 60_000),
        sess("s-wait-desk", last=NOW - 5 * 60_000, asks=True),
        sess("s-agent-arch", last=NOW - 4 * H, archived=True),
        sess("s-panel-arch", last=NOW - 3 * H - 30 * 60_000, archived=True, archived_here=True),
        sess("s-hidden", last=NOW - 3 * H),
        sess("s-reopened", last=NOW - H),
        sess("s-other-proj", cwd="/repo/other", origin="/repo/other", last=NOW - 1000),
        sess("s-headless", entry="codex_exec", agent="codex", last=NOW - 1000),
    ]
    hidden = {"s-hidden": NOW - 2 * H, "s-reopened": NOW - 2 * H}
    res = project_work_list([proj], sessions, 1, existing={"s-run", "s-recent-desk"},
                            now_ms=NOW, hidden=hidden)

    assert [(x["head"], x["sid"]) for x in res["active"]] == [
        (2, "s-run"),
        (None, "s-wait-desk"),
        (1, ""),
    ]
    assert [x["sid"] for x in res["recent"]] == ["s-recent-desk", "s-reopened", "s-done"]
    assert [(x["sid"], x["archive_reason"]) for x in res["archived"]] == [
        ("s-hidden", "hidden"),
        ("s-panel-arch", "panel"),
        ("s-agent-arch", "agent"),
        ("s-arch-task", "task"),
    ]
    assert res["counts"] == {"active": 3, "recent": 3, "archived": 4}


def test_archived_task_with_new_session_activity_returns_to_recent():
    proj = {
        "id": 1, "name": "Proj", "path": "/repo/proj",
        "tasks": [task(1, "archived", "s1", completed_at=iso(NOW - 5 * H))],
    }
    sessions = [sess("s1", last=NOW - H)]
    res = project_work_list([proj], sessions, 1, existing={"s1"}, now_ms=NOW, hidden={})
    assert [x["sid"] for x in res["recent"]] == ["s1"]
    assert res["archived"] == []


def test_project_work_list_filters_and_paginates():
    proj = {
        "id": 1, "name": "Proj", "path": "/repo/proj",
        "tasks": [
            task(1, "archived", description="legacy fix auth", completed_at=iso(NOW - 6 * H)),
        ],
    }
    sessions = [
        sess("c1", agent="claude", title="build list", last=NOW - 1 * H),
        sess("c2", agent="codex", title="codex refactor", last=NOW - 2 * H),
        sess("c3", agent="claude", title="older claude", last=NOW - 3 * H),
        sess("a1", agent="codex", title="archived auth", archived=True, last=NOW - 4 * H),
    ]
    p1 = project_work_list([proj], sessions, 1, existing=set(), now_ms=NOW,
                           recent_offset=0, recent_limit=2)
    assert [x["sid"] for x in p1["recent"]] == ["c1", "c2"]
    assert p1["recent_total"] == 3

    filtered = project_work_list([proj], sessions, 1, existing=set(), now_ms=NOW,
                                 agent="codex", query="auth")
    assert filtered["recent"] == []
    assert [x["sid"] for x in filtered["archived"]] == ["a1"]
    assert filtered["counts"] == {"active": 0, "recent": 3, "archived": 2}


def test_archive_and_unarchive_task_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    st = ProjectStore()
    p = st.create_project("Proj", "/repo/proj", inject=False)
    t1 = st.add_task(p["id"], "first fail", agent="claude")
    st.update_task(p["id"], t1["id"], status="failed", error="boom", session_id="s-line",
                   completed_at=iso(NOW - 10 * 86400 * 1000))
    t2 = st.add_task(p["id"], "follow up", agent="claude")
    st.update_task(p["id"], t2["id"], status="done", session_id="s-line",
                   completed_at=iso(NOW - 9 * 86400 * 1000))

    hid = HiddenSessions(path=tmp_path / "hidden.json")
    hid.hide("s-line", True)
    arch = HiddenSessions(path=tmp_path / "archived.json")
    arch.hide("s-line", True)

    class _Idx:
        def sessions(self):
            return []

    class _WS:
        async def broadcast(self, msg):
            pass

    monkeypatch.setattr(server, "store", st)
    monkeypatch.setattr(server, "hidden_sessions", hid)
    monkeypatch.setattr(server, "archived_sessions", arch)
    monkeypatch.setattr(server, "session_index", _Idx())
    monkeypatch.setattr(server, "ws_manager", _WS())

    async def run():
        res = await server.archive_single_task(p["id"], t2["id"])
        assert res["archived_count"] == 2
        assert st.get_task(p["id"], t1["id"])["status"] == "archived"
        assert st.get_task(p["id"], t1["id"])["archived_from"] == "failed"
        assert st.get_task(p["id"], t2["id"])["archived_from"] == "done"

        lst = await server.get_project_list(p["id"])
        assert lst["recent"] == []
        assert len(lst["archived"]) == 1 and lst["archived"][0]["archive_reason"] == "task"

        un = await server.unarchive_task(p["id"], t2["id"])
        assert un["restored_count"] == 2
        assert st.get_task(p["id"], t1["id"])["status"] == "failed"
        assert st.get_task(p["id"], t2["id"])["status"] == "done"
        assert "s-line" not in hid.snapshot()
        assert "s-line" not in arch.snapshot()

        lst2 = await server.get_project_list(p["id"])
        assert len(lst2["recent"]) == 1
        assert lst2["archived"] == []

    asyncio.run(run())


def test_project_list_truncates_long_text_without_breaking_search():
    long_desc = "line one\n\n" + ("word " * 100) + "UNIQUE_TAIL_TOKEN"
    long_title = "Session title " + ("detail " * 80) + "HIDDEN_TITLE_NEEDLE"
    proj = {
        "id": 1, "name": "Proj", "path": "/repo/proj",
        "tasks": [
            task(1, "done", "s1", description=long_desc,
                 started_at=iso(NOW - 10 * 60_000), completed_at=iso(NOW - 2 * 60_000)),
        ],
    }
    sessions = [
        sess("s1", last=NOW - 2 * 60_000),
        sess("s2", title=long_title, last=NOW - 60_000),
    ]
    res = project_work_list([proj], sessions, 1, existing={"s1", "s2"}, now_ms=NOW)
    by_key = {x["key"]: x for x in res["recent"]}
    t_desc = by_key["t:1"]["task"]["description"]
    s_title = by_key["s:s2"]["title"]
    assert "\n" not in t_desc and len(t_desc) <= 200 and t_desc.endswith("…")
    assert len(s_title) <= 200 and s_title.endswith("…")

    matched_task = project_work_list([proj], sessions, 1, existing=set(), now_ms=NOW,
                                     query="UNIQUE_TAIL_TOKEN")
    assert [x["key"] for x in matched_task["recent"]] == ["t:1"]

    matched_sess = project_work_list([proj], sessions, 1, existing=set(), now_ms=NOW,
                                     query="HIDDEN_TITLE_NEEDLE")
    assert [x["key"] for x in matched_sess["recent"]] == ["s:s2"]


def test_project_list_computes_duration_for_agy_old_and_running(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _agy_seg_cache.clear()
    agy_dir = tmp_path / "agy_logs"
    agy_dir.mkdir()
    # Antigravity standalone session with two bursts (45s + 90s) separated by > GAP_MS
    events = [
        {"ts": NOW - 20 * 60_000, "event": "prompt"},
        {"ts": NOW - 20 * 60_000 + 45_000, "event": "result"},
        {"ts": NOW - 10 * 60_000, "event": "prompt"},
        {"ts": NOW - 10 * 60_000 + 90_000, "event": "result"},
    ]
    (agy_dir / "agy-1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )

    # >14d task with empty segs on session, plus active running task
    old_start = NOW - 20 * 86400 * 1000
    old_end = old_start + 125_000
    run_start = NOW - 150_000
    proj = {
        "id": 1, "name": "Proj", "path": "/repo/proj",
        "tasks": [
            task(1, "done", "s-old", started_at=iso(old_start), completed_at=iso(old_end)),
            task(2, "in_progress", "s-live", agent="antigravity", started_at=iso(run_start)),
        ],
    }
    sessions = [
        {
            "sid": "agy-1", "agent": "antigravity", "title": "agy run",
            "cwd": "/repo/proj", "origin": "/repo/proj",
            "last_at": NOW - 8 * 60_000, "first_at": NOW - 20 * 60_000,
            "segs": [], "days": {}, "path": "", "turns": None, "kind": "end",
        },
        sess("s-old", last=old_end, segs=[]),
        {
            "sid": "s-live", "agent": "antigravity", "title": "live agy",
            "cwd": "/repo/proj", "origin": "/repo/proj",
            "last_at": NOW - 10_000, "first_at": run_start,
            "segs": [], "days": {}, "path": "", "turns": None, "kind": "run",
        },
    ]
    res = project_work_list([proj], sessions, 1, existing=set(), now_ms=NOW)
    by_key = {x["key"]: x for x in res["active"] + res["recent"]}
    assert by_key["s:agy-1"]["active_ms"] == 135_000
    assert by_key["t:1"]["active_ms"] == 125_000
    assert by_key["t:2"]["active_ms"] == 150_000


def test_distinguishes_active_run_and_background_wait():
    from overview import build_overview, project_live

    proj = {"id": 1, "name": "Proj", "path": "/repo/proj", "tasks": []}
    sessions = [
        sess("s-active", last=NOW - 10_000, kind="run"),
        sess("s-bg", last=NOW - 40_000, kind="end",
             bg={"tool_1": [NOW - 50_000, "b1"], "tool_2": [NOW - 45_000, "b2"]},
             step="等后台任务（2 个）"),
    ]
    ov = build_overview([proj], sessions, now_ms=NOW, boards=True)
    now_by_sid = {c["sid"]: c for c in ov["now"]}
    assert now_by_sid["s-active"]["state"] == "run" and now_by_sid["s-active"]["bg_wait"] == 0
    assert now_by_sid["s-bg"]["state"] == "run" and now_by_sid["s-bg"]["bg_wait"] == 2
    assert [c["sid"] for c in ov["now"]] == ["s-active", "s-bg"]
    p1 = next(p for p in ov["projects"] if p["project_id"] == 1)
    assert p1["run"] == 1 and p1["bg_wait"] == 1
    assert ov["today"]["running"] == 1 and ov["today"]["bg_waiting"] == 1

    lst = project_work_list([proj], sessions, 1, existing={"s-active", "s-bg"}, now_ms=NOW)
    active_by_sid = {x["sid"]: x for x in lst["active"]}
    assert active_by_sid["s-active"]["bg_wait"] == 0
    assert active_by_sid["s-bg"]["bg_wait"] == 2

    live = project_live([proj], sessions, 1, existing={"s-active", "s-bg"}, now_ms=NOW)
    live_by_sid = {c["sid"]: c for c in live["now"]}
    assert live_by_sid["s-bg"]["bg_wait"] == 2
