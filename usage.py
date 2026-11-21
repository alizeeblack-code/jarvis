"""Subscription usage per executor: how much of each window is used and when
it resets.

Claude: the CLI's stream-json output carries a `rate_limit_event`; every run
Jarvis starts (worker tasks, replies from the session panel) records it for
free, and `probe_claude()` runs a one-word Haiku turn to refresh it on demand.
Codex: every turn's `token_count` event in the rollout logs carries
`rate_limits` (also for runs outside Jarvis), so it is read from disk.
Antigravity: `agy -p /usage --output-format json` answers without a model call
(no tokens, no conversation); it is refreshed in the background every few minutes.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from config import DATA_DIR
from sessions import CODEX_HOME

USAGE_FILE = DATA_DIR / "usage.json"
CODEX_TAIL = 1 << 20
PROBE_TIMEOUT = 120
AGY_TTL_MS = 5 * 60 * 1000
_AGY_GROUPS = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude/GPT", "Gemini 4 Argon": "Argon"}


def claude_windows(info: dict) -> dict:
    """rate_limit_info -> {window: {used (0-100 or None), resets_at (ms), status}}."""
    out = {}
    for name, w in (info.get("unifiedWindows") or {}).items():
        if isinstance(w, dict):
            u = w.get("utilization")
            out[name] = {"used": round(u * 100, 1) if isinstance(u, (int, float)) else None,
                         "resets_at": int(w.get("resetsAt") or 0) * 1000,
                         "status": w.get("status") or ""}
    kind = info.get("rateLimitType")
    if kind and kind not in out:
        out[kind] = {"used": None, "resets_at": int(info.get("resetsAt") or 0) * 1000,
                     "status": info.get("status") or ""}
    return out


def codex_limits(rl: dict, at_ms: int) -> dict:
    windows = {}
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if isinstance(w, dict):
            minutes = w.get("window_minutes") or 0
            windows[f"m{minutes}"] = {"used": w.get("used_percent"), "minutes": minutes,
                                      "resets_at": int(w.get("resets_at") or 0) * 1000,
                                      "status": "rejected" if (w.get("used_percent") or 0) >= 100 else "allowed"}
    credits = rl.get("credits") or {}
    bal = credits.get("balance")
    try:
        bal = None if credits.get("unlimited") else float(bal)
    except (TypeError, ValueError):
        bal = None
    return {"windows": windows, "plan": rl.get("plan_type") or "", "credits": bal,
            "limited": bool(rl.get("rate_limit_reached_type")), "at": at_ms}


def latest_codex(codex_home=CODEX_HOME, files: int = 6) -> dict:
    """Newest `rate_limits` in the most recently written rollout logs."""
    days = sorted(Path(codex_home).glob("sessions/*/*/*"), reverse=True)[:3]   # YYYY/MM/DD
    paths = sorted((p for d in days for p in d.glob("rollout-*.jsonl")),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:files]
    best = None
    for p in paths:
        try:
            with open(p, "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - CODEX_TAIL))
                lines = f.read().split(b"\n")
        except OSError:
            continue
        for raw in reversed(lines):
            if b'"rate_limits"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            rl = (d.get("payload") or {}).get("rate_limits")
            if isinstance(rl, dict) and (rl.get("primary") or rl.get("secondary")):
                from sessions import parse_ts
                at = parse_ts(d.get("timestamp"))
                if not best or at > best["at"]:
                    best = codex_limits(rl, at)
                break
    return best or {}


def agy_limits(data: dict, at_ms: int) -> dict:
    """`/usage` command data -> {windows: {bucket id: {used, resets_at, status, group, window}}}."""
    from sessions import parse_ts
    windows = {}
    for g in data.get("groups") or []:
        name = g.get("name") or ""
        group = _AGY_GROUPS.get(name, name.replace(" Models", "").replace(" models", ""))
        for b in g.get("buckets") or []:
            left = b.get("remaining_fraction")
            used = round((1 - left) * 100, 1) if isinstance(left, (int, float)) else None
            windows[b.get("id") or f"{group}-{b.get('window')}"] = {
                "used": used, "resets_at": parse_ts(b.get("reset_time")),
                "status": "rejected" if left == 0 else "allowed",
                "group": group, "window": b.get("window") or ""}
    return {"windows": windows, "at": at_ms}


def fetch_agy() -> tuple:
    """(limits or None, error)."""
    try:
        proc = subprocess.run(["agy", "-p", "/usage", "--output-format", "json", "--log-file", os.devnull],
                              cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=90)
        d = json.loads(proc.stdout or "{}")
    except FileNotFoundError:
        return None, "找不到 agy 命令"
    except subprocess.TimeoutExpired:
        return None, "agy 用量查询超时"
    except ValueError:
        return None, "agy 用量结果无法解析"
    data = (d.get("command") or {}).get("data") or {}
    if d.get("status") != "SUCCESS" or not data.get("groups"):
        return None, str(d.get("response") or d.get("error") or "agy 没有返回用量")[:200]
    return agy_limits(data, int(time.time() * 1000)), ""


# Antigravity models Jarvis picks from. data/models.json can override any of
# this (see README). Argon (agy's own default) is slow and has a small quota of its
# own, so it only runs when a task asks for it.
MODELS_FILE = DATA_DIR / "models.json"
_AGY_BUILTIN = {
    "default": {"model": "gemini-4-hydrogen-high", "label": "Hydrogen"},
    "fallback": "claude-sonnet-5-5-high",            # the Claude/GPT quota group
    "choices": {
        "argon": {"model": "gemini-4-argon-high", "label": "Argon"},
        "flash": {"model": "gemini-3.8-flash-high", "label": "Flash"},
        "sonnet": {"model": "claude-sonnet-5-5-high", "label": "Claude Sonnet"},
    },
    # families a run never goes below -high in, besides those of the models above
    "keep_high": ["claude-opus-5-5"],
}
_MODEL_ID = r"[a-z0-9][a-z0-9.\-]{2,60}"


def _agy_config(path=None) -> dict:
    """The built-in Antigravity models, with data/models.json's "antigravity" on top."""
    import re
    cfg = json.loads(json.dumps(_AGY_BUILTIN))
    try:
        own = json.loads(Path(path or MODELS_FILE).read_text("utf-8")).get("antigravity") or {}
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError, AttributeError) as e:
        print(f"[models] {path or MODELS_FILE} ignored: {e}")
        return cfg

    def entry(v, label=""):
        v = {"model": v, "label": label or v} if isinstance(v, str) else dict(v or {})
        if not re.fullmatch(_MODEL_ID, str(v.get("model") or "")):
            raise ValueError(f"bad model id: {v.get('model')!r}")
        v.setdefault("label", v["model"])
        return v
    try:
        if "default" in own:
            cfg["default"] = entry(own["default"])
        if "fallback" in own:
            cfg["fallback"] = entry(own["fallback"])["model"]
        if "choices" in own:
            cfg["choices"] = {str(k): entry(v) for k, v in (own["choices"] or {}).items()
                              if re.fullmatch(r"[a-z0-9_-]{1,30}", str(k))}
        if "keep_high" in own:
            cfg["keep_high"] = [str(x) for x in own["keep_high"] or []]
    except (ValueError, TypeError) as e:
        print(f"[models] {path or MODELS_FILE} ignored: {e}")
        return json.loads(json.dumps(_AGY_BUILTIN))
    return cfg


