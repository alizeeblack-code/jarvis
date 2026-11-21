"""Per-session choices made in Jarvis (for now: the effort to run at). They apply to
every turn Jarvis sends to that session; the desktop app / terminal keep their own."""

import json
import os
import threading

from config import DATA_DIR

PREFS_FILE = DATA_DIR / "session_prefs.json"
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"),
           "codex": ("low", "medium", "high", "xhigh"),
           "antigravity": ("low", "medium", "high", "xhigh")}


class SessionPrefs:
    def __init__(self, path=PREFS_FILE):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path) as f:
                self.data = {k: v for k, v in (json.load(f) or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError, AttributeError):
            self.data = {}

    def _save(self):
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def set_effort(self, sid: str, agent: str, effort: str) -> bool:
        """'' clears the choice (back to the default: at least high)."""
        if effort and effort not in EFFORTS.get(agent, ()):
            return False
        with self.lock:
            if effort:
                self.data.setdefault(sid, {})["effort"] = effort
            else:
                self.data.get(sid, {}).pop("effort", None)
                if not self.data.get(sid):
                    self.data.pop(sid, None)
            self._save()
        return True

    def effort(self, sid: str) -> str:
        with self.lock:
            return (self.data.get(sid) or {}).get("effort", "")


prefs = SessionPrefs()
