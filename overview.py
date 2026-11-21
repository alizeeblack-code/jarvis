"""Global overview: Jarvis tasks + local Claude sessions, grouped by project."""

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from board import (_session_column, _task_at, _task_column, _thread_key,
                   project_board)
import config
from config import task_agent
from sessions import (GAP_MS, MIN_SEG_MS, QUIET_WINDOW_MS, bg_waiting, clip_segs, context_level,
                      day_bounds, day_key, headless, session_state, union_ms)

NOW_STATES = ("run", "wait", "done", "quiet", "error", "limit")
STATE_ORDER = {"error": 0, "limit": 1, "wait": 2, "run": 3, "done": 4, "quiet": 5,
               "idle": 6, "archived": 7}
DAILY_DAYS = 14

_WORKTREE_RE = re.compile(r"/\.claude/worktrees/[^/]+(/.*)?$")
_SCRATCH = ("/private/tmp/", "/tmp/", "/scratch-workspaces/", "/private/var/", "/var/folders/")
_git_root_cache: dict = {}


def _git_root(path: str) -> str:
    if path in _git_root_cache:
        return _git_root_cache[path]
    home = os.path.expanduser("~")
    cur, root = path, path
    while cur and cur != "/" and cur != home:
        if os.path.exists(os.path.join(cur, ".git")):
            root = cur
            break
        cur = os.path.dirname(cur)
    _git_root_cache[path] = root
    return root


class ProjectResolver:
    """Map a working directory to a registered project, a discovered folder, or 'other'."""

    def __init__(self, projects: list):
        self.registered = sorted(
            [(os.path.normpath(p["path"]), p) for p in projects if p.get("path")],
            key=lambda x: -len(x[0]))

    def resolve(self, cwd: str) -> dict:
        if not cwd:
            return {"key": "other", "name": "其它", "path": "", "project_id": None}
        cwd = _WORKTREE_RE.sub("", os.path.normpath(cwd))
        for path, p in self.registered:
            if cwd == path or cwd.startswith(path + "/"):
                return {"key": f"p{p['id']}", "name": p["name"], "path": path,
                        "project_id": p["id"]}
        if any(s in cwd + "/" for s in _SCRATCH):
            return {"key": "other", "name": "其它", "path": "", "project_id": None}
        root = _git_root(cwd)
        return {"key": "d:" + root, "name": os.path.basename(root) or root,
                "path": root, "project_id": None}


def link_mcp_sessions(projects: list, sessions: list) -> list:
    """Tasks claimed over MCP in an interactive session carry no session id;
    the session's own log names them (complete_task / log_progress task_id=…).
    Fills `session_id` (marked `session_linked: "mcp"`) on the given projects."""
    resolver = ProjectResolver(projects)
    best: dict = {}
    for s in sessions:
        for pid, tid in s.get("mcp") or []:
            if not pid:
                pid = resolver.resolve(s.get("origin") or s.get("cwd"))["project_id"]
            if pid and ((pid, tid) not in best or s["last_at"] > best[(pid, tid)]["last_at"]):
                best[(pid, tid)] = s
    if best:
        for p in projects:
            for t in p.get("tasks", []):
                s = best.get((p["id"], t["id"]))
                if s and not t.get("session_id"):
                    t["session_id"], t["session_linked"] = s["sid"], "mcp"
    return projects


def existing_session_ids(projects_dir, codex_home=None) -> set:
    """Session ids whose log is still on disk (Claude prunes old ones): Claude
    session ids, plus Codex thread ids from rollout file names."""
    import glob as _glob
    out = set()
    try:
        for d in os.scandir(projects_dir):
            if d.is_dir():
                for f in os.scandir(d.path):
                    if f.name.endswith(".jsonl"):
                        out.add(f.name[:-6])
    except OSError:
        pass
    if codex_home:
        for p in _glob.glob(f"{codex_home}/sessions/**/rollout-*.jsonl", recursive=True):
            out.add(os.path.basename(p)[:-6][-36:])
    return out


def moved_on(s: dict, ended_ms: int) -> bool:
    """The session was used again (terminal, desktop app) after its Jarvis task ended,
    so its own state counts again."""
    return bool(s and ended_ms and (s.get("last_at") or 0) > ended_ms + 60_000)