def _load_agy(cfg: dict):
    global AGY_DEFAULT, AGY_FALLBACK, AGY_CHOICES, AGY_MENU, _AGY_HIGH_FAMILIES
    AGY_DEFAULT = cfg["default"]["model"]
    AGY_FALLBACK = cfg["fallback"]
    AGY_CHOICES = {k: v["model"] for k, v in cfg["choices"].items()}
    # what the task box offers: "" is the default
    AGY_MENU = [{"value": "", "label": cfg["default"]["label"]}] + [
        {"value": k, "label": v["label"]} for k, v in cfg["choices"].items()]
    _AGY_HIGH_FAMILIES = {m[:-len("-high")] for m in [AGY_DEFAULT, AGY_FALLBACK, *AGY_CHOICES.values()]
                          if m.endswith("-high")} | set(cfg["keep_high"])


_load_agy(_agy_config())
_AGY_QUOTA_WORDS = ("quota", "credits balance", "out of credits", "resource_exhausted",
                    "resource exhausted", "rate limit")


def agy_model_id(choice: str) -> str:
    """'argon' / a full model id / '' -> the model id to run ('' -> the default)."""
    choice = (choice or "").strip()
    picked = choice.startswith("!")          # an effort you chose for the session: no floor
    choice = choice.lstrip("!")
    if choice in AGY_CHOICES:
        return AGY_CHOICES[choice]
    import re
    if not re.fullmatch(_MODEL_ID, choice) or "-" not in choice:   # a choice no longer offered too
        return AGY_DEFAULT
    m = re.fullmatch(r"(.+)-(?:low|medium)", choice)
    if not picked and m and m.group(1) in _AGY_HIGH_FAMILIES:
        return m.group(1) + "-high"                 # models with a high variant never go below it
    return choice


