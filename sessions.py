"""Passive index of local Claude Code sessions.

Reads (never writes) two sources:
- ~/.claude/projects/<dir>/<sid>.jsonl            session logs (+ <sid>/subagents/agent-*.jsonl)
- ~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json
                                                  Claude desktop manifest (title, archived)

Logs are read incrementally by byte offset; per-file aggregates are cached in
data/session_index.json so a restart only reads what was appended since.
"""

import glob
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
DESKTOP_SESSIONS = HOME / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
CODEX_HOME = HOME / ".codex"
# Antigravity: the IDE and the `agy` CLI each keep their own summaries
AGY_DIRS = (HOME / ".gemini" / "antigravity", HOME / ".gemini" / "antigravity-cli")

INDEX_VERSION = 12
WINDOW_DAYS = 14                    # how far back the index keeps data
GAP_MS = 5 * 60 * 1000              # events closer than this form one working span
MIN_SEG_MS = 60 * 1000              # a span with a single event still counts as a minute
LIVE_STALE_MS = 90 * 1000           # "running" with no new event this long reads as quiet
QUIET_WINDOW_MS = 30 * 60 * 1000    # quiet sessions older than this drop out of "now"
TOOL_MAX_MS = 30 * 60 * 1000        # a tool call in flight counts as running for at most this long
BASH_DEFAULT_MS = 2 * 60 * 1000     # Bash's own default timeout
WAIT_WINDOW_MS = 3 * 3600 * 1000    # finished turns older than this are idle, not "waiting"

# Cheap byte pre-filter before json.loads (most lines are attachments / request blobs).
_WANT = (b'"assistant"', b'"user"', b'"custom-title"', b'"api_error"', b'<task-notification>')
_LIMIT_RE = re.compile(r"rate.?limit|usage limit|limit reached|quota|out of credits", re.I)
_SKIP_PROMPT = ("<command-", "<local-command", "<system-reminder", "<task-notification",
                "Caveat:", "<bash-", "<user-memory", "[SYSTEM")


def parse_ts(s) -> int:
    """ISO-8601 timestamp → epoch ms (0 if unparseable)."""
    if not s or not isinstance(s, str):
        return 0
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def step_text(name: str, inp) -> str:
    """Short readable description of a tool call."""
    inp = inp if isinstance(inp, dict) else {}
    base = lambda p: os.path.basename(str(p or "").rstrip("/"))
    if name == "Bash":
        return ("$ " + " ".join(str(inp.get("command", "")).split()))[:60]
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"改 {base(inp.get('file_path') or inp.get('notebook_path'))}"
    if name == "Read":
        return f"读 {base(inp.get('file_path'))}"
    if name in ("Grep", "Glob"):
        return f"搜 {str(inp.get('pattern', ''))[:40]}"
    if name in ("Task", "Agent"):
        return f"派子代理 {str(inp.get('description', ''))[:30]}"
    if name.startswith("WebSearch"):
        return f"搜网页 {str(inp.get('query', ''))[:30]}"
    if name.startswith("WebFetch"):
        return "读网页"
    if name == "Skill":
        return f"用技能 {inp.get('skill', '')}"
    if name in ("TodoWrite", "TaskCreate", "TaskUpdate"):
        return "整理待办"
    return name.split("__")[-1] if name.startswith("mcp__") else name


def todo_of(pairs) -> dict:
    """[(status, text)] → {done, total, cur, items}: the agent's own checklist
    (Claude TodoWrite / TaskCreate, Codex update_plan). Ported from Agent Deck."""
    pairs = [(str(st or "pending"), " ".join(str(tx or "").split())) for st, tx in pairs or []]
    pairs = [(st, tx) for st, tx in pairs if st != "deleted"]
    if not pairs:
        return None
    done = sum(1 for st, _ in pairs if st in ("completed", "done"))
    cur = next((tx for st, tx in pairs if st == "in_progress"), "") or \
        next((tx for st, tx in pairs if st == "pending"), "")
    return {"done": done, "total": len(pairs), "cur": "" if done >= len(pairs) else cur[:60],
            "items": [{"t": tx[:120], "s": st} for st, tx in pairs[:30]]}


def _apply_task_tool(entry: dict, name: str, inp: dict):
    """Claude's newer TaskCreate / TaskUpdate tools: ids are assigned in order."""
    tk = entry.setdefault("tk", {})
    if name == "TaskCreate":
        entry["tk_seq"] = entry.get("tk_seq", 0) + 1
        tid = str(inp.get("taskId") or inp.get("id") or entry["tk_seq"])
        tk[tid] = [str(inp.get("subject") or inp.get("title") or inp.get("description") or f"#{tid}"),
                   str(inp.get("activeForm") or ""), str(inp.get("status") or "pending")]
    else:
        tid = str(inp.get("taskId") or inp.get("id") or "")
        if not tid:
            return
        cur = tk.setdefault(tid, [f"#{tid}", "", "pending"])
        if inp.get("status"):
            cur[2] = str(inp["status"])
        if inp.get("subject") or inp.get("title"):
            cur[0] = str(inp.get("subject") or inp.get("title"))
        if inp.get("activeForm"):
            cur[1] = str(inp["activeForm"])
    entry["todo"] = todo_of([(st, act if st == "in_progress" and act else subj)
                             for subj, act, st in tk.values()])


