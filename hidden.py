"""Sessions put away from the board ("收起") — Jarvis-only; the apps' data is untouched.
A session comes back by itself once it has activity after it was put away."""

import json
import os
import threading
import time

from config import DATA_DIR

HIDDEN_FILE = DATA_DIR / "hidden_sessions.json"


class HiddenSessions:
    def __init__(self, path=HIDDEN_FILE):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path) as f:
                self.data = {k: int(v) for k, v in (json.load(f) or {}).items()}
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

    def hide(self, sid: str, on: bool = True):
        with self.lock:
            if on:
                self.data[sid] = int(time.time() * 1000)
            else:
                self.data.pop(sid, None)
            self._save()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.data)


hidden_sessions = HiddenSessions()
# Sessions archived from the panel: out of "now", the board and the "all" list (they
# sit under 全部 › 已归档). Like a put-away session, new activity brings one back.
archived_sessions = HiddenSessions(DATA_DIR / "archived_sessions.json")