def project_live(projects: list, sessions: list, project_id: int,
                 existing: set, now_ms: int = None, hidden: dict = None) -> dict:
    """What the project page needs: today's numbers, live sessions, task session states."""
    now_ms = now_ms or int(time.time() * 1000)
    ov = build_overview(projects, sessions, now_ms, boards=True, hidden=hidden)
    key = f"p{project_id}"
    bucket = next((p for p in ov["projects"] if p["key"] == key), None) or {}
    project = next((p for p in projects if p["id"] == project_id), {})
    # a resumed session can belong to several tasks; the running one wins
    task_of = {t["session_id"]: t for t in sorted(project.get("tasks", []),
                                                   key=lambda t: t["status"] == "in_progress")
               if t.get("session_id")}
    task_sids = set(task_of)
    by_sid = {s["sid"]: s for s in sessions}
    tasks = {}
    for sid in task_sids:
        s = by_sid.get(sid)
        if s:
            st = session_state(s, now_ms)
            tasks[sid] = {"state": st, "bg_wait": bg_waiting(s, now_ms) if st == "run" else 0,
                          "step": s.get("step", ""),
                          "out": sum(_tok(d) for d in s["days"].values()),
                          "last_at": s["last_at"], "todo": _task_todo(s, task_of[sid])}
    return {
        "today_ms": bucket.get("today_ms", 0), "today_out": bucket.get("today_out", 0),
        "spark": bucket.get("spark", []), "color": bucket.get("color", -1),
        "now": [c for c in ov["now"] if c["project"] == key],
        "tasks": tasks,
        "resumable": sorted(task_sids & existing),
        "board": ov["boards"].get(project_id),
    }


def _iso_ms(ts) -> int:
    try:
        return int(datetime.fromisoformat(ts).timestamp() * 1000) if ts else 0
    except (TypeError, ValueError):
        return 0


def _task_todo(s: dict, task: dict):
    """The session's checklist, only if it was written during this task's run
    (a resumed session still carries the previous task's list)."""
    todo = s.get("todo")
    if not todo:
        return None
    started = _iso_ms(task.get("started_at")) if task else 0
    return todo if s.get("todo_at", 0) >= started else None


def _brief(todo):
    return todo and {k: todo[k] for k in ("done", "total", "cur")}


def _tok(day: dict) -> int:
    return day.get("out", 0)


