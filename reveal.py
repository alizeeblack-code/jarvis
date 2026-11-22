"""Bring a session up on this Mac, from the desk display (POST /api/sessions/{sid}/reveal).

Opens the session where it lives:
  - a Claude desktop-app session: that very session, through the app's own
    `claude://code/continue?session=local_…` link (what its Dock menu and Spotlight use);
  - a session running in a terminal or an editor: that app, brought to the front
    (found the way AI Remote does it: the agent process whose working folder is the
    session's, then the .app it runs under);
  - a finished Claude session that never was in the desktop app: imported into it with
    `claude://resume?session=<id>`;
  - Codex / Antigravity with nothing live: their app; Jarvis's own headless runs: Jarvis.

The screen is woken first. A locked Mac is unlocked only if you opted in by storing your
login password in your login keychain yourself:
    security add-generic-password -U -s jarvis-unlock -a "$USER" -w
Then the password is typed into the lock screen (System Events; the process running
Jarvis needs Accessibility access), at most once a minute, and a wrong password turns
this off for an hour so the account never gets locked out. Without it, or when typing
fails, Jarvis waits for you to unlock (Touch ID, Apple Watch) and opens the session then.
Anyone holding Jarvis's access token can then unlock this Mac: that is the price.
"""
import ctypes
import os
import re
import subprocess
import threading
import time
from typing import Optional

WAIT_FOR_UNLOCK = 180          # seconds
KEYCHAIN_SERVICE = "jarvis-unlock"
UNLOCK_EVERY = 60              # at most one typed attempt a minute
UNLOCK_BACKOFF = 3600          # after a wrong password, stop typing it for an hour
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_APPS = {                      # an agent's own app, when nothing live says where it runs
    "codex": ["/Applications/Codex.app", "/Applications/ChatGPT.app"],
    "antigravity": ["/Applications/Antigravity.app"],
    "claude": ["/Applications/Claude.app"],
}
_PROCS = {"claude": "claude", "codex": "codex", "antigravity": "agy"}


# ── the screen ──────────────────────────────────────────────────────────────

def screen_locked() -> bool:
    """True while the login window covers the screen (CGSessionCopyCurrentDictionary)."""
    try:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        d = cg.CGSessionCopyCurrentDictionary()
        if not d:
            return False
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
        v = cf.CFDictionaryGetValue(d, key)
        locked = bool(v) and bool(cf.CFBooleanGetValue(v))
        cf.CFRelease(key)
        cf.CFRelease(d)
        return locked
    except Exception:
        return False


def wake() -> None:
    """Turn the display on (it shows the lock screen if the Mac is locked)."""
    try:
        subprocess.Popen(["caffeinate", "-u", "-t", "3"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ── where a session lives ───────────────────────────────────────────────────

def _run(args: list) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _app_of(path: str) -> str:
    """'/Applications/Ghostty.app/Contents/MacOS/ghostty' -> '/Applications/Ghostty.app'."""
    i = path.find(".app/Contents/")
    return path[:i + 4] if i > 0 else ""


def host_app(agent: str, cwd: str) -> str:
    """The .app a live agent process working in `cwd` runs under ('' if none)."""
    proc = _PROCS.get(agent)
    if not proc or not cwd:
        return ""
    pids = []
    cur = None
    for line in _run(["lsof", "-a", "-d", "cwd", "-c", proc, "-Fpn"]).splitlines():
        if line.startswith("p"):
            cur = int(line[1:])
        elif line.startswith("n") and cur is not None and os.path.realpath(line[1:]) == os.path.realpath(cwd):
            pids.append(cur)
    if not pids:
        return ""
    parent, path = {}, {}
    for line in _run(["ps", "-axo", "pid=,ppid=,comm="]).splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])] = int(parts[1])
            path[int(parts[0])] = parts[2]
    found = []
    for pid in pids:
        # the outermost .app up the chain: the CLI itself ships inside a claude.app,
        # the terminal or editor that runs it is further up
        p, hops, top = pid, 0, ""
        while p and p in parent and hops < 30:
            top = _app_of(path.get(p, "")) or top
            p, hops = parent[p], hops + 1
        if top:
            found.append(top)
    # a terminal or editor beats the Claude app itself (its own sessions go by link)
    found.sort(key=lambda a: a.endswith("/Claude.app"))
    return found[0] if found else ""