_PLAN_OBJ = re.compile(r"\{([^{}]*)\}")
_PLAN_STEP = re.compile(r'"?step"?\s*:\s*"((?:[^"\\]|\\.)*)"')
_PLAN_STATUS = re.compile(r'"?status"?\s*:\s*"(\w+)"')


def codex_plan(raw) -> dict:
    """Codex update_plan arguments, either JSON or code-mode
    `tools.update_plan({plan:[{step:"…","status":"…"}]})`."""
    raw = str(raw or "")
    i = raw.find("update_plan")
    body = raw[i:] if i >= 0 else raw
    pairs = []
    for m in _PLAN_OBJ.finditer(body[body.find("[") + 1:] if "[" in body else body):
        st, sp = _PLAN_STATUS.search(m.group(1)), _PLAN_STEP.search(m.group(1))
        if st and sp:
            try:
                text = json.loads(f'"{sp.group(1)}"')
            except ValueError:
                text = sp.group(1)
            pairs.append((st.group(1), text))
    return todo_of(pairs)


_MCP_TASK_TOOLS = ("complete_task", "log_progress", "fail_task", "submit_plan")


def _note_mcp_task(entry: dict, inp: dict):
    """A Jarvis task worked on from this (interactive) session via MCP."""
    try:
        ref = [int(inp.get("project_id") or 0), int(inp["task_id"])]
    except (KeyError, TypeError, ValueError):
        return
    mcp = entry.setdefault("mcp", [])
    if ref not in mcp:
        mcp.append(ref)
        del mcp[:-50]


def _new_entry(sid: str, parent: bool, agent: str = "claude") -> dict:
    return {
        "agent": agent, "codex_tot": {},
        "sid": sid, "main": not parent, "offset": 0, "size": 0, "mtime": 0.0,
        "cwd": "", "entry": "", "title": "", "prompt": "", "model": "",
        "first_at": 0, "last_at": 0, "segs": [], "days": {},
        "kind": "", "step": "", "err": "", "last_text": "", "last_msg": "",
        "todo": None, "todo_at": 0, "tk": {}, "tk_seq": 0,
        "cwd0": "", "mcp": [], "turns": 0, "entry0": "",
        "ctx": 0, "ctx_max": 0,       # tokens the latest call read (its context), and the window
        "bg": {},                     # background tasks still running: tool_use_id -> [started ms, task id]
        "bg_reads": {},               # TaskOutput calls waiting for their result: tool_use_id -> task id
        "busy_until": 0,              # a tool call is in flight: running (not quiet) until this ms
        # did the latest turn leave something for you? (see asks_you)
        "wrap": None, "q": False, "tail": "",
    }


def _add_seg(segs: list, ts: int):
    if segs and segs[-1][0] - GAP_MS <= ts <= segs[-1][1] + GAP_MS:
        segs[-1][0] = min(segs[-1][0], ts)
        segs[-1][1] = max(segs[-1][1], ts)
    else:
        segs.append([ts, ts])


def _day(entry: dict, ts: int) -> dict:
    k = day_key(ts)
    d = entry["days"].get(k)
    if d is None:
        d = entry["days"][k] = {"out": 0, "fresh": 0, "cached": 0, "calls": 0, "prompts": 0}
    return d


_BG_STARTED = ("Command running in background with ID", "Async agent launched successfully",
               "Monitor started")
_BG_ID_RE = re.compile(r"with ID: ([A-Za-z0-9_-]+)")
_BG_TOOL_RE = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")
_BG_TASK_RE = re.compile(r"<task-id>([^<]+)</task-id>")
BG_MAX_MS = 6 * 3600 * 1000     # past this, a background task that never reported back is gone


_BG_FINISHED_RE = re.compile(r'"status"\s*:\s*"(completed|failed|killed)"|<status>(completed|failed|killed)</status>'
                             r'|<retrieval_status>(success|completed)</retrieval_status>')


def _result_text(content) -> str:
    return content if isinstance(content, str) else " ".join(
        x.get("text", "") for x in (content or []) if isinstance(x, dict))


def _bg_forget(entry: dict, task_id: str):
    """Stopped (TaskStop) or read to the end (TaskOutput): no notification will come."""
    if not task_id:
        return
    bg = entry.get("bg") or {}
    for k in [k for k, v in bg.items() if v[1] == task_id]:
        bg.pop(k, None)


def _bg_started(entry: dict, tool_use_id: str, content, ts: int):
    """A tool result saying the work goes on in the background (Bash run_in_background,
    a background agent, Monitor): the session will be woken when it reports back."""
    text = _result_text(content)
    if not any(m in text[:400] for m in _BG_STARTED):
        return
    m = _BG_ID_RE.search(text[:400])
    entry.setdefault("bg", {})[tool_use_id] = [ts, m.group(1) if m else ""]


def _bg_done(entry: dict, text: str):
    """A <task-notification>: the background task it names has finished."""
    if "<task-notification>" not in text:
        return
    bg = entry.get("bg") or {}
    tools, tasks = set(_BG_TOOL_RE.findall(text)), set(_BG_TASK_RE.findall(text))
    for k in [k for k, v in bg.items() if k in tools or (v[1] and v[1] in tasks)]:
        bg.pop(k, None)