def build_overview(projects: list, sessions: list, now_ms: int = None,
                   boards: bool = False, hidden: dict = None, keep_all: bool = False) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    now = datetime.fromtimestamp(now_ms / 1000)
    today = day_key(now_ms)
    t0, t1 = day_bounds(now)
    resolver = ProjectResolver(projects)

    # Jarvis tasks indexed by Claude session id
    task_by_sid, live_tasks, todos = {}, [], []
    for p in projects:
        for t in p.get("tasks", []):
            ref = {"project_id": p["id"], "task_id": t["id"], "status": t["status"],
                   "started_at": t.get("started_at"), "ended": _iso_ms(t.get("completed_at"))}
            if t.get("session_id"):
                task_by_sid[t["session_id"]] = (p, ref)
                # sessions you drive yourself keep their own state after the task ends
                ref["mcp"] = bool(t.get("session_linked") or t.get("source") == "session")
            if t["status"] == "in_progress":
                live_tasks.append((p, t))
            elif t["status"] == "todo":
                todos.append({"project_id": p["id"], "project": f"p{p['id']}",
                              "project_name": p["name"], "task_id": t["id"],
                              "description": t["description"], "agent": t.get("agent") or "",
                              "created_at": _iso_ms(t.get("created_at"))})
    todos.sort(key=lambda x: -x["created_at"])

    days = [(now - timedelta(days=i)) for i in range(DAILY_DAYS - 1, -1, -1)]
    day_keys = [d.strftime("%Y-%m-%d") for d in days]
    bounds = {k: day_bounds(d) for k, d in zip(day_keys, days)}

    proj: dict = {}

    def bucket(info):
        b = proj.get(info["key"])
        if b is None:
            b = proj[info["key"]] = {
                **info, "registered": info["project_id"] is not None,
                "today_ms": 0, "today_out": 0, "run": 0, "bg_wait": 0, "wait": 0, "last_at": 0,
                "spark": [0] * DAILY_DAYS, "_segs": {k: [] for k in day_keys},
                "sessions": 0, "tasks": None}
        return b

    for p in projects:   # registered projects always get a card
        b = bucket({"key": f"p{p['id']}", "name": p["name"],
                    "path": os.path.normpath(p.get("path") or ""), "project_id": p["id"]})
        tasks = p.get("tasks", [])
        b["tasks"] = {s: sum(1 for t in tasks if t["status"] == s)
                      for s in ("pending", "in_progress", "review", "done", "failed")}
        for t in tasks:
            ts = t.get("completed_at") or t.get("started_at") or t.get("created_at")
            if ts:
                try:
                    b["last_at"] = max(b["last_at"], int(datetime.fromisoformat(ts).timestamp() * 1000))
                except ValueError:
                    pass

    all_today, cards, listed, timeline = [], [], [], []
    psess: dict = {}            # project id -> [(session, state)] for its board
    agent_today: dict = {}
    agent_days: dict = {k: {} for k in day_keys}
    today_out = prompts = sessions_today = 0
    prev_union = {k: [] for k in day_keys}

    for s in sessions:
        state = session_state(s, now_ms)
        task = task_by_sid.get(s["sid"])
        if task and not task[1]["mcp"] and task[1]["status"] != "in_progress" \
                and state in ("wait", "done", "quiet", "run") and not moved_on(s, task[1]["ended"]):
            state = "idle"          # a Jarvis run that already finished isn't waiting on anyone
        if task:
            p, ref = task
            info = {"key": f"p{p['id']}", "name": p["name"], "path": p.get("path", ""),
                    "project_id": p["id"]}
        else:
            ref, info = None, resolver.resolve(s.get("origin") or s.get("cwd"))
        b = bucket(info)
        b["last_at"] = max(b["last_at"], s["last_at"])
        if info["project_id"] is not None:
            psess.setdefault(info["project_id"], []).append((s, state))

        agent = s.get("agent", "claude")
        for k in day_keys:
            segs = clip_segs(s["segs"], *bounds[k])
            if segs:
                b["_segs"][k].extend(segs)
                prev_union[k].extend(segs)
                agent_days[k].setdefault(agent, []).extend(segs)
        segs_today = clip_segs(s["segs"], t0, t1)
        day = s["days"].get(today, {})
        if segs_today or _tok(day):
            ag = agent_today.setdefault(agent, {"segs": [], "out": 0, "sessions": 0})
            ag["segs"].extend(segs_today)
            ag["out"] += _tok(day)
            ag["sessions"] += 1
        if segs_today:
            sessions_today += 1
            b["sessions"] += 1
            all_today.extend(segs_today)
            for a, z in segs_today:
                timeline.append({"start": a, "end": min(z, now_ms), "project": info["key"],
                                 "sid": s["sid"], "title": s["title"], "agent": agent})
        today_out += _tok(day)
        prompts += day.get("prompts", 0)
        b["today_out"] += _tok(day)
        bg_wait = bg_waiting(s, now_ms) if state == "run" else 0
        if state == "run":
            if bg_wait:
                b["bg_wait"] += 1
            else:
                b["run"] += 1
        elif state == "wait":
            b["wait"] += 1

        out_total = sum(_tok(d) for d in s["days"].values())
        card = {
            "agent": agent,
            "sid": s["sid"], "local_id": s.get("local_id", ""), "title": s["title"],
            "project": info["key"], "project_name": info["name"], "state": state,
            "bg_wait": bg_wait,
            "step": s.get("step", ""), "err": s.get("err", ""), "last_text": s.get("last_text", ""),
            "last_at": s["last_at"], "first_at": s.get("first_at", 0), "model": s.get("model", ""),
            "out": out_total, "today_out": _tok(day), "entry": s.get("entry", ""),
            "active_ms": union_ms(clip_segs(s["segs"], 0, now_ms)),
            "task": ref and {k: v for k, v in ref.items() if k not in ("started_at", "mcp")},
            "todo": _brief(_task_todo(s, ref) if ref else s.get("todo")),
            "ctx": s.get("ctx", 0), "ctx_level": context_level(s),
        }
        if state in NOW_STATES:
            cards.append(card)
        # "All": every session you had (scripted runs only when they did a Jarvis task)
        if ref or not (headless(s) or s.get("turns") == 0):
            listed.append(card)

    # Jarvis workers that have not written a session log yet
    known = {c["sid"] for c in cards}
    for p, t in live_tasks:
        if t.get("session_id") in known:
            continue
        cards.append({
            "agent": task_agent(t) or "claude",
            "sid": t.get("session_id") or "", "local_id": "", "title": t["description"][:80],
            "project": f"p{p['id']}", "project_name": p["name"], "state": "run",
            "step": "Jarvis worker", "err": "", "last_text": "", "last_at": now_ms,
            "first_at": 0, "model": t.get("model") or "", "out": 0, "today_out": 0,
            "entry": "projectv", "active_ms": 0, "todo": None,
            "task": {"project_id": p["id"], "task_id": t["id"], "status": t["status"]}})
        bucket({"key": f"p{p['id']}", "name": p["name"], "path": p.get("path", ""),
                "project_id": p["id"]})["run"] += 1

    # Registered projects count what their board shows, so "N waiting" on the
    # home card matches the board's column.
    board_of = {}
    for p in projects:
        bd = board_of[p["id"]] = project_board(p.get("tasks", []), psess.get(p["id"], []), now_ms, hidden)
        b = proj[f"p{p['id']}"]
        n_bg = sum(1 for c in bd["in_progress"] if c.get("bg_wait"))
        b["run"], b["bg_wait"], b["wait"] = len(bd["in_progress"]) - n_bg, n_bg, len(bd["wait"])

    cards.sort(key=lambda c: (STATE_ORDER[c["state"]], bool(c.get("bg_wait")), -c["last_at"]))
    listed.sort(key=lambda c: -c["last_at"])

    # per-project day totals (union, so parallel sessions are not double counted)
    daily = [{
        "date": k,
        "projects": {},
        "agents": {a: union_ms(segs) for a, segs in agent_days[k].items() if segs},
        "total_ms": union_ms(prev_union[k]),
    } for k in day_keys]
    for key, b in proj.items():
        for i, k in enumerate(day_keys):
            ms = union_ms(b["_segs"][k])
            b["spark"][i] = ms
            if ms:
                daily[i]["projects"][key] = ms
        b["today_ms"] = b["spark"][-1]
        del b["_segs"]

    prev = [union_ms(prev_union[k]) for k in day_keys[-8:-1]]
    used = [x for x in prev if x > 0]
    prev_avg = round(sum(used) / len(used)) if len(used) >= 3 else None

    plist = [b for b in proj.values()
             if b["registered"] or b["last_at"] >= now_ms - DAILY_DAYS * 86400 * 1000]
    plist.sort(key=lambda b: (-(b["run"] + b["bg_wait"] + b["wait"] > 0), -b["last_at"]))
    # Busiest projects get the distinct colours; "other" and idle ones stay grey.
    ranked = sorted((b for b in plist if b["key"] != "other" and sum(b["spark"])),
                    key=lambda b: -sum(b["spark"]))
    for b in plist:
        b["color"] = -1
    for i, b in enumerate(ranked):
        b["color"] = i

    return {
        "generated_at": now_ms,
        "day_start": t0,
        "today": {
            "date": today, "active_ms": union_ms(all_today), "prev_avg_ms": prev_avg,
            "out_tokens": today_out, "sessions": sessions_today, "prompts": prompts,
            "running": sum(1 for c in cards if c["state"] == "run" and not c.get("bg_wait")),
            "bg_waiting": sum(1 for c in cards if c["state"] == "run" and c.get("bg_wait")),
            "waiting": sum(1 for c in cards if c["state"] == "wait"),
            "by_agent": {a: {"active_ms": union_ms(v["segs"]), "out": v["out"],
                             "sessions": v["sessions"]} for a, v in agent_today.items()},
        },
        "timeline": timeline,
        "now": [c for c in cards
                if c["state"] != "quiet" or now_ms - c["last_at"] <= QUIET_WINDOW_MS],
        "projects": plist,
        "daily": daily,
        "all_count": sum(1 for c in listed if c["state"] != "archived"),
        **({"all": listed} if keep_all else {}),
        "todos": todos,
        **({"boards": board_of} if boards else {}),
    }