def _own_app(agent: str) -> str:
    return next((a for a in _APPS.get(agent, []) if os.path.isdir(a)), "")


def plan(s: dict, headless: bool, jarvis_url: str) -> Optional[dict]:
    """How to bring session `s` up: {"how": session|app|import|jarvis, "open": [...]}."""
    agent = s.get("agent") or "claude"
    sid = s.get("sid") or ""
    if agent == "claude" and (s.get("local_id") or "").startswith("local_"):
        return {"how": "session", "open": [f"claude://code/continue?session={s['local_id']}"]}
    app = host_app(agent, s.get("cwd") or "")
    if app:
        return {"how": "app", "open": ["-a", app]}
    if headless:
        return {"how": "jarvis", "open": [jarvis_url]}
    if agent == "claude" and _UUID.match(sid):
        return {"how": "import", "open": [f"claude://resume?session={sid}"]}
    own = _own_app(agent)
    return {"how": "app", "open": ["-a", own]} if own else None


# ── unlocking, for those who opted in ───────────────────────────────────────

_unlock = {"last": 0.0, "off_until": 0.0, "note": ""}


def _password() -> str:
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                            "-a", os.environ.get("USER", ""), "-w"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.rstrip("\n") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def can_unlock() -> bool:
    """Opted in, and neither just tried nor backing off after a wrong password."""
    now = time.time()
    if now < _unlock["off_until"] or now - _unlock["last"] < UNLOCK_EVERY:
        return False
    try:
        return subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                               "-a", os.environ.get("USER", "")],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def try_unlock() -> bool:
    """Type the stored password into the lock screen; True once the screen is unlocked."""
    _unlock["last"] = time.time()
    pw = _password()
    if not pw:
        return False
    time.sleep(1.5)                         # the lock screen needs a moment after waking
    try:
        r = subprocess.run(
            ["osascript", "-e", 'tell application "System Events"',
             "-e", 'keystroke (system attribute "JARVIS_PW")',
             "-e", "delay 0.3", "-e", "key code 36", "-e", "end tell"],
            env={**os.environ, "JARVIS_PW": pw},   # never on a command line
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        pw = ""
    if r.returncode != 0:                   # most likely: no Accessibility access
        _unlock["note"] = (r.stderr or "").strip()[:200]
        print(f"[reveal] typing the password failed: {_unlock['note']}", flush=True)
        return False
    for _ in range(8):
        time.sleep(0.5)
        if not screen_locked():
            _unlock["note"] = ""
            return True
    _unlock["off_until"] = time.time() + UNLOCK_BACKOFF     # wrong password: don't try again soon
    _unlock["note"] = "typed, still locked"
    print("[reveal] typed the password but the Mac stayed locked; not trying again for an hour", flush=True)
    return False


# ── doing it ────────────────────────────────────────────────────────────────

_pending = {"gen": 0}
_lock = threading.Lock()


def _open(args: list) -> bool:
    try:
        return subprocess.run(["open", *args], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _open_after_unlock(gen: int, args: list) -> None:
    end = time.time() + WAIT_FOR_UNLOCK
    while time.time() < end:
        time.sleep(1)
        with _lock:
            if _pending["gen"] != gen:      # a newer tap took over
                return
        if not screen_locked():
            time.sleep(0.8)                 # let the desktop come back first
            _open(args)
            return


def reveal(p: dict) -> dict:
    """Wake the screen and open `p` now, or once the Mac is unlocked."""
    wake()
    with _lock:
        _pending["gen"] += 1
        gen = _pending["gen"]
    if screen_locked():
        # typing takes a few seconds; the caller waits for it, so it can say what happened
        if can_unlock() and try_unlock():
            time.sleep(0.8)                 # let the desktop come back first
            return {"ok": _open(p["open"]), "how": p["how"], "locked": False, "unlocked": True}
        threading.Thread(target=_open_after_unlock, args=(gen, p["open"]), daemon=True).start()
        return {"ok": True, "how": p["how"], "locked": True, "unlocked": False}
    return {"ok": _open(p["open"]), "how": p["how"], "locked": False, "unlocked": False}