def background_running(s: dict, now_ms: int) -> int:
    """How many background tasks the session is still waiting on."""
    return sum(1 for v in (s.get("bg") or {}).values() if now_ms - v[0] <= BG_MAX_MS)


def bg_waiting(s: dict, now_ms: int) -> int:
    """Active background tasks holding an otherwise-ended turn open."""
    if not s or s.get("kind") != "end":
        return 0
    if s.get("bg_wait"):
        return int(s["bg_wait"])
    age = now_ms - (s.get("last_at") or 0)
    return background_running(s, now_ms) if age <= BG_MAX_MS else 0


def ingest_line(entry: dict, d: dict, cutoff_ms: int):
    """Fold one parsed jsonl record into a file entry."""
    t = d.get("type")
    if t == "custom-title":
        if d.get("customTitle"):
            entry["title"] = str(d["customTitle"])[:120]
        return
    if t == "queue-operation" and d.get("operation") == "enqueue":
        _bg_done(entry, str(d.get("content") or ""))      # a background task reported back
        return
    if d.get("isSidechain"):
        return
    ts = parse_ts(d.get("timestamp"))
    if not ts:
        return
    entry["cwd"] = d.get("cwd") or entry["cwd"]
    if not entry.get("cwd0") and d.get("cwd"):
        entry["cwd0"] = d["cwd"]           # where the session was started (resume needs it)
    entry["entry"] = d.get("entrypoint") or entry["entry"]
    if not entry.get("entry0") and d.get("entrypoint"):
        entry["entry0"] = d["entrypoint"]   # how it was started (a panel reply later says sdk-cli)

    if t == "system":
        if d.get("subtype") == "api_error" and entry["main"]:
            err = d.get("error") or {}
            msg = err.get("formatted") or err.get("message") or "API error"
            entry["err"] = str(msg)[:160]
            entry["kind"] = "limit" if _LIMIT_RE.search(json.dumps(err)) else "error"
            entry["last_at"] = max(entry["last_at"], ts)
        return
    if t not in ("assistant", "user"):
        return

    in_window = ts >= cutoff_ms
    if in_window:
        _add_seg(entry["segs"], ts)
        entry["first_at"] = min(entry["first_at"] or ts, ts)
    entry["last_at"] = max(entry["last_at"], ts)
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}

    if t == "assistant":
        entry["model"] = msg.get("model") or entry["model"]
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for b in content:
            if b.get("type") == "tool_use":
                name, inp = b.get("name", ""), b.get("input") if isinstance(b.get("input"), dict) else {}
                entry["step"] = step_text(name, inp)
                entry["busy_until"] = ts + tool_budget_ms(name, inp)
                if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                    entry["todo"] = todo_of([(t.get("status"), t.get("activeForm") if t.get("status") == "in_progress"
                                              and t.get("activeForm") else t.get("content"))
                                             for t in inp["todos"] if isinstance(t, dict)])
                    entry["todo_at"] = ts
                elif name in ("TaskCreate", "TaskUpdate"):
                    _apply_task_tool(entry, name, inp)
                    entry["todo_at"] = ts
                elif name.startswith("mcp__task-manager__") and name.split("__")[-1] in _MCP_TASK_TOOLS:
                    _note_mcp_task(entry, inp)
                elif name.endswith("__wrap_up") or name == "wrap_up":
                    # the turn's closing box: a question or "blocked" means it waits on you
                    entry["wrap"] = bool(inp.get("ask") or inp.get("blocked"))
                elif name in ("AskUserQuestion", "ExitPlanMode"):
                    entry["q"] = True
                elif name in ("TaskStop", "KillShell", "KillBash"):
                    _bg_forget(entry, inp.get("task_id") or inp.get("shell_id") or inp.get("bash_id"))
                elif name in ("TaskOutput", "BashOutput"):
                    entry.setdefault("bg_reads", {})[b.get("id", "")] = inp.get("task_id") or inp.get("bash_id") or ""
            elif b.get("type") == "text" and b.get("text") and not (
                    msg.get("model") == "<synthetic>" and b["text"].strip() == "No response requested."):
                entry["last_text"] = " ".join(b["text"].split())[:200]
                entry["tail"] = b["text"].strip()[-60:]
        # One API message is split across several records that all repeat the
        # same usage, so count it once per message id.
        mid = msg.get("id") or ""
        u = msg.get("usage")
        if u and mid and mid != entry["last_msg"]:
            entry["ctx"] = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                            + (u.get("cache_creation_input_tokens") or 0))
        if u and mid and mid != entry["last_msg"] and in_window:
            day = _day(entry, ts)
            day["out"] += u.get("output_tokens") or 0
            day["fresh"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            day["cached"] += u.get("cache_read_input_tokens") or 0
            day["calls"] += 1
        if mid:
            entry["last_msg"] = mid
        if d.get("isApiErrorMessage"):
            entry["kind"] = "limit" if _LIMIT_RE.search(entry["last_text"]) else "error"
            entry["err"] = entry["last_text"][:160]
        else:
            stop = msg.get("stop_reason")
            entry["kind"] = "end" if stop in ("end_turn", "stop_sequence") else "run"
            entry["err"] = ""
        return

    # user
    c = msg.get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        entry["kind"] = "run"
        entry["busy_until"] = 0             # the tool came back
        entry["q"] = False                  # the question was answered
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                _bg_started(entry, b["tool_use_id"], b.get("content"), ts)
                read = (entry.get("bg_reads") or {}).pop(b["tool_use_id"], None)
                if read and _BG_FINISHED_RE.search(_result_text(b.get("content"))[:2000]):
                    _bg_forget(entry, read)     # it read the finished output: no notification follows
        return
    text = c if isinstance(c, str) else " ".join(
        b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")
    text = text.strip()
    if not text:
        return
    if text.startswith("[Request interrupted"):
        entry["kind"] = "end"
        return
    if text.startswith("<task-notification>"):
        _bg_done(entry, text)
    entry["kind"] = "run"
    if text.startswith(_SKIP_PROMPT):
        return
    todo = entry.get("todo")
    if todo and todo["done"] >= todo["total"]:
        entry["todo"], entry["tk"] = None, {}      # finished list; a new request starts fresh
    entry["turns"] = entry.get("turns", 0) + 1      # all-time, not just the window
    entry["wrap"], entry["q"], entry["tail"] = None, False, ""
    if in_window:
        _day(entry, ts)["prompts"] += 1
    if not entry["prompt"]:
        entry["prompt"] = " ".join(text.split())[:120]


_CODEX_SKIP = ("<environment_context", "<user_instructions", "<permissions", "# AGENTS.md",
               "<skills_instructions", "<turn_aborted", "<subagent")
_CODEX_WANT = (b'"session_meta"', b'"turn_context"', b'"event_msg"', b'"response_item"')


def _codex_step(p: dict) -> str:
    name = p.get("name") or ""
    raw = p.get("arguments") or p.get("input") or ""
    if isinstance(raw, str) and "tools." in raw:
        # code-mode: `text(await tools.exec_command({cmd:"..."}))` / tools.apply_patch("...")
        calls = re.findall(r"tools\.(\w+)\(", raw)
        tool = calls[0] if calls else name
        if tool == "apply_patch":
            m = re.search(r"\*\*\* (?:Update|Add|Delete) File: ([^\\\n\"]+)", raw)
            return f"改 {os.path.basename(m.group(1).strip())}" if m else "改文件"
        if tool == "exec_command":
            m = re.search(r"cmd\s*:\s*\"((?:[^\"\\]|\\.)*)\"", raw)
            cmd = m.group(1).replace("\\n", "; ").replace('\\"', '"') if m else ""
            return ("$ " + " ".join(cmd.split()))[:60]
        if tool == "write_stdin":
            return "向命令输入"
        if tool == "update_plan":
            return "整理待办"
        return tool
    args = {}
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except ValueError:
            args = {"cmd": raw}
    elif isinstance(raw, dict):
        args = raw
    cmd = args.get("cmd") or args.get("command")
    if isinstance(cmd, list):
        cmd = " ".join(map(str, cmd))
    if name in ("shell", "exec_command", "local_shell", "shell_command") or (cmd and name != "apply_patch"):
        return ("$ " + " ".join(str(cmd or "").split()))[:60]
    if name == "apply_patch" or "*** Begin Patch" in str(raw):
        m = re.search(r"\*\*\* (?:Update|Add) File: (\S+)", str(raw))
        return f"改 {os.path.basename(m.group(1))}" if m else "改文件"
    return name or "工具"


def ingest_codex_line(entry: dict, d: dict, cutoff_ms: int):
    """Fold one Codex rollout record (~/.codex/sessions/**/rollout-*.jsonl) into an entry."""
    t, p = d.get("type"), d.get("payload") or {}
    if t == "session_meta":
        entry["sid"] = p.get("session_id") or p.get("id") or entry["sid"]
        entry["main"] = True
        entry["cwd"] = p.get("cwd") or entry["cwd"]
        entry["entry"] = p.get("originator") or entry["entry"]
        return
    if t == "turn_context":
        entry["model"] = p.get("model") or entry["model"]
        entry["cwd"] = p.get("cwd") or entry["cwd"]
        return
    ts = parse_ts(d.get("timestamp"))
    if not ts or t not in ("event_msg", "response_item"):
        return
    pt = p.get("type")
    in_window = ts >= cutoff_ms
    if pt == "token_count":
        info = p.get("info") or {}
        last = info.get("last_token_usage") or {}
        if last.get("input_tokens"):
            entry["ctx"] = last["input_tokens"]
        if info.get("model_context_window"):
            entry["ctx_max"] = info["model_context_window"]
        tot = info.get("total_token_usage") or {}
        prev = entry["codex_tot"]
        if tot and in_window:
            day = _day(entry, ts)
            out = (tot.get("output_tokens") or 0) - prev.get("output_tokens", 0)
            inp = ((tot.get("input_tokens") or 0) - (tot.get("cached_input_tokens") or 0)) \
                - (prev.get("input_tokens", 0) - prev.get("cached_input_tokens", 0))
            cached = (tot.get("cached_input_tokens") or 0) - prev.get("cached_input_tokens", 0)
            day["out"] += max(out, 0)
            day["fresh"] += max(inp, 0)
            day["cached"] += max(cached, 0)
            day["calls"] += 1
        if tot:
            entry["codex_tot"] = {k: v for k, v in tot.items() if isinstance(v, (int, float))}
        return
    if in_window:
        _add_seg(entry["segs"], ts)
        entry["first_at"] = min(entry["first_at"] or ts, ts)
    entry["last_at"] = max(entry["last_at"], ts)
    if pt == "task_started":
        entry["kind"] = "run"
    elif pt in ("task_complete", "turn_aborted"):
        entry["kind"] = "end"
        if p.get("last_agent_message"):
            entry["last_text"] = " ".join(str(p["last_agent_message"]).split())[:200]
    elif pt == "error":
        entry["kind"] = "limit" if _LIMIT_RE.search(str(p.get("message"))) else "error"
        entry["err"] = str(p.get("message", ""))[:160]
    elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
        entry["step"] = _codex_step(p)
        entry["kind"] = "run"
        raw = p.get("arguments") or p.get("input") or ""
        if p.get("name") == "update_plan" or "update_plan" in str(raw):
            plan = codex_plan(raw)
            if plan:
                entry["todo"], entry["todo_at"] = plan, ts
    elif pt == "message" and p.get("role") == "user":
        text = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict)).strip()
        if text and not text.startswith(_CODEX_SKIP):
            entry["kind"] = "run"
            entry["turns"] = entry.get("turns", 0) + 1
            entry["wrap"], entry["q"], entry["tail"] = None, False, ""
            if in_window:
                _day(entry, ts)["prompts"] += 1
            if not entry["prompt"]:
                entry["prompt"] = " ".join(text.split())[:120]
    elif pt == "message" and p.get("role") == "assistant":
        text = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict)).strip()
        if text:
            entry["last_text"] = " ".join(text.split())[:200]
            entry["tail"] = text[-60:]