def session_page(projects: list, sessions: list, offset: int = 0, limit: int = 30,
                 agent: str = "", project: str = "", now_ms: int = None,
                 archived: bool = False) -> dict:
    """One page of "all sessions", newest first, with the projects to filter by.
    archived: the archived ones only (otherwise they are left out)."""
    ov = build_overview(projects, sessions, now_ms, keep_all=True)
    rows = ov["all"]
    n_archived = sum(1 for c in rows if c["state"] == "archived")
    rows = [c for c in rows if (c["state"] == "archived") == archived]
    if agent:
        rows = [c for c in rows if c["agent"] == agent]
    projs = {}
    for c in rows:
        projs.setdefault(c["project"], c["project_name"])
    if project:
        rows = [c for c in rows if c["project"] == project]
    return {"rows": rows[offset:offset + limit], "total": len(rows), "archived": n_archived,
            "projects": [{"key": k, "name": n} for k, n in projs.items()]}


_ACTIVE_COL_ORDER = {"in_progress": 0, "wait": 1, "pending": 2, "todo": 3}
_LIST_TITLE_MAX = 200
_LIST_ASK_MAX = 160
_agy_seg_cache: dict = {}


def _truncate_text(text: str, limit: int = _LIST_TITLE_MAX) -> str:
    """Normalize whitespace and cap overly long text for list display."""
    s = " ".join((text or "").split())
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _agy_log_segs(sid: str, agy_dir: Path = None) -> list:
    """Group Antigravity event timestamps into [start, end] working spans."""
    if not sid:
        return []
    path = (agy_dir or (config.DATA_DIR / "agy_logs")) / f"{sid}.jsonl"
    try:
        st = path.stat()
    except OSError:
        return []
    key = str(path)
    sig = (st.st_mtime_ns, st.st_size)
    cached = _agy_seg_cache.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    times = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    ts = int(json.loads(line).get("ts") or 0)
                except (ValueError, TypeError, AttributeError):
                    continue
                if ts > 0:
                    times.append(ts)
    except OSError:
        return []
    segs = []
    for ts in sorted(times):
        if segs and ts <= segs[-1][1] + GAP_MS:
            segs[-1][1] = max(segs[-1][1], ts)
        else:
            segs.append([ts, ts])
    if len(_agy_seg_cache) > 512:
        _agy_seg_cache.clear()
    _agy_seg_cache[key] = (sig, segs)
    return segs


