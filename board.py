"""Project board: one card per line of work, placed by who has to act next.

A card is a session together with every Jarvis task that ran in it (a
follow-up resumed in place, replies sent from the session panel), a task that
has not started yet, or a session opened outside Jarvis (desktop app,
terminal). Columns:

  todo         written down, not handed out
  pending      handed out, not started
  in_progress  a worker or a session is running
  wait         your move: a session that stopped for you, a plan to review,
               a recent failure / error / limit hit
  done         finished; sessions quiet for a while
"""

from datetime import datetime

from config import task_agent
from sessions import bg_waiting, context_level, headless

COLUMNS = ("todo", "pending", "in_progress", "wait", "done")
WAIT_STATES = ("wait", "error", "limit")
FAILED_WAIT_MS = 3 * 86400 * 1000     # failures older than this stop asking for you


def _ms(ts) -> int:
    try:
        return int(datetime.fromisoformat(ts).timestamp() * 1000) if ts else 0
    except (TypeError, ValueError):
        return 0


def _task_at(t: dict) -> int:
    return max(_ms(t.get("completed_at")), _ms(t.get("started_at")), _ms(t.get("created_at")))


def _thread_key(t: dict) -> str:
    # Tasks claimed over MCP by one long session are separate pieces of work.
    if t.get("session_id") and not t.get("session_linked"):
        return t["session_id"]
    return f"t{t['id']}"


def _task_column(tasks: list, head: dict, state: str, now_ms: int, s_last: int = 0) -> str:
    if any(t["status"] == "in_progress" for t in tasks) or state == "run":
        return "in_progress"
    if any(t["status"] == "pending" for t in tasks):
        return "pending"
    st = head["status"]
    if st in ("todo", "pending"):
        return st
    if st == "review":
        return "wait"
    if st == "failed":
        if head.get("source") == "session" and s_last > _task_at(head):
            return _session_column(state)   # a reply that didn't go out; the session moved on without it
        return "wait" if now_ms - _task_at(head) <= FAILED_WAIT_MS else "done"
    if st == "done" and head.get("source") == "session" and state in WAIT_STATES:
        return "wait"          # you replied in the panel; it answered and waits for you
    return "done"


def _session_column(state: str) -> str:
    if state == "run":
        return "in_progress"
    return "wait" if state in WAIT_STATES else "done"


def _brief(todo):
    return {k: todo.get(k) for k in ("done", "total", "cur")} if todo else None


def _own_card(s: dict, hidden: dict) -> bool:
    """A session opened outside Jarvis that deserves a card: it can still be
    read and resumed, you asked it something, and you didn't put it away."""
    if s.get("agent") != "antigravity" and not s.get("path"):
        return False                       # log gone: nothing to read or resume
    if s.get("turns") == 0 or headless(s) or s.get("archived"):
        return False                       # scripted runs aren't a line of work; archived ones are put away
    return hidden.get(s["sid"], 0) < s.get("last_at", 0)


def project_board(tasks: list, sessions: list, now_ms: int, hidden: dict = None) -> dict:
    """tasks: the project's tasks (MCP session links filled in).
    sessions: [(session, state)] — the project's sessions with their display state.
    Returns {column: [card]}; a card is
    {head, tasks, sid, agent, state, at, title, step} (head = latest task id or None)."""
    by_sid = {s["sid"]: (s, state) for s, state in sessions}
    groups: dict = {}
    for t in sorted(tasks, key=lambda t: t["id"]):
        groups.setdefault(_thread_key(t), []).append(t)
    used = {t["session_id"] for t in tasks if t.get("session_id")}
    cols = {c: [] for c in COLUMNS}
    for ts in groups.values():
        head = next((t for t in reversed(ts) if t["status"] == "in_progress"), ts[-1])
        sid = head.get("session_id") or ""
        s, state = by_sid.get(sid, (None, ""))
        at = max([_task_at(t) for t in ts] + [(s or {}).get("last_at", 0)])
        cols[_task_column(ts, head, state, now_ms, (s or {}).get("last_at", 0))].append({
            "head": head["id"], "tasks": [t["id"] for t in ts], "sid": sid,
            "agent": task_agent(head) or (s or {}).get("agent", ""), "state": state,
            "bg_wait": bg_waiting(s, now_ms) if state == "run" else 0,
            "at": at, "step": (s or {}).get("step", ""),
            "ctx": (s or {}).get("ctx", 0), "ctx_level": context_level(s) if s else 0,
            # replies sent from the panel read under the session's own title
            "title": (s or {}).get("title", "") if head.get("source") == "session" else ""})
    for sid, (s, state) in by_sid.items():
        if sid in used or not _own_card(s, hidden or {}):
            continue
        cols[_session_column(state)].append({
            "head": None, "tasks": [], "sid": sid, "agent": s.get("agent", "claude"),
            "state": state, "bg_wait": bg_waiting(s, now_ms) if state == "run" else 0,
            "at": s.get("last_at", 0), "turns": s.get("turns"),
            "archived": bool(s.get("archived")),
            "title": s.get("title") or s.get("prompt") or "", "step": s.get("step", ""),
            "err": s.get("err", ""), "todo": _brief(s.get("todo")),
            "ctx": s.get("ctx", 0), "ctx_level": context_level(s)})
    for c in ("todo", "pending"):
        cols[c].sort(key=lambda x: x["head"] or 0)
    for c in ("in_progress", "wait", "done"):
        cols[c].sort(key=lambda x: -x["at"])
    return cols