def _file_sid(path: str):
    """(session id, is_subagent_file) from a log path."""
    p = Path(path)
    if p.parent.name == "subagents":
        return p.parent.parent.name, True
    return p.stem, False


class SessionIndex:
    def __init__(self, index_file: Path, projects_dir: Path = CLAUDE_PROJECTS,
                 desktop_dir: Path = DESKTOP_SESSIONS, codex_home: Path = CODEX_HOME,
                 agy_dirs=AGY_DIRS):
        self.index_file = Path(index_file)
        self.projects_dir = Path(projects_dir)
        self.desktop_dir = Path(desktop_dir)
        self.codex_home = Path(codex_home)
        self.agy_dirs = [Path(d) for d in agy_dirs]
        self.lock = threading.Lock()
        self.files: dict = {}
        self.manifest: dict = {}      # cli session id -> desktop info
        self._manifest_mtimes: dict = {}
        self.codex_meta: dict = {}    # thread id -> {title, archived}
        self.agy: list = []           # Antigravity conversation summaries
        self._side_mtimes: dict = {}
        self.scanning = False
        self.last_scan_at = 0
        self._load()

    # ── persistence ──
    def _load(self):
        try:
            data = json.loads(self.index_file.read_text("utf-8"))
            if data.get("version") == INDEX_VERSION:
                self.files = data.get("files", {})
        except (OSError, ValueError):
            self.files = {}

    def _save(self):
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": INDEX_VERSION, "files": self.files},
                                  ensure_ascii=False), "utf-8")
        os.replace(tmp, self.index_file)

    # ── scanning ──
    def _log_paths(self):
        """(path, agent) for every log file that may hold sessions."""
        root, cx = str(self.projects_dir), str(self.codex_home)
        claude = glob.glob(f"{root}/*/*.jsonl") + glob.glob(f"{root}/*/*/subagents/*.jsonl")
        codex = (glob.glob(f"{cx}/sessions/**/rollout-*.jsonl", recursive=True)
                 + glob.glob(f"{cx}/archived_sessions/*.jsonl"))
        return [(p, "claude") for p in claude] + [(p, "codex") for p in codex]

    def _read_file(self, path: str, entry: dict, cutoff_ms: int):
        codex = entry.get("agent") == "codex"
        want, ingest = (_CODEX_WANT, ingest_codex_line) if codex else (_WANT, ingest_line)
        with open(path, "rb") as f:
            f.seek(entry["offset"])
            for raw in f:
                if not raw.endswith(b"\n"):
                    break           # partial line still being written
                entry["offset"] += len(raw)
                if not any(w in raw for w in want):
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    ingest(entry, d, cutoff_ms)

    def _changed(self, path) -> bool:
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return False
        if self._side_mtimes.get(str(path)) == mt:
            return False
        self._side_mtimes[str(path)] = mt
        return True

    def _scan_codex_meta(self) -> bool:
        """Thread titles (session_index.jsonl) and archived flags (state_*.sqlite)."""
        changed = False
        idx = self.codex_home / "session_index.jsonl"
        if self._changed(idx):
            meta = dict(self.codex_meta)
            for line in idx.read_text("utf-8", errors="ignore").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("id"):
                    meta.setdefault(r["id"], {})["title"] = r.get("thread_name") or ""
            self.codex_meta = meta
            changed = True
        dbs = sorted(glob.glob(str(self.codex_home / "state_*.sqlite")))
        if dbs and self._changed(dbs[-1]):
            rows = _sqlite_rows(dbs[-1], "select id, title, archived from threads")
            meta = dict(self.codex_meta)
            for tid, title, archived in rows:
                m = meta.setdefault(tid, {})
                m["archived"] = bool(archived)
                m.setdefault("title", title or "")
            self.codex_meta = meta
            changed = True
        return changed

    def _scan_agy(self, cutoff_ms: int) -> bool:
        """Antigravity keeps only summaries we can read (steps are protobuf).
        The CLI leaves titles empty; its cache has the first instruction."""
        watch = [d / f for d in self.agy_dirs for f in
                 ("conversation_summaries.db", "conversation_summaries.db-wal",
                  "cache/conversation_metadata.json")]
        if not any([self._changed(p) for p in watch]):     # list: check (and record) every file
            return False
        best: dict = {}
        ours = _agy_started_here()
        for d in self.agy_dirs:
            previews = _agy_previews(d / "cache" / "conversation_metadata.json")
            rows = _sqlite_rows(d / "conversation_summaries.db",
                                "select conversation_id, title, preview, workspace_uris, "
                                "status, last_modified_time, step_count from conversation_summaries")
            for cid, title, preview, uris, status, mod, steps in rows:
                last = parse_ts(str(mod).replace(" ", "T", 1)) if mod else 0
                if last < cutoff_ms or (cid in best and best[cid]["last_at"] >= last):
                    continue
                try:
                    ws = json.loads(uris or "[]")
                except ValueError:
                    ws = []
                cwd = (ws[0] if ws else "").replace("file://", "")
                preview = preview or previews.get(cid, "")
                best[cid] = {"agent": "antigravity", "sid": cid,
                             "title": (title or preview or "（无标题）")[:200],
                             "cwd": cwd, "origin": cwd, "last_at": last, "first_at": last, "segs": [],
                             "days": {}, "kind": "run" if "RUNNING" in str(status) else "end",
                             "step": "", "err": "", "last_text": (preview or "")[:200], "model": "",
                             "entry": "antigravity", "entry0": "agy-print" if cid in ours else "antigravity",
                             "local_id": "", "archived": False,
                             "awaiting": False, "steps": steps or 0, "turns": None,
                             "ctx": _agy_total_input(cid), "ctx_max": 0}
        self.agy = list(best.values())
        return True

    def _scan_manifest(self) -> bool:
        changed = False
        seen = set()
        for path in glob.glob(f"{self.desktop_dir}/**/local_*.json", recursive=True):
            seen.add(path)
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            if self._manifest_mtimes.get(path) == mt:
                continue
            try:
                d = json.loads(Path(path).read_text("utf-8"))
            except (OSError, ValueError):
                continue
            self._manifest_mtimes[path] = mt
            cli = d.get("cliSessionId")
            if not cli:
                continue
            self.manifest[cli] = {
                "local_id": d.get("sessionId", ""), "title": d.get("title") or "",
                "archived": d.get("isArchived") is True,
                "cwd": d.get("originCwd") or d.get("cwd") or "",
                "last_at": d.get("lastActivityAt") or 0,
                "awaiting": bool(d.get("interruptedAwaitingPerson")),
            }
            changed = True
        for gone in set(self._manifest_mtimes) - seen:
            self._manifest_mtimes.pop(gone, None)
        return changed

    def scan(self) -> bool:
        """Read whatever was appended since the last scan. Returns True if anything changed."""
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - WINDOW_DAYS * 86400 * 1000
        changed = False
        self.scanning = True
        try:
            live = set()
            for path, agent in self._log_paths():
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                # Older sessions stay listed (their logs can still be read and
                # resumed); only their subagent logs, which add nothing to a list, are skipped.
                if st.st_mtime * 1000 < cutoff_ms and "/subagents/" in path:
                    continue
                live.add(path)
                with self.lock:
                    old = self.files.get(path)
                if old and old["size"] == st.st_size and old["mtime"] == st.st_mtime:
                    continue
                if agent == "codex":
                    sid, is_sub = Path(path).stem[-36:], False
                else:
                    sid, is_sub = _file_sid(path)
                entry = (dict(old) if old and st.st_size >= old["offset"]
                         else _new_entry(sid, is_sub, agent))
                entry["segs"] = [list(s) for s in entry["segs"]]
                entry["days"] = {k: dict(v) for k, v in entry["days"].items()}
                entry["tk"] = {k: list(v) for k, v in (entry.get("tk") or {}).items()}
                entry["mcp"] = [list(x) for x in entry.get("mcp") or []]
                try:
                    self._read_file(path, entry, cutoff_ms)
                except OSError:
                    continue
                entry["size"], entry["mtime"] = st.st_size, st.st_mtime
                self._trim(entry, cutoff_ms)
                with self.lock:
                    self.files[path] = entry
                changed = True
            with self.lock:
                for gone in set(self.files) - live:
                    del self.files[gone]
                    changed = True
            changed = self._scan_manifest() or changed
            changed = self._scan_codex_meta() or changed
            changed = self._scan_agy(cutoff_ms) or changed
            if changed:
                self._save()
        finally:
            self.scanning = False
            self.last_scan_at = now_ms
        return changed

    @staticmethod
    def _trim(entry: dict, cutoff_ms: int):
        entry["segs"] = [s for s in entry["segs"] if s[1] >= cutoff_ms]
        cut_day = day_key(cutoff_ms)
        entry["days"] = {k: v for k, v in entry["days"].items() if k >= cut_day}

    # ── read model ──
    def sessions(self) -> list:
        """Merge file entries into one record per session id."""
        with self.lock:
            files = list(self.files.items())
            manifest = dict(self.manifest)
            codex_meta = dict(self.codex_meta)
            agy = [dict(a) for a in self.agy]
        by_sid: dict = {}
        for path, e in files:
            s = by_sid.get(e["sid"])
            if s is None:
                s = by_sid[e["sid"]] = {
                    "agent": e.get("agent", "claude"),
                    "sid": e["sid"], "cwd": "", "title": "", "prompt": "", "model": "",
                    "first_at": 0, "last_at": 0, "segs": [], "days": {}, "kind": "",
                    "step": "", "err": "", "last_text": "", "entry": "", "has_main": False,
                    "todo": None, "todo_at": 0, "cwd0": "", "mcp": [], "path": "", "turns": 0,
                    "entry0": "", "asks": False, "ctx": 0, "ctx_max": 0, "bg": {}}
            for k, v in e["days"].items():
                d = s["days"].setdefault(k, {"out": 0, "fresh": 0, "cached": 0, "calls": 0, "prompts": 0})
                for kk in d:
                    d[kk] += v.get(kk, 0)
            if e["main"]:
                newer = e["last_at"] >= s["last_at"]
                s["has_main"] = True
                for k in ("cwd", "title", "prompt", "model", "first_at"):
                    s[k] = e[k] or s[k]
                if newer:
                    for k in ("kind", "step", "err", "last_text", "entry"):
                        s[k] = e[k] or s[k]
                    s["ctx"], s["ctx_max"] = e.get("ctx", 0) or s["ctx"], e.get("ctx_max", 0) or s["ctx_max"]
                    s["bg"] = dict(e.get("bg") or {})
                    s["busy_until"] = e.get("busy_until", 0)
                    s["path"] = path
                    s["asks"] = asks_you(e)
                s["segs"] = [list(x) for x in e["segs"]] if newer else s["segs"]
                s["last_at"] = max(s["last_at"], e["last_at"])
                if e.get("todo") and e.get("todo_at", 0) >= s.get("todo_at", 0):
                    s["todo"], s["todo_at"] = e["todo"], e.get("todo_at", 0)
                s["cwd0"] = s["cwd0"] or e.get("cwd0")
                s["mcp"] = e.get("mcp") or s["mcp"]
                s["turns"] = max(s["turns"], e.get("turns", 0))
                s["entry0"] = s["entry0"] or e.get("entry0")
        out = []
        now_ms = int(time.time() * 1000)
        from hidden import archived_sessions
        ours = archived_sessions.snapshot()          # archived from Jarvis's panel
        for s in by_sid.values():
            if not s["has_main"]:
                continue
            n_bg = background_running(s, now_ms)
            if n_bg and s["kind"] == "end":
                s["step"] = f"等后台任务（{n_bg} 个）"
            s["bg_wait"] = n_bg if (n_bg and s["kind"] == "end") else 0
            if s["agent"] == "codex":
                m = codex_meta.get(s["sid"]) or {}
                s.update(local_id="", archived=m.get("archived", False) or _still_archived(ours, s),
                         awaiting=False, origin=s["cwd"])
                s["title"] = m.get("title") or s["prompt"] or "（无标题）"
                out.append(s)
                continue
            m = manifest.get(s["sid"]) or {}
            s["local_id"] = m.get("local_id", "")
            s["archived"] = m.get("archived", False) or _still_archived(ours, s)
            s["awaiting"] = m.get("awaiting", False)
            s["title"] = m.get("title") or s["title"] or s["prompt"] or "（无标题）"
            # The log's cwd follows `cd` into subfolders; the desktop manifest
            # keeps the folder the session was opened in.
            s["origin"] = m.get("cwd") or s["cwd0"] or s["cwd"]
            out.append(s)
        for a in agy:
            a["archived"] = a.get("archived") or _still_archived(ours, a)
        return out + agy