def _work_duration_ms(s: dict, tasks: list, sid: str, state: str, now_ms: int) -> int:
    """Total execution duration across session segments, agy_logs, and task spans."""
    spans = []
    if s and s.get("segs"):
        s_segs = clip_segs(s["segs"], 0, now_ms)
        spans.extend(s_segs)
        last_at = s.get("last_at", 0)
        if state == "run" and s_segs and last_at and 0 < now_ms - last_at <= QUIET_WINDOW_MS:
            tail_end = max(s["segs"][-1][1], last_at)
            if now_ms > tail_end:
                spans.append([tail_end, now_ms])
    task_spans = []
    for t in tasks or ():
        st = _iso_ms(t.get("started_at"))
        en = _iso_ms(t.get("completed_at"))
        if not en and t.get("status") == "in_progress" and st > 0:
            en = max(st, now_ms)
        if st > 0 and en > st:
            en = min(en, now_ms)
            if en > st:
                task_spans.append([st, en])
    spans.extend(task_spans)
    if sid and ((s or {}).get("agent") == "antigravity" or not (s and s.get("segs"))):
        for a, b in _agy_log_segs(sid):
            if a >= now_ms:
                continue
            b = min(b, now_ms)
            if b > a:
                spans.append([a, b])
            elif not task_spans:
                spans.append([a, min(now_ms, a + MIN_SEG_MS)])
    return union_ms(spans)


def _task_brief(t: dict) -> dict:
    return {
        "id": t["id"],
        "status": t.get("status", ""),
        "archived_from": t.get("archived_from", ""),
        "description": _truncate_text(t.get("description", ""), _LIST_TITLE_MAX),
        "agent": task_agent(t),
        "plan_mode": bool(t.get("plan_mode")),
        "merge_state": t.get("merge_state", ""),
        "source": t.get("source", ""),
        "images_count": len(t.get("images") or []),
        "error": t.get("error", ""),
        "started_at": t.get("started_at"),
        "completed_at": t.get("completed_at"),
        "created_at": t.get("created_at"),
    }


