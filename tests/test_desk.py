"""The desk display's data: six keys, live sessions first, then recent idle ones."""
from desk import desk_view


def _card(state, sid, **kw):
    return {"sid": sid, "title": f"t-{sid}", "project_name": "p", "agent": "claude",
            "state": state, "bg_wait": 0, "step": "Edit a.py", "err": "boom",
            "last_at": 1000, **kw}


def test_fields_and_counts():
    ov = {"generated_at": 5, "now": [
        _card("error", "e"), _card("wait", "w"), _card("run", "r"),
        _card("run", "b", bg_wait=2), _card("done", "d"), _card("quiet", "q")]}
    v = desk_view(ov)
    assert v["at"] == 5 and v["more"] == 0
    assert v["counts"] == {"wait": 1, "run": 2, "bg": 1, "done": 1, "error": 1}
    by = {it["sid"]: it for it in v["items"]}
    assert [it["sid"] for it in v["items"]] == ["e", "w", "r", "b", "d", "q"]
    assert by["r"]["step"] == "Edit a.py" and by["d"]["step"] == ""
    assert by["e"]["err"] == "boom" and by["w"]["err"] == ""
    assert by["b"]["bg"] == 2
    assert set(by["w"]) == {"sid", "title", "project", "agent", "state", "bg", "step", "err", "at"}


def test_free_keys_go_to_recent_idle_sessions():
    ov = {"now": [_card("wait", "w"), _card("run", "r")],
          "all": [_card("run", "r"), _card("idle", "i1"), _card("archived", "a"),
                  _card("idle", "i2"), _card("idle", "i3"), _card("idle", "i4"), _card("idle", "i5")]}
    v = desk_view(ov)
    assert [it["sid"] for it in v["items"]] == ["w", "r", "i1", "i2", "i3", "i4"]
    assert v["items"][2]["state"] == "idle"


def test_too_many_waiting_reports_the_rest():
    ov = {"now": [_card("wait", f"w{i}") for i in range(8)]}
    v = desk_view(ov)
    assert len(v["items"]) == 6 and v["more"] == 2 and v["counts"]["wait"] == 8


def test_sessions_outside_projects_go_by_their_title():
    ov = {"now": [_card("run", "x", project="other", project_name="其它", title="Mac 硬盘空间清理"),
                  _card("run", "y", project="other", project_name="其它", title=""),
                  _card("run", "z", project="p1", project_name="Jarvis")]}
    names = [it["project"] for it in desk_view(ov)["items"]]
    assert names == ["Mac 硬盘空间清理", "其它", "Jarvis"]


def test_scripted_runs_of_other_programs_stay_off_the_keys():
    ov = {"now": [_card("run", "mine"), _card("run", "sdk", scripted=True)]}
    v = desk_view(ov)
    assert [it["sid"] for it in v["items"]] == ["mine"] and v["counts"]["run"] == 1


def test_quota_left_per_agent_from_its_tightest_window():
    from desk import desk_usage
    now = 1_000_000
    snap = {"claude": {"at": 5, "windows": {"seven_day": {"used": 63.0, "resets_at": now + 9},
                                             "five_hour": {"used": 90.0, "resets_at": now - 1}}},   # already reset
            "codex": None,
            "antigravity": {"at": 7, "windows": {
                "gemini-weekly": {"used": 20, "resets_at": now + 5, "group": "Gemini"},
                "3p-weekly": {"used": 100, "resets_at": now + 5, "group": "Claude/GPT"},
                "gemini-5h": {"used": 0, "resets_at": now + 1, "group": "Gemini", "status": "rejected"}}}}
    assert desk_usage(snap, now) == [
        {"name": "Claude", "left": 37, "resets_at": now + 9, "at": 5},
        {"name": "Gemini", "left": 0, "resets_at": now + 1, "at": 7},
    ]


def test_claude_quota_is_only_reread_after_claude_was_used():
    from server import claude_used_since
    sessions = [{"agent": "codex", "last_at": 900}, {"agent": "claude", "last_at": 400}]
    assert claude_used_since(500, sessions) is False          # only Codex ran since
    assert claude_used_since(300, sessions) is True