# How long a conversation has grown, by executor: (worth wrapping up, time to start over).
# Claude / agy in tokens; Codex as a share of its context window.
CTX_LEVELS = {"claude": (300_000, 600_000), "codex": (0.6, 0.8), "antigravity": (1_000_000, 2_000_000)}


def _still_archived(archived: dict, s: dict) -> bool:
    """Archived from the panel, and nothing happened in the session since (a minute
    of slack for the turn that was finishing when it was archived)."""
    at = archived.get(s.get("sid"))
    return bool(at) and (s.get("last_at") or 0) <= at + 60_000


def context_level(s: dict) -> int:
    """0 fine, 1 worth wrapping up, 2 time for a new session."""
    ctx = s.get("ctx") or 0
    if not ctx:
        return 0
    agent = s.get("agent") or "claude"
    lo, hi = CTX_LEVELS.get(agent, CTX_LEVELS["claude"])
    if agent == "codex":
        if not s.get("ctx_max"):
            lo, hi = CTX_LEVELS["claude"]
        else:
            ctx = ctx / s["ctx_max"]
    return 2 if ctx >= hi else 1 if ctx >= lo else 0


def _agy_total_input(cid: str) -> int:
    """agy reports input tokens summed over the whole conversation (its `result`
    events, in the log Jarvis keeps); 0 when we never ran it."""
    from config import DATA_DIR
    path = DATA_DIR / "agy_logs" / f"{cid}.jsonl"
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - (2 << 20)))
            lines = f.read().split(b"\n")
    except OSError:
        return 0
    for raw in reversed(lines):
        if b'"event": "result"' in raw:
            try:
                return int(((json.loads(raw).get("result") or {}).get("usage") or {}).get("input_tokens") or 0)
            except (ValueError, TypeError):
                continue
    return 0