def _session_metrics(s: dict, task: dict, now_ms: int, tasks: list = None,
                     sid: str = "", state: str = "") -> dict:
    eff_sid = sid or (s or {}).get("sid", "")
    active_ms = _work_duration_ms(s, tasks if tasks is not None else ([task] if task else ()),
                                  eff_sid, state, now_ms)
    if not s:
        return {"out": 0, "active_ms": active_ms, "todo": None, "ctx": 0, "ctx_level": 0}
    todo = _task_todo(s, task) if task else s.get("todo")
    return {
        "out": sum(_tok(d) for d in s.get("days", {}).values()),
        "active_ms": active_ms,
        "todo": _brief(todo),
        "ctx": s.get("ctx", 0),
        "ctx_level": context_level(s),
    }


def _classify_session(s: dict, state: str, hidden: dict) -> tuple:
    if s.get("archived"):
        return "archived", "done", ("panel" if s.get("archived_here") else "agent")
    if hidden.get(s["sid"], 0) >= s.get("last_at", 0):
        return "archived", "done", "hidden"
    col = _session_column(state)
    if col in ("in_progress", "wait"):
        return "active", col, ""
    return "recent", col, ""


def _matches_list_query(item: dict, q: str, search_texts: list) -> bool:
    if not q:
        return True
    q = q.lower()
    fields = [item.get("title"), item.get("ask"), item.get("step"),
              item.get("err"), item.get("sid"), *search_texts]
    for tid in item.get("tasks") or []:
        fields.append(str(tid))
        fields.append(f"#{tid}")
    return any(q in (f or "").lower() for f in fields)