def agy_group(model: str) -> str:
    """Which /usage quota group a model draws from."""
    if "argon" in model:
        return "Argon"
    return "Gemini" if model.startswith("gemini") else "Claude/GPT"


def agy_chain(choice: str) -> list:
    """The model asked for, then the ones to fall back to, without repeats."""
    out = []
    for m in (agy_model_id(choice), AGY_DEFAULT, AGY_FALLBACK):
        if m not in out:
            out.append(m)
    return out


def agy_quota_error(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _AGY_QUOTA_WORDS)


class UsageTracker:
    def __init__(self, path: Path = USAGE_FILE, codex_home=CODEX_HOME):
        self.path = Path(path)
        self.codex_home = codex_home
        self.lock = threading.Lock()
        self.probing = False
        self.agy_busy = False
        self.agy_tried = 0
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False))
            os.replace(tmp, self.path)
        except OSError:
            pass

    def record_claude(self, info: dict, at_ms: int = None):
        if not isinstance(info, dict):
            return
        windows = claude_windows(info)
        if not windows:
            return
        with self.lock:
            prev = (self.data.get("claude") or {}).get("windows") or {}
            self.data["claude"] = {
                "windows": {**prev, **windows},     # a run reports only the windows in play
                "status": info.get("status") or "",
                "overage": info.get("overageStatus") or "",
                "at": at_ms or int(time.time() * 1000)}
            self._save()

    def refresh_agy(self) -> str:
        with self.lock:
            if self.agy_busy:
                return ""
            self.agy_busy = True
            self.agy_tried = int(time.time() * 1000)
        try:
            got, err = fetch_agy()
            if got:
                with self.lock:
                    self.data["antigravity"] = got
                    self._save()
            return err
        finally:
            self.agy_busy = False

    def snapshot(self, background: bool = True) -> dict:
        """Current numbers; Antigravity older than a few minutes is re-read in the background."""
        now = int(time.time() * 1000)
        with self.lock:
            claude, agy = self.data.get("claude"), self.data.get("antigravity")
            stale = (not agy or now - agy.get("at", 0) > AGY_TTL_MS) and now - self.agy_tried > AGY_TTL_MS
        if background and stale and not self.agy_busy:
            threading.Thread(target=self.refresh_agy, daemon=True).start()
        return {"claude": claude, "codex": latest_codex(self.codex_home) or None,
                "antigravity": agy, "probing": self.probing or self.agy_busy}

    def agy_exhausted(self, group: str) -> bool:
        """A quota group with a window used up (as of the last /usage read)."""
        with self.lock:
            windows = ((self.data.get("antigravity") or {}).get("windows") or {}).values()
            return any(w.get("group") == group and (w.get("status") == "rejected" or (w.get("used") or 0) >= 100)
                       for w in windows)

    def agy_models(self, choice: str) -> list:
        """Models to try in order: the chain with used-up groups moved to the back."""
        chain = agy_chain(choice)
        return sorted(chain, key=lambda m: self.agy_exhausted(agy_group(m)))

    def probe_claude(self) -> str:
        """One-word Haiku turn, not saved as a session; returns an error or ""."""
        with self.lock:
            if self.probing:
                return ""
            self.probing = True
        try:
            proc = subprocess.run(
                ["claude", "-p", "Reply with: ok", "--model", "haiku", "--effort", "low",
                 "--output-format", "stream-json", "--verbose", "--no-session-persistence",
                 "--tools", ""],
                cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=PROBE_TIMEOUT)
            got, err = False, ""
            for line in proc.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "rate_limit_event":
                    self.record_claude(ev.get("rate_limit_info") or {})
                    got = True
                elif ev.get("type") == "result" and ev.get("is_error"):
                    err = str(ev.get("result") or "error")[:300]
            if not got and not err:
                err = (proc.stderr or "").strip()[-300:] or f"退出码 {proc.returncode}"
            return "" if got else err
        except FileNotFoundError:
            return "找不到 claude 命令"
        except subprocess.TimeoutExpired:
            return "刷新超时"
        finally:
            self.probing = False


tracker = UsageTracker()