def _agy_started_here() -> set:
    """Antigravity conversations Jarvis began with `agy -p` (their saved event
    log opens with a prompt marked `started`) — headless runs nobody waits on."""
    from config import DATA_DIR
    out = set()
    for p in glob.glob(str(DATA_DIR / "agy_logs" / "*.jsonl")):
        try:
            with open(p) as f:
                if json.loads(f.readline() or "{}").get("started"):
                    out.add(Path(p).stem)
        except (OSError, ValueError):
            pass
    return out


_QUESTION_END = re.compile(r"[?？]\s*[*_`)）\]]*\s*$")


def asks_you(e: dict) -> bool:
    """Did the last turn end with something for you to answer? A pending
    AskUserQuestion / ExitPlanMode, else the desktop app's closing box
    (`wrap_up` with a question or blocked), else — for sessions without that
    box (terminal, Codex) — a reply that ends with a question mark."""
    if e.get("q"):
        return True
    if e.get("wrap") is not None:
        return bool(e["wrap"])
    return bool(_QUESTION_END.search(e.get("tail") or ""))


HEADLESS_STARTS = ("sdk-cli", "codex_exec", "agy-print")


def headless(s: dict) -> bool:
    """Started by a script or by Jarvis itself rather than by you in an app.
    `claude -p` run from a shell inside the desktop app inherits its entrypoint
    (claude-desktop) but never appears in the app's own session list."""
    start = s.get("entry0") or s.get("entry")
    if start in HEADLESS_STARTS:
        return True
    return s.get("agent") == "claude" and start == "claude-desktop" and not s.get("local_id")