def project_work_list(projects: list, sessions: list, project_id: int,
                      existing: set, now_ms: int = None, hidden: dict = None,
                      agent: str = "", query: str = "",
                      recent_offset: int = 0, recent_limit: int = 50,
                      archived_offset: int = 0, archived_limit: int = 30) -> dict:
    """Task + session list view for one registered project:
    active (in_progress/wait/pending/todo), recent sessions, and archived/hidden items."""
    now_ms = now_ms or int(time.time() * 1000)
    hidden = hidden or {}
    project = next((p for p in projects if p["id"] == project_id), None)
    if not project:
        return {"active": [], "recent": [], "recent_total": 0,
                "archived": [], "archived_total": 0,
                "counts": {"active": 0, "recent": 0, "archived": 0}}

    resolver = ProjectResolver(projects)
    tasks = sorted(project.get("tasks", []), key=lambda t: t["id"])
    groups: dict = {}
    task_by_sid: dict = {}
    for t in tasks:
        groups.setdefault(_thread_key(t), []).append(t)
        if t.get("session_id"):
            task_by_sid[t["session_id"]] = t

    by_sid: dict = {}
    for s in sessions:
        sid = s["sid"]
        t = task_by_sid.get(sid)
        if not t and resolver.resolve(s.get("origin") or s.get("cwd"))["project_id"] != project_id:
            continue
        state = session_state(s, now_ms)
        if t:
            mcp = bool(t.get("session_linked") or t.get("source") == "session")
            ended = _iso_ms(t.get("completed_at"))
            if not mcp and t["status"] != "in_progress" and state in ("wait", "done", "quiet", "run") and not moved_on(s, ended):
                state = "idle"
        by_sid[sid] = (s, state)

    used_sids = set()
    entries = []  # [(item, search_texts)]

    for ts in groups.values():
        live_ts = [t for t in ts if t.get("status") != "archived"]
        ref_ts = live_ts or ts
        head = next((t for t in reversed(ref_ts) if t["status"] == "in_progress"), ref_ts[-1])
        sid = head.get("session_id") or next(
            (t.get("session_id") for t in reversed(ref_ts) if t.get("session_id")), "")
        if sid:
            used_sids.add(sid)
        s, state = by_sid.get(sid, (None, ""))
        s_last = (s or {}).get("last_at", 0)
        max_task_at = max(_task_at(t) for t in ts)
        at = max(max_task_at, s_last)

        if not live_ts:
            if s and s_last > max_task_at + 60_000:
                bucket, col, reason = _classify_session(s, state, hidden)
            else:
                bucket, col, reason = "archived", "done", "task"
        else:
            col = _task_column(live_ts, head, state, now_ms, s_last)
            if col in _ACTIVE_COL_ORDER:
                bucket, reason = "active", ""
            elif s and s.get("archived"):
                bucket, reason = "archived", ("panel" if s.get("archived_here") else "agent")
            elif sid and hidden.get(sid, 0) >= max(s_last, max_task_at):
                bucket, reason = "archived", "hidden"
            else:
                bucket, reason = "recent", ""

        eff_agent = task_agent(head) or (s or {}).get("agent", "")
        is_session_reply = head.get("source") == "session"
        raw_title = (s or {}).get("title") or (s or {}).get("prompt") or "" if (is_session_reply or not live_ts) else ""
        title = _truncate_text(raw_title, _LIST_TITLE_MAX)
        metrics = _session_metrics(s, head, now_ms, tasks=ts, sid=sid, state=state)
        item = {
            "key": f"t:{head['id']}",
            "bucket": bucket,
            "column": col,
            "archive_reason": reason,
            "head": head["id"],
            "tasks": [t["id"] for t in ref_ts],
            "task": _task_brief(head),
            "has_pending_merge": any(t.get("merge_state") == "pending" for t in ref_ts),
            "sid": sid,
            "agent": eff_agent,
            "state": state,
            "bg_wait": bg_waiting(s, now_ms) if state == "run" else 0,
            "at": at,
            "title": title,
            "ask": _truncate_text(head.get("description", ""), _LIST_ASK_MAX) if is_session_reply else "",
            "step": (s or {}).get("step", ""),
            "err": (s or {}).get("err", "") or head.get("error", ""),
            "turns": (s or {}).get("turns"),
            "resumable": bool(sid and (sid in existing or eff_agent == "antigravity")),
            "archived": bucket == "archived",
            "hidden": reason == "hidden",
            **metrics,
        }
        search_texts = ([raw_title] if raw_title else []) + [t.get("description", "") for t in ts] + [t.get("summary", "") for t in ts]
        entries.append((item, search_texts))

    for sid, (s, state) in by_sid.items():
        if sid in used_sids:
            continue
        if s.get("agent") != "antigravity" and not s.get("path"):
            continue
        if s.get("turns") == 0 or headless(s):
            continue
        bucket, col, reason = _classify_session(s, state, hidden)
        ag = s.get("agent", "claude")
        raw_title = s.get("title") or s.get("prompt") or ""
        metrics = _session_metrics(s, None, now_ms, tasks=[], sid=sid, state=state)
        item = {
            "key": f"s:{sid}",
            "bucket": bucket,
            "column": col,
            "archive_reason": reason,
            "head": None,
            "tasks": [],
            "task": None,
            "has_pending_merge": False,
            "sid": sid,
            "agent": ag,
            "state": state,
            "bg_wait": bg_waiting(s, now_ms) if state == "run" else 0,
            "at": s.get("last_at", 0),
            "title": _truncate_text(raw_title, _LIST_TITLE_MAX),
            "ask": "",
            "step": s.get("step", ""),
            "err": s.get("err", ""),
            "turns": s.get("turns"),
            "resumable": bool(sid in existing or ag == "antigravity"),
            "archived": bucket == "archived",
            "hidden": reason == "hidden",
            **metrics,
        }
        entries.append((item, [raw_title] if raw_title else []))

    counts_all = {"active": 0, "recent": 0, "archived": 0}
    buckets = {"active": [], "recent": [], "archived": []}
    q = (query or "").strip()
    for item, search_texts in entries:
        counts_all[item["bucket"]] += 1
        if agent and item["agent"] != agent:
            continue
        if q and not _matches_list_query(item, q, search_texts):
            continue
        buckets[item["bucket"]].append(item)

    buckets["active"].sort(key=lambda x: (
        _ACTIVE_COL_ORDER.get(x["column"], 9),
        (x["head"] or 0) if x["column"] in ("pending", "todo") else -x["at"],
    ))
    buckets["recent"].sort(key=lambda x: -x["at"])
    buckets["archived"].sort(key=lambda x: -x["at"])

    recent = buckets["recent"]
    archived = buckets["archived"]
    return {
        "active": buckets["active"],
        "recent": recent[recent_offset:recent_offset + recent_limit],
        "recent_total": len(recent),
        "archived": archived[archived_offset:archived_offset + archived_limit],
        "archived_total": len(archived),
        "counts": counts_all,
    }

