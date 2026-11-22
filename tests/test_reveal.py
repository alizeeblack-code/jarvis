"""Opening a session on this Mac from the desk display: which way each kind of session goes."""
import reveal

SID = "ee416abc-fc37-4753-ad5c-b93168c06bca"
J = "http://localhost:5180/"


def _plan(monkeypatch, s, host="", headless=False, own=True):
    monkeypatch.setattr(reveal, "host_app", lambda agent, cwd: host)
    monkeypatch.setattr(reveal.os.path, "isdir", lambda p: own)
    return reveal.plan({"agent": "claude", "sid": SID, "cwd": "/w", **s}, headless, J)


def test_desktop_sessions_open_themselves(monkeypatch):
    p = _plan(monkeypatch, {"local_id": "local_abc"}, host="/Applications/Ghostty.app")
    assert p == {"how": "session", "open": ["claude://code/continue?session=local_abc"]}


def test_live_sessions_bring_their_app_up(monkeypatch):
    p = _plan(monkeypatch, {"agent": "codex"}, host="/Applications/Ghostty.app")
    assert p == {"how": "app", "open": ["-a", "/Applications/Ghostty.app"]}


def test_finished_claude_sessions_are_imported_into_the_desktop_app(monkeypatch):
    assert _plan(monkeypatch, {})["open"] == [f"claude://resume?session={SID}"]
    # an id that is not a CLI session id is never put in a link
    assert _plan(monkeypatch, {"sid": "x"})["how"] == "app"


def test_jarvis_runs_open_jarvis_and_others_their_app(monkeypatch):
    assert _plan(monkeypatch, {}, headless=True) == {"how": "jarvis", "open": [J]}
    assert _plan(monkeypatch, {"agent": "antigravity", "sid": "c1"})["open"] == ["-a", "/Applications/Antigravity.app"]
    assert _plan(monkeypatch, {"agent": "antigravity", "sid": "c1"}, own=False) is None


def test_app_of_a_process_path():
    assert reveal._app_of("/Applications/Ghostty.app/Contents/MacOS/ghostty") == "/Applications/Ghostty.app"
    assert reveal._app_of("/bin/zsh") == ""


def test_a_locked_mac_opens_after_unlock(monkeypatch):
    opened, states = [], iter([True, True, False])
    monkeypatch.setattr(reveal, "wake", lambda: None)
    monkeypatch.setattr(reveal, "screen_locked", lambda: next(states, False))
    monkeypatch.setattr(reveal, "_open", lambda args: opened.append(args) or True)
    monkeypatch.setattr(reveal, "can_unlock", lambda: False)
    monkeypatch.setattr(reveal.time, "sleep", lambda s: None)
    started = []
    monkeypatch.setattr(reveal.threading, "Thread",
                        lambda target, args, daemon: started.append((target, args)) or type("T", (), {"start": lambda self: None})())
    r = reveal.reveal({"how": "session", "open": ["claude://x"]})
    assert r == {"ok": True, "how": "session", "locked": True, "unlocked": False} and opened == []
    target, args = started[0]
    target(*args)                       # the waiting thread, run inline
    assert opened == [["claude://x"]]


def test_a_typed_unlock_backs_off_after_a_wrong_password(monkeypatch):
    calls = []
    monkeypatch.setattr(reveal, "_password", lambda: "pw")
    monkeypatch.setattr(reveal, "screen_locked", lambda: True)          # typing did not unlock it
    monkeypatch.setattr(reveal.time, "sleep", lambda s: None)
    monkeypatch.setattr(reveal.subprocess, "run", lambda args, **kw: calls.append((args, kw.get("env", {}).get("JARVIS_PW")))
                        or type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())
    monkeypatch.setitem(reveal._unlock, "off_until", 0.0)
    monkeypatch.setitem(reveal._unlock, "last", 0.0)
    assert reveal.try_unlock() is False
    # the password travels in the environment, never in the arguments
    assert calls and calls[0][1] == "pw" and all("pw" not in a for a in calls[0][0])
    assert reveal.can_unlock() is False                                # backing off now


def test_a_stored_password_unlocks_and_opens_at_once(monkeypatch):
    opened = []
    monkeypatch.setattr(reveal, "wake", lambda: None)
    monkeypatch.setattr(reveal, "screen_locked", lambda: True)
    monkeypatch.setattr(reveal, "can_unlock", lambda: True)
    monkeypatch.setattr(reveal, "try_unlock", lambda: True)
    monkeypatch.setattr(reveal, "_open", lambda args: opened.append(args) or True)
    monkeypatch.setattr(reveal.time, "sleep", lambda s: None)
    r = reveal.reveal({"how": "session", "open": ["claude://x"]})
    assert r == {"ok": True, "how": "session", "locked": False, "unlocked": True} and opened == [["claude://x"]]