def _agy_previews(path) -> dict:
    """conversation id -> first instruction, from Antigravity's metadata cache."""
    try:
        with open(path) as f:
            convs = (json.load(f) or {}).get("conversations") or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for cid, v in convs.items():
        summ = (v or {}).get("summary") or {}
        text = summ.get("Title") or summ.get("Preview") or ""
        if text:
            out[cid] = text
    return out


def _sqlite_rows(path, sql) -> list:
    """Read-only query against another app's database; never locks or writes it."""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def tool_budget_ms(name: str, inp: dict) -> int:
    """How long a tool call may run before its silence means something is off.
    A foreground Bash (a test suite, a build) writes nothing to the log until it returns."""
    if name == "Bash":
        if inp.get("run_in_background"):
            return LIVE_STALE_MS
        t = inp.get("timeout")
        return min((t if isinstance(t, (int, float)) and t > 0 else BASH_DEFAULT_MS) + 60_000, TOOL_MAX_MS)
    return TOOL_MAX_MS                      # subagents, workflows, web fetches, MCP jobs


def session_state(s: dict, now_ms: int) -> str:
    """run / wait (needs your reply) / done (turn finished, nothing asked) /
    quiet / error / limit / idle / archived."""
    if s.get("archived"):
        return "archived"
    age = now_ms - (s.get("last_at") or 0)
    kind = s.get("kind")
    if kind in ("error", "limit"):
        if age <= LIVE_STALE_MS:
            return "run"            # connection errors are retried automatically
        return kind if age <= WAIT_WINDOW_MS else "idle"
    if (s.get("awaiting") or s.get("asks")) and age <= WAIT_WINDOW_MS and not headless(s):
        return "wait"               # a question / plan approval holds the turn open too
    if kind == "run":
        if age <= LIVE_STALE_MS or now_ms <= (s.get("busy_until") or 0):
            return "run"                # including a long tool call still in flight
        return "quiet" if age <= QUIET_WINDOW_MS else "idle"
    if kind == "end":
        if background_running(s, now_ms) and age <= BG_MAX_MS:
            return "run"            # the turn ended, but the app wakes it when its background work reports back
        if headless(s):
            return "idle"           # headless run: finished, nobody is being waited on
        return "done" if age <= WAIT_WINDOW_MS else "idle"
    return "idle"


def seg_ms(seg) -> int:
    return max(seg[1] - seg[0], MIN_SEG_MS)


def clip_segs(segs, start_ms: int, end_ms: int) -> list:
    """Segments (with the single-event floor applied) clipped to [start, end)."""
    out = []
    for s, e in segs:
        e = max(e, s + MIN_SEG_MS)
        a, b = max(s, start_ms), min(e, end_ms)
        if b > a:
            out.append([a, b])
    return out


def union_ms(segs) -> int:
    total, cur = 0, None
    for s, e in sorted(segs):
        if cur is None or s > cur[1]:
            if cur:
                total += cur[1] - cur[0]
            cur = [s, e]
        else:
            cur[1] = max(cur[1], e)
    if cur:
        total += cur[1] - cur[0]
    return total


def day_bounds(day: datetime):
    start = datetime(day.year, day.month, day.day)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)
