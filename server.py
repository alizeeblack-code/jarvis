"""
Jarvis - 任务管理中心
FastAPI app with routes, WorkerManager, and WebSocket hub.
Modules: config.py, store.py, worker.py
"""

# Force standard asyncio event loop policy BEFORE anything else.
# uvloop (uvicorn's default) has known bugs with subprocess stdin pipes
# (https://github.com/MagicStack/uvloop/issues/312)
import asyncio
try:
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
except Exception:
    pass
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from access import AccessMiddleware, access_token, with_token
from desk import desk_usage, desk_view
import reveal
from config import BASE_DIR, DATA_DIR, IMAGES_ONLY_PROMPT, MAX_WORKERS, UPLOADS_DIR, task_agent
from overview import (ProjectResolver, build_overview, existing_session_ids, moved_on, session_page,
                      link_mcp_sessions, project_live, project_work_list)
from session_send import SessionSender
from sessions import (CLAUDE_PROJECTS, CODEX_HOME, QUIET_WINDOW_MS, SessionIndex, context_level,
                      headless, session_state)
from transcript import TranscriptReader, find_log, image_at, session_settings, tool_cat
from usage import tracker as usage_tracker
import gitflow
from hidden import archived_sessions, hidden_sessions
from store import ProjectStore
from worker import WorkerProcess
from utils import extract_questions

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB


# ─── WorkerManager ───────────────────────────────────────────────────────────

class WorkerManager:
    """Manages multiple parallel Claude Code workers (Ralph loop)"""

    def __init__(self, store: ProjectStore, broadcast_fn):
        self.store = store
        self.broadcast = broadcast_fn
        self.workers: dict[str, WorkerProcess] = {}
        self.worker_tasks: dict[str, asyncio.Task] = {}
        self.max_workers = MAX_WORKERS
        self._polling = False
        self._poll_task = None
        self._wake: Optional[asyncio.Event] = None

    def wake(self):
        if self._wake:
            self._wake.set()

    async def start_polling(self):
        self._polling = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop_polling(self):
        self._polling = False
        if self._poll_task:
            self._poll_task.cancel()
            try: await self._poll_task
            except asyncio.CancelledError: pass

    async def _poll_loop(self):
        self._wake = asyncio.Event()
        self._stale_counter = 25    # first check a few seconds after start: settle runs a restart cut off
        while self._polling:
            claimed = False
            try:
                claimed = await self._poll_once()
            except Exception as e:                       # one bad round must not stop the workers
                print(f"[workers] poll failed: {e!r}")
            if claimed:
                continue                                 # look for the next task right away
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> bool:
        """One round: start a task if a worker is free, tidy finished workers, and
        every ~60s settle stale / merged work. Returns whether a task was started."""
        self._wake.clear()
        active = len([t for t in self.worker_tasks.values() if not t.done()])
        if active < self.max_workers:
            wid = f"worker-{uuid.uuid4().hex[:8]}"
            task = self.store.claim_any_task(wid)
            if task:
                path = self.store.get_project_path(task["project_id"])
                if path:
                    await self._start_worker(wid, task, path)
                    return True
        for w in [w for w, t in self.worker_tasks.items() if t.done()]:
            del self.worker_tasks[w]
            self.workers.pop(w, None)
        self._stale_counter += 1
        if self._stale_counter >= 30:
            self._stale_counter = 0
            await self._cleanup_stale_tasks()
            try:
                await self._settle_merged_lines()
            except Exception as e:                       # noqa: BLE001
                print(f"[merge-settle] {e}")
        return False

    async def _cleanup_stale_tasks(self):
        """Auto-fail tasks whose worker process died without updating status"""
        active_task_ids = {
            w.task_id for w in self.workers.values()
            if w.worker_id in self.worker_tasks
            and not self.worker_tasks[w.worker_id].done()
        }
        for project in self.store.get_all_projects():
            pid = project["id"]
            for task in self.store.get_tasks(pid):
                if task["status"] != "in_progress":
                    continue
                if task["id"] in active_task_ids:
                    continue
                if task.get("source") == "session":
                    # a reply from the session panel: its run lives in SessionSender; when this
                    # process no longer tracks it (restarted mid-send), the session log decides
                    if not sender.busy(task.get("session_id") or ""):
                        t = await asyncio.to_thread(_settle_panel_task, pid, task)
                        if t:
                            await self.broadcast({"type": "task_update", "task_id": t["id"],
                                                  "project_id": pid, "status": t["status"]})
                    continue
                started = task.get("started_at", "")
                if not started:
                    continue
                if _started_ms(task) < SERVER_STARTED_MS:
                    # its worker lived in the server process before this one: a restart
                    # (often the task restarting Jarvis itself) took the run down with it
                    t = await asyncio.to_thread(_settle_lost_worker_task, pid, task)
                    if t:
                        await self.broadcast({"type": "task_update", "task_id": t["id"],
                                              "project_id": pid, "status": t["status"]})
                    continue
                # Check time since LAST LOG (not start time)
                last_time = (self.store.get_last_log_time(pid, task["id"])
                             or started)
                try:
                    idle = (datetime.now()
                            - datetime.fromisoformat(last_time)).total_seconds()
                except (ValueError, TypeError):
                    idle = 0
                if idle > 600:  # 10 min since last activity
                    self.store.update_task(
                        pid, task["id"], status="failed",
                        error="Worker process died (auto-detected)")
                    await self.broadcast({
                        "type": "task_update", "task_id": task["id"],
                        "project_id": pid, "status": "failed",
                    })

    async def _settle_merged_lines(self):
        """「待合并」work that reached main some other way (a session merged it
        itself) is marked merged, and its folder and branch are cleaned up."""
        for project in self.store.get_all_projects():
            pid, repo = project["id"], project.get("path") or ""
            tasks = self.store.get_tasks(pid)
            waiting = [t for t in tasks if t.get("merge_state") == "pending" and t.get("line")]
            if not waiting or not gitflow.is_repo(repo):
                continue
            for line in {t["line"] for t in waiting}:
                same = [t for t in tasks if t.get("line") == line]
                if any(t["status"] in ("in_progress", "pending") for t in same):
                    continue
                ids = [i for t in same if t.get("merge_state") == "pending" for i in t.get("commit_ids") or []]
                if not await asyncio.to_thread(gitflow.in_main, repo, ids):
                    continue
                if not await asyncio.to_thread(gitflow.settle_merged, repo, line):
                    continue
                now = datetime.now().isoformat()
                for t in same:
                    if t.get("merge_state") == "pending":
                        self.store.update_task(pid, t["id"], merge_state="merged", merged_at=now)
                        await self.broadcast({"type": "task_update", "task_id": t["id"],
                                              "project_id": pid, "status": t["status"]})

    async def _start_worker(self, wid, task, project_path):
        settings = self.store.get_project_settings(task["project_id"])
        worker = WorkerProcess(wid, task, project_path, self.store,
                               self.broadcast, project_settings=settings)
        sid = task.get("session_id") or worker._get_predecessor_session()
        if sid:
            rb = await asyncio.to_thread(_round_count, sid, worker.agent)
            task["rounds_before"] = rb
            self.store.update_task(task["project_id"], task["id"],
                                   session_id=sid, rounds_before=rb)
        self.workers[wid] = worker
        wt = asyncio.create_task(worker.run())
        wt.add_done_callback(lambda _: self.wake())
        self.worker_tasks[wid] = wt
        await self.broadcast({
            "type": "worker_started", "worker_id": wid,
            "task_id": task["id"], "project_id": task["project_id"],
        })

    async def cancel_task(self, project_id, task_id):
        for wid, w in self.workers.items():
            if w.task_id == task_id and w.project_id == project_id:
                await w.cancel()
                return True
        self.store.update_task(project_id, task_id, status="cancelled")
        return True

    async def retry_task(self, project_id, task_id):
        task = self.store.get_task(project_id, task_id)
        if task and task["status"] in ("failed", "cancelled"):
            self.store.update_task(project_id, task_id, status="pending",
                                   error=None, worker_id=None,
                                   started_at=None, logs=[])
            self.wake()
            return True
        return False

    def get_active_workers(self):
        return [{
            "worker_id": wid, "task_id": w.task_id,
            "project_id": w.project_id,
            "running": not self.worker_tasks[wid].done()
                if wid in self.worker_tasks else False,
        } for wid, w in self.workers.items()]


# ─── WebSocket Hub ───────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, ws):
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws):
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast(self, message):
        dead = []
        for conn in self.active_connections:
            try: await conn.send_json(message)
            except Exception: dead.append(conn)
        for conn in dead:
            if conn in self.active_connections:
                self.active_connections.remove(conn)


# ─── App Setup ───────────────────────────────────────────────────────────────

ws_manager = ConnectionManager()
store = ProjectStore()
worker_manager = WorkerManager(store, ws_manager.broadcast)
session_index = SessionIndex(DATA_DIR / "session_index.json")
SESSION_SCAN_INTERVAL = 10
transcripts = TranscriptReader()
_loop: Optional[asyncio.AbstractEventLoop] = None


def _finish_panel_task(sid: str) -> list:
    """Close the task a reply from the session panel opened: its outcome and the
    session's last answer as the summary. Returns [(project_id, task)] updated."""
    run = sender.status(sid)
    if not run or run.get("state") == "running":
        return []
    out = []
    for p in store.get_all_projects():
        for t in store.get_tasks(p["id"]):
            if (t.get("session_id") != sid or t.get("source") != "session"
                    or t["status"] != "in_progress"):
                continue
            status = {"done": "done", "cancelled": "cancelled"}.get(run["state"], "failed")
            out.append((p["id"], store.update_task(
                p["id"], t["id"], status=status, completed_at=datetime.now().isoformat(),
                summary=_last_reply(sid, task_agent(t) or "claude") if status == "done" else "",
                error=run.get("error") or None)))
    return out


def _settle_panel_task(pid: int, task: dict):
    """Close a panel reply whose run this process lost (the server restarted while it
    ran — e.g. a session editing Jarvis itself). Finished turn → done with its last
    answer; still mid-turn → wait; nothing at all after the reply for a while → failed."""
    sid = task.get("session_id") or ""
    s = next((x for x in session_index.sessions() if x["sid"] == sid), None)
    now = int(datetime.now().timestamp() * 1000)
    try:
        started = int(datetime.fromisoformat(task.get("started_at")).timestamp() * 1000)
    except (TypeError, ValueError):
        started = now
    if s and s["last_at"] > started:
        finished = s.get("kind") in ("end", "error", "limit") or s.get("asks")
        if not finished and now - s["last_at"] < QUIET_WINDOW_MS:
            return None                     # still working through the turn
        return store.update_task(pid, task["id"], status="done",
                                 completed_at=datetime.fromtimestamp(s["last_at"] / 1000).isoformat(),
                                 summary=_last_reply(sid, task_agent(task) or "claude") or None, error=None)
    if now - started > 10 * 60 * 1000:
        return store.update_task(pid, task["id"], status="failed", completed_at=datetime.now().isoformat(),
                                 error="发出后会话没有任何动静")
    return None


SERVER_STARTED_MS = int(time.time() * 1000)
_ASKS_RESTART = re.compile(r"重启|restart", re.I)


def _started_ms(task: dict) -> int:
    try:
        return int(datetime.fromisoformat(task.get("started_at")).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _agent_alive(task: dict) -> bool:
    """The executor this task started is still running (it outlives a server restart)."""
    pid = task.get("agent_pid")
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:                                 # a recycled pid is someone else's process
        import subprocess
        comm = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5).stdout
    except Exception:                    # noqa: BLE001
        return True
    return any(w in comm for w in ("claude", "codex", "agy"))


def _agy_finished_since(sid: str, since_ms: int) -> bool:
    """agy wrote a result for a turn that started after since_ms (from its event log)."""
    path = find_log(sid, "antigravity")
    if not path:
        return False
    seen_prompt = False
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if '"event": "prompt"' in line:
                    try:
                        seen_prompt = json.loads(line).get("ts", 0) >= since_ms - 5000
                    except ValueError:
                        pass
                elif seen_prompt and '"event": "result"' in line:
                    return True
    except OSError:
        return False
    return False


def _settle_lost_worker_task(pid: int, task: dict):
    """A worker task whose worker died with the previous server process. While its
    executor is still running, wait. Then: the turn finished, or it left commits on
    its branch → done (commits wait to be merged); it was asked to restart the
    service (Jarvis restarting itself ends the run that asked for it) → done;
    otherwise it was cut off → failed, saying so, so it shows under 等你."""
    if _agent_alive(task):
        return None
    sid, agent = task.get("session_id") or "", task_agent(task) or "claude"
    started = _started_ms(task)
    s = next((x for x in session_index.sessions() if x["sid"] == sid), None) if sid else None
    now = int(time.time() * 1000)
    if agent != "antigravity" and s and now - s.get("last_at", 0) < 2 * 60 * 1000 \
            and s.get("kind") not in ("end", "error", "limit"):
        return None                         # a child that outlived the server may still be writing
    reply = _last_reply(sid, agent) if sid else ""
    if agent == "antigravity":              # its index entry doesn't say whether a turn ended
        finished = bool(sid) and _agy_finished_since(sid, started)
    else:
        finished = bool(s and s.get("last_at", 0) > started and (
            s.get("kind") in ("end", "error", "limit") or s.get("asks")))
    extra, note = {}, ""
    repo, line = store.get_project_path(pid) or "", task.get("line")
    if line and repo and gitflow.is_repo(repo):
        info = gitflow.ahead(repo, line)
        if info["commits"]:
            extra = {"merge_state": "pending", "commit_ids": info["ids"]}
            if not finished:
                note = (f"\n\n（服务重启后这一轮在后台跑完，回复没有记录下来；"
                        f"改动在分支 {info['branch']}，{info['commits']} 个提交待合并。）")
    if finished or extra or _ASKS_RESTART.search(task.get("description") or ""):
        if not (finished or extra):
            note = "\n\n（服务已重启；这一轮随旧的服务进程一起结束。）"
        return store.update_task(pid, task["id"], status="done", completed_at=datetime.now().isoformat(),
                                 summary=((reply or "") + note).strip() or None, error=None, **extra)
    return store.update_task(pid, task["id"], status="failed", completed_at=datetime.now().isoformat(),
                             summary=reply or None, error="服务重启打断了这一轮，可以回复「继续」接着做")


def _last_reply(sid: str, agent: str) -> str:
    s = next((x for x in session_index.sessions() if x["sid"] == sid), None)
    path = (s or {}).get("path") or find_log(sid, agent)
    if not path:        # Antigravity without a saved event log: only its summary preview
        return (s or {}).get("last_text", "") if s else ""
    try:
        rounds = transcripts.read(path, agent, limit=1)["rounds"]
    except OSError:
        return ""
    texts = [i["t"] for i in (rounds[-1]["items"] if rounds else []) if i["k"] == "text"]
    return texts[-1] if texts else ""


def _round_count(sid: str, agent: str, path: str = "") -> int:
    path = path or find_log(sid, agent)
    if not path:
        return 0
    try:
        return transcripts.read(path, agent, limit=1)["total"]
    except OSError:
        return 0


def _iso_ms(ts) -> int:
    try:
        return int(datetime.fromisoformat(ts).timestamp() * 1000) if ts else 0
    except (TypeError, ValueError):
        return 0


def _clean_images(raw) -> list:
    if not isinstance(raw, list):
        return []
    root = UPLOADS_DIR.resolve()
    out = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        path_str = str(item.get("path") or "").strip()
        p = None
        if path_str:
            try:
                cand = Path(path_str).resolve()
                if cand.is_relative_to(root) and cand.is_file():
                    p = cand
            except (OSError, ValueError):
                p = None
        if p is None and url.startswith("/api/uploads/"):
            parts = url[len("/api/uploads/"):].split("/", 1)
            if len(parts) == 2:
                bucket = "sessions" if parts[0] == "s" else "".join(c for c in parts[0] if c.isdigit())
                safe_name = "".join(c for c in parts[1] if c.isalnum() or c in ".-_")
                if bucket and safe_name:
                    cand = (UPLOADS_DIR / bucket / safe_name).resolve()
                    if cand.is_relative_to(root) and cand.is_file():
                        p = cand
        if p is None:
            continue
        if not url:
            rel = p.relative_to(root)
            bucket_seg = "s" if rel.parts[0] == "sessions" else rel.parts[0]
            url = f"/api/uploads/{bucket_seg}/{rel.parts[-1]}"
        out.append({"url": url, "path": str(p)})
    return out


def _image_urls(images) -> list:
    return [img.get("url") for img in (images or []) if isinstance(img, dict) and img.get("url")]


def _in_flight_round(text: str, at_ms: int, images=None) -> dict:
    urls = _image_urls(images)
    r = {"ask": text[:8000], "at": at_ms, "end": at_ms, "images": len(urls), "imgs": [],
         "items": [], "interrupted": False, "model": "", "effort": ""}
    if urls:
        r["image_urls"] = urls
    return r


async def _drain_direct_queue(sid: str) -> bool:
    """If `sid` has queued direct instructions and is no longer running, start the next one."""
    q = sender.queue(sid)
    if not q or sender.busy(sid):
        return False
    s = next((x for x in session_index.sessions() if x["sid"] == sid), None)
    now_ms = int(datetime.now().timestamp() * 1000)
    if s and session_state(s, now_ms) == "run":
        return False
    agent = q[0]["agent"]
    path = (s or {}).get("path") or ""
    rb, (model, effort) = await asyncio.gather(
        asyncio.to_thread(_round_count, sid, agent, path),
        asyncio.to_thread(session_settings, sid, agent, path),
    )
    started = sender.start_next(sid, rounds_before=rb, model=model, effort=effort)
    if not started:
        return False
    item, _ = started
    if item.get("project_id"):
        new = store.add_task(item["project_id"], item["text"] or IMAGES_ONLY_PROMPT,
                             images=item.get("images") or [], agent=item["agent"], extra={
            "status": "in_progress", "session_id": sid, "source": "session",
            "started_at": datetime.now().isoformat(), "rounds_before": rb})
        if new:
            await ws_manager.broadcast({"type": "task_created", "task": new,
                                        "project_id": item["project_id"]})
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return True


async def _after_send(sid: str):
    if await asyncio.to_thread(session_index.scan):
        await ws_manager.broadcast({"type": "overview_changed"})
    for pid, t in await asyncio.to_thread(_finish_panel_task, sid):
        if t:
            await ws_manager.broadcast({"type": "task_update", "task_id": t["id"],
                                        "project_id": pid, "status": t["status"]})
    if sender.status(sid).get("state") != "error":
        await _drain_direct_queue(sid)
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})


def _send_done(sid: str):
    if _loop:
        asyncio.run_coroutine_threadsafe(_after_send(sid), _loop)


sender = SessionSender(on_done=_send_done)


def linked_projects(sessions=None) -> list:
    """Projects with MCP-claimed tasks tied to the interactive session that did them."""
    return link_mcp_sessions(store.snapshot_projects(),
                             session_index.sessions() if sessions is None else sessions)


CLAUDE_USAGE_EVERY_MS = 30 * 60 * 1000


def claude_used_since(at_ms: float, sessions: list) -> bool:
    """Has any Claude session on this machine done something since `at_ms`? Only use spends
    quota, so with none the last reading still holds (a window past its reset counts as fresh)."""
    return any(x.get("agent", "claude") == "claude" and (x.get("last_at") or 0) > at_ms for x in sessions)


async def _claude_usage_loop():
    """Keep Claude's quota at most half an hour old for the desk display, but only when Claude
    was used since it was last read: then send the tiny probe turn /api/usage/refresh sends
    (a real Haiku request, so never while nobody is using Claude). Runs Jarvis starts read
    it for free."""
    while True:
        await asyncio.sleep(60)
        try:
            at = ((usage_tracker.snapshot(False).get("claude") or {}).get("at") or 0)
            if time.time() * 1000 - at > CLAUDE_USAGE_EVERY_MS and claude_used_since(at, session_index.sessions()):
                err = await asyncio.to_thread(usage_tracker.probe_claude)
                if err:
                    print(f"[usage] Claude quota probe failed: {err}")
                    await asyncio.sleep(CLAUDE_USAGE_EVERY_MS / 1000)   # don't retry every minute
        except Exception as e:
            print(f"[usage] Claude quota probe failed: {e}")


async def _session_scan_loop():
    """Re-read appended Claude session logs; tell open pages when something changed."""
    while True:
        try:
            if await asyncio.to_thread(session_index.scan):
                await ws_manager.broadcast({"type": "overview_changed"})
            for sid in sender.queued_sids():
                run = sender.status(sid)
                q = sender.queue(sid)
                if q and (run.get("state") != "error" or q[0]["at"] > run.get("ended_at", 0)):
                    await _drain_direct_queue(sid)
        except Exception as e:  # never let a bad log line kill the loop
            print(f"[sessions] scan failed: {e}")
        await asyncio.sleep(SESSION_SCAN_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    access_token()   # create data/access_token now, so it is there to hand to other devices
    try:   # lets mcp_task_server.py find us on whatever port we run
        (DATA_DIR / "server_port").write_text(_uvicorn_arg("--port", "8000") + "\n")
    except OSError:
        pass
    await worker_manager.start_polling()
    scan_task = asyncio.create_task(_session_scan_loop())
    usage_task = asyncio.create_task(_claude_usage_loop())
    yield
    scan_task.cancel()
    usage_task.cancel()
    await worker_manager.stop_polling()


app = FastAPI(title="Jarvis", lifespan=lifespan)
app.add_middleware(AccessMiddleware)


# ─── Project API ─────────────────────────────────────────────────────────────

_STATIC_REF = re.compile(r'/static/([\w.-]+\.(?:js|css))"')


def _ui_version() -> str:
    """Changes whenever any page file changes; open pages compare it to reload themselves."""
    return str(max(int(f.stat().st_mtime) for f in (BASE_DIR / "static").iterdir() if f.is_file()))


def _page(name: str) -> HTMLResponse:
    """Serve a page with ?v=<mtime> on local assets so browsers never run stale JS/CSS,
    and never from cache (phones keep old pages around otherwise)."""
    html = (BASE_DIR / "static" / name).read_text("utf-8")

    def bust(m):
        f = BASE_DIR / "static" / m.group(1)
        v = int(f.stat().st_mtime) if f.exists() else 0
        return f'/static/{m.group(1)}?v={v}"'
    html = _STATIC_REF.sub(bust, html).replace(
        "<head>", f'<head>\n<meta name="pv-version" content="{_ui_version()}">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index():
    return _page("index.html")


@app.get("/desk")
async def desk_page():
    """Full-screen status for a phone left on the desk (also what the iOS app shows)."""
    return _page("desk.html")


@app.get("/api/desk")
async def desk_data():
    ov = await asyncio.to_thread(
        lambda: build_overview(linked_projects(), session_index.sessions(),
                               hidden=hidden_sessions.snapshot(), keep_all=True))
    snap = await asyncio.to_thread(usage_tracker.snapshot)
    return {**desk_view(ov), "usage": desk_usage(snap, int(time.time() * 1000)), "version": _ui_version()}


@app.get("/api/version")
async def ui_version():
    return {"version": _ui_version()}


_TAILSCALE_NET = ipaddress.IPv4Network("100.64.0.0/10")
_BENCHMARK_NET = ipaddress.IPv4Network("198.18.0.0/15")


def _classify_ipv4(ip_str: str) -> str:
    """Classify an IPv4 string as 'tailscale', 'lan', or '' (ignore)."""
    try:
        addr = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return ""
    if (addr.is_loopback or addr.is_link_local or addr.is_unspecified
            or addr.is_multicast or addr in _BENCHMARK_NET):
        return ""
    if addr in _TAILSCALE_NET:
        return "tailscale"
    if addr.is_private:
        return "lan"
    return ""


def _iface_rank(iface: str) -> int:
    if iface.startswith(("en", "eth", "wl")):
        return 0
    if iface.startswith("bridge"):
        return 1
    if iface.startswith(("utun", "tun", "tap", "ppp")):
        return 3
    return 2


def _parse_network_interfaces(ifconfig_text: str, primary_ip: str = "") -> dict:
    """Extract prioritized LAN and Tailscale IPv4 addresses from ifconfig output."""
    lan_ranked = []
    tailscale_ips = []
    seen_lan = set()
    seen_ts = set()
    current_iface = ""

    if _classify_ipv4(primary_ip) == "lan":
        lan_ranked.append((-1, primary_ip))
        seen_lan.add(primary_ip)

    for line in ifconfig_text.splitlines():
        m_iface = re.match(r"^([A-Za-z0-9._-]+):\s", line)
        if m_iface:
            current_iface = m_iface.group(1)
            continue
        m_inet = re.match(r"^\s+inet\s+(\d+\.\d+\.\d+\.\d+)\b", line)
        if not m_inet:
            continue
        ip_str = m_inet.group(1)
        kind = _classify_ipv4(ip_str)
        if kind == "tailscale" and ip_str not in seen_ts:
            seen_ts.add(ip_str)
            tailscale_ips.append(ip_str)
        elif kind == "lan" and ip_str not in seen_lan:
            seen_lan.add(ip_str)
            lan_ranked.append((_iface_rank(current_iface), ip_str))

    lan_ranked.sort(key=lambda item: item[0])
    lan_ips = [ip for _, ip in lan_ranked]
    return {
        "localhost": "localhost",
        "lan_ip": lan_ips[0] if lan_ips else None,
        "lan_ips": lan_ips,
        "tailscale_ip": tailscale_ips[0] if tailscale_ips else None,
        "tailscale_ips": tailscale_ips,
    }


def _detect_network_addresses() -> dict:
    """Detect localhost, LAN IPv4, Tailscale IPv4 and optional MagicDNS hostname."""
    primary_ip = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
    except OSError:
        pass

    ifconfig_text = ""
    try:
        res = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            ifconfig_text = res.stdout
    except (OSError, subprocess.SubprocessError):
        pass

    result = _parse_network_interfaces(ifconfig_text, primary_ip=primary_ip)

    tailscale_dns = None
    try:
        ts = subprocess.run(["tailscale", "status", "--json"],
                            capture_output=True, text=True, timeout=0.6)
        if ts.returncode == 0 and ts.stdout.strip():
            data = json.loads(ts.stdout)
            dns_name = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
            if dns_name:
                tailscale_dns = dns_name
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    result["tailscale_dns"] = tailscale_dns
    return result


def _uvicorn_arg(name: str, default: str) -> str:
    """A value uvicorn was started with (`--host x` / `--host=x`)."""
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def _bind_host() -> str:
    return _uvicorn_arg("--host", "127.0.0.1")


@app.get("/api/access")
async def jarvis_access(request: Request):
    """Addresses that open Jarvis on another device, token included. This machine only."""
    if not request.scope.get("state", {}).get("local"):
        return {"error": "只能在运行 Jarvis 的电脑上查看"}
    if _bind_host() in ("127.0.0.1", "localhost", "::1"):
        return {"urls": [], "local_only": True}
    info = await asyncio.to_thread(_detect_network_addresses)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    hosts = [("局域网", info.get("lan_ip")), ("Tailscale", info.get("tailscale_ip")),
             ("MagicDNS", info.get("tailscale_dns"))]
    return {"urls": [{"label": label, "url": with_token(_access_base(request.url.scheme, h, port))}
                     for label, h in hosts if h], "local_only": False}


def _access_base(scheme: str, host: str, port: int) -> str:
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    return f"{scheme}://{host}/" if default else f"{scheme}://{host}:{port}/"


@app.get("/api/models")
async def models_menu():
    """Model choices the task box offers (built-in, or data/models.json)."""
    import usage
    return {"antigravity": usage.AGY_MENU}


@app.get("/api/network-addresses")
async def get_network_addresses(request: Request):
    info = await asyncio.to_thread(_detect_network_addresses)
    default_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return {**info, "default_port": default_port}


@app.get("/api/overview")
async def get_overview():
    data = await asyncio.to_thread(
        lambda: build_overview(linked_projects(), session_index.sessions(),
                               hidden=hidden_sessions.snapshot()))
    data["indexing"] = session_index.scanning and not session_index.files
    data["usage"] = await asyncio.to_thread(usage_tracker.snapshot)
    return data


@app.get("/api/sessions")
async def list_sessions(offset: int = 0, limit: int = 30, agent: str = "", project: str = "",
                        archived: bool = False):
    """All sessions (not only the live ones), newest first, a page at a time;
    archived=1 lists the archived ones."""
    return await asyncio.to_thread(
        lambda: session_page(linked_projects(), session_index.sessions(),
                             max(0, offset), max(1, min(limit, 200)), agent, project,
                             archived=archived))


@app.get("/api/usage")
async def get_usage():
    return await asyncio.to_thread(usage_tracker.snapshot)


@app.post("/api/usage/refresh")
async def refresh_usage():
    """Claude: a one-word Haiku turn; Antigravity: `agy /usage` (Codex is read from its logs)."""
    claude_err, agy_err = await asyncio.gather(asyncio.to_thread(usage_tracker.probe_claude),
                                               asyncio.to_thread(usage_tracker.refresh_agy))
    snap = await asyncio.to_thread(usage_tracker.snapshot, False)
    return {**snap, "error": claude_err, "agy_error": agy_err}


@app.get("/api/projects/{project_id}/live")
async def get_project_live(project_id: int):
    return await asyncio.to_thread(
        lambda: project_live(linked_projects(), session_index.sessions(), project_id,
                             existing_session_ids(CLAUDE_PROJECTS, CODEX_HOME),
                             hidden=hidden_sessions.snapshot()))


@app.get("/api/projects/{project_id}/list")
async def get_project_list(project_id: int, agent: str = "", q: str = "",
                           recent_offset: int = 0, recent_limit: int = 50,
                           archived_offset: int = 0, archived_limit: int = 30):
    return await asyncio.to_thread(
        lambda: project_work_list(
            linked_projects(), session_index.sessions(), project_id,
            existing_session_ids(CLAUDE_PROJECTS, CODEX_HOME),
            hidden=hidden_sessions.snapshot(), agent=agent, query=q,
            recent_offset=max(0, recent_offset), recent_limit=max(1, min(recent_limit, 200)),
            archived_offset=max(0, archived_offset), archived_limit=max(1, min(archived_limit, 200)),
        ))


@app.post("/api/sessions/{sid}/effort")
async def set_session_effort(sid: str, body: dict):
    """The effort every turn Jarvis sends to this session runs at ("" = default)."""
    from session_prefs import prefs
    s = next((x for x in session_index.sessions() if x["sid"] == sid), None)
    agent = (s or {}).get("agent") or body.get("agent") or "claude"
    if not prefs.set_effort(sid, agent, str(body.get("effort") or "")):
        return {"ok": False, "error": "这个执行者不支持这个档位"}
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"ok": True, "effort": prefs.effort(sid)}


@app.post("/api/sessions/{sid}/archive")
async def archive_session(sid: str, body: dict = None):
    """Archive a session from the panel (Jarvis only; the apps' data is untouched):
    it leaves "now" and the board for 全部 › 已归档. New activity brings it back."""
    archived_sessions.hide(sid, (body or {}).get("archived", True))
    await ws_manager.broadcast({"type": "overview_changed"})
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"success": True}


@app.post("/api/sessions/{sid}/hide")
async def hide_session(sid: str, body: dict = None):
    """Put a session away from the board (Jarvis only); it returns on new activity."""
    hidden_sessions.hide(sid, (body or {}).get("hidden", True))
    await ws_manager.broadcast({"type": "overview_changed"})
    return {"success": True}


@app.post("/api/sessions/{sid}/reveal")
async def reveal_session(sid: str, request: Request):
    """From the desk display: wake this Mac and open the session where it lives
    (once the Mac is unlocked, if it is locked). See reveal.py."""
    s = _session_details(sid)[0]
    port = request.url.port or 80
    jarvis = f"http://localhost:{port}/"
    if not s:
        p = {"how": "jarvis", "open": [jarvis]} if sid.startswith("task:") else None
    else:
        p = await asyncio.to_thread(reveal.plan, s, headless(s), jarvis)
    if not p:
        return {"ok": False, "how": "none", "locked": False}
    return await asyncio.to_thread(reveal.reveal, p)


# ─── Sessions: read the conversation, send a new instruction ────────────────

_TASK_SID_RE = re.compile(r"^task:(\d+):(\d+)$")


def _session_details(sid: str, task_id: Optional[int] = None):
    """(session, project, active_task, persisted, tail_task, queued_tasks)."""
    sessions = session_index.sessions()
    projects = linked_projects(sessions)
    m = _TASK_SID_RE.match(sid or "")
    if m:
        pid, tid = int(m.group(1)), int(m.group(2))
        p = next((x for x in projects if x["id"] == pid), None)
        t = next((x for x in (p or {}).get("tasks", []) if x["id"] == tid), None)
        if not p or not t:
            return None, None, None, False, None, []
        if t.get("session_id"):
            return _session_details(t["session_id"], tid)
        line = t.get("line")
        ts = [x for x in p.get("tasks", [])
              if (line and x.get("line") == line)
              or x["id"] == tid
              or x.get("predecessor_id") == tid
              or x.get("line") == tid]
        ts.sort(key=lambda x: x["id"])
        for x in reversed(ts):
            if x.get("session_id") and not x.get("session_linked"):
                return _session_details(x["session_id"], tid)
        first_id = ts[0]["id"]
        queued = [x for x in ts if x["status"] == "pending"
                  and (x.get("predecessor_id") or x["id"] != first_id)]
        active = (next((x for x in reversed(ts) if x["status"] == "in_progress"), None)
                  or next((x for x in reversed(ts) if x not in queued), None)
                  or ts[-1])
        persisted = not active.get("session_linked") and active.get("source") != "session"
        return None, p, active, persisted, ts[-1], queued

    s = next((x for x in sessions if x["sid"] == sid), None)
    hits = [(p, t) for p in projects for t in p.get("tasks", []) if t.get("session_id") == sid]
    if hits:
        p, last = hits[-1]
        persisted = not last.get("session_linked") and last.get("source") != "session"
        if not persisted:
            p, t = next(((p, t) for p, t in hits if t["id"] == task_id), hits[-1])
            return s, p, t, False, t, []
        ts = [t for _, t in hits]
        first_id = ts[0]["id"]
        queued = [t for t in ts if t["status"] == "pending"
                  and (t.get("predecessor_id") or t["id"] != first_id)]
        active = (next((t for t in reversed(ts) if t["status"] == "in_progress"), None)
                  or next((t for t in reversed(ts) if t not in queued), None)
                  or last)
        return s, p, active, True, last, queued
    p = None
    if s:
        info = ProjectResolver(projects).resolve(s.get("origin") or s.get("cwd"))
        p = next((x for x in projects if x["id"] == info["project_id"]), None)
    return s, p, None, False, None, []


def _session_context(sid: str, task_id: Optional[int] = None):
    """(session, project, task, persisted) — task is the Jarvis task this session ran
    (the given one when a session did several)."""
    s, p, task, persisted, _, _ = _session_details(sid, task_id)
    return s, p, task, persisted


def _send_mode(s, p, task, persisted) -> str:
    """task = queue a follow-up task (worker, git flow); direct = resume in place."""
    if task and task.get("status") in ("todo", "archived"):
        return ""
    agent = (s or {}).get("agent") or (task_agent(task) if task else "") or ("claude" if task else "")
    if task and persisted and p and agent in ("claude", "codex", "antigravity"):
        return "task"
    if s and agent in ("claude", "codex", "antigravity") and (s.get("path") or agent == "antigravity"):
        return "direct"
    return ""


def _format_task_tool(raw: str) -> tuple:
    raw = (raw or "").strip()
    if raw.startswith("[tool_use]"):
        raw = raw[10:].strip()
    m = re.match(r"^([A-Za-z0-9_-]+)(?::\s*|\s+)(.*)$", raw, re.DOTALL)
    name, arg = (m.group(1), " ".join(m.group(2).split())) if m else (raw, "")
    n = name.split("__")[-1].lower()
    base = os.path.basename(arg.split()[0].rstrip("/")) if arg else ""
    if n in ("bash", "shell", "run_command", "exec_command"):
        t = ("$ " + arg)[:60] if arg else "$ " + name
    elif n in ("edit", "write", "multiedit", "notebookedit", "write_file", "edit_file", "apply_patch"):
        t = f"改 {base or arg[:40]}"
    elif n in ("read", "read_file", "view_file"):
        t = f"读 {base or arg[:40]}"
    elif n in ("grep", "glob", "grep_search", "find_by_name"):
        t = f"搜 {arg[:40]}"
    elif n in ("task", "agent"):
        t = f"派子代理 {arg[:30]}".strip()
    elif n.startswith("websearch") or n == "search_web":
        t = f"搜网页 {arg[:30]}".strip()
    elif n.startswith("webfetch") or n == "read_url_content":
        t = "读网页"
    else:
        t = f"{name} {arg[:40]}".strip()
    return t, tool_cat(name, t)


def _task_conv_fallback(project_id: int, task: dict, p: Optional[dict] = None) -> dict:
    """Synthesize conversation rounds from Jarvis task records and task_logs when no
    session .jsonl file exists on disk (e.g. MCP tasks, older tasks, or tasks without session_id)."""
    if not task:
        return {"rounds": [], "start": 0, "total": 0}
    line = task.get("line")
    sid = task.get("session_id")
    all_tasks = (p or {}).get("tasks") or store.get_tasks(project_id)
    if line:
        ts = [x for x in all_tasks if x.get("line") == line or x["id"] == task["id"]]
    elif sid and not task.get("session_linked"):
        ts = [x for x in all_tasks if x.get("session_id") == sid]
    else:
        ts = [task]
    ts.sort(key=lambda x: x["id"])
    first_id = ts[0]["id"]
    non_queued = [x for x in ts if not (x["status"] == "pending" and (x.get("predecessor_id") or x["id"] != first_id))]
    rounds = []
    last_model = task.get("model") or ""
    for t in non_queued:
        logs = store.get_logs(project_id, t["id"], limit=300)
        summary = (t.get("summary") or "").strip()
        if t["status"] in ("todo", "pending", "review") and not logs and not summary:
            continue
        at = _iso_ms(t.get("started_at")) or _iso_ms(t.get("created_at"))
        end = _iso_ms(t.get("completed_at")) or at
        items = []
        model = t.get("model") or ""
        for entry in logs:
            ltype = entry.get("type") or "message"
            msg = (entry.get("content") or entry.get("message") or "").strip()
            if not msg:
                continue
            if ltype == "init" or msg.startswith("[init]"):
                meta_model = (entry.get("meta") or {}).get("model")
                if meta_model:
                    model = meta_model
                else:
                    mm = re.search(r"model=([^\s\[]+)", msg)
                    if mm:
                        model = mm.group(1)
                continue
            if ltype in ("git", "info", "result", "diag", "tool_ok") or msg.startswith(
                    ("[git]", "[output]", "[result]", "[tool_ok]", "[DIAG]",
                     "Running in:", "Running Antigravity in:", "Running Codex in:")):
                continue
            if ltype == "tool_use" or msg.startswith("[tool_use]"):
                step_t, cat = _format_task_tool(msg)
                if cat != "internal":
                    items.append({"k": "tool", "t": step_t, "c": cat})
                continue
            if ltype in ("tool_error", "error") or msg.startswith("[tool_error]"):
                if items and items[-1]["k"] == "tool":
                    items[-1]["err"] = True
                else:
                    clean = re.sub(r"^\[tool_error\]\s*", "", msg).strip()
                    if clean:
                        items.append({"k": "tool", "t": clean[:80], "c": "other", "err": True})
                continue
            if ltype == "message":
                items.append({"k": "text", "t": msg[:12000]})
        if summary:
            last_text = next((i["t"].strip() for i in reversed(items) if i["k"] == "text"), "")
            if last_text != summary:
                items.append({"k": "text", "t": summary[:12000]})
        if model:
            last_model = model
        urls = _image_urls(t.get("images"))
        rounds.append({
            "ask": (t.get("description") or "")[:8000],
            "at": at,
            "end": end,
            "images": len(urls),
            "imgs": [],
            **({"image_urls": urls} if urls else {}),
            "items": items,
            "interrupted": t.get("status") == "cancelled",
            "model": model,
            "effort": "",
        })
    return {"rounds": rounds, "start": 0, "total": len(rounds), "model": last_model}


def _task_line(p, task) -> Optional[int]:
    if not task:
        return None
    if task.get("line"):
        return task["line"]
    sid = task.get("session_id")
    if sid and p and not task.get("session_linked"):
        for t in p.get("tasks", []):
            if t.get("session_id") == sid and t.get("line"):
                return t["line"]
    return None


def _merge_info(p, task):
    """Unmerged work on the task's branch (gitflow), for the panel's "合并到 main"."""
    line = _task_line(p, task)
    if not p or not line or not gitflow.is_repo(p.get("path") or ""):
        return None
    info = gitflow.ahead(p["path"], line)
    return info["commits"] and {"line": line, "branch": info["branch"], "commits": info["commits"],
                                "files": info["files"], "test": bool(p.get("settings", {}).get("test_command"))}


@app.post("/api/projects/{project_id}/tasks/{task_id}/merge")
async def merge_task_line(project_id: int, task_id: int):
    """Run the project's tests in the task's worktree, then merge its branch into main."""
    task = store.get_task(project_id, task_id)
    p = next((x for x in store.get_all_projects() if x["id"] == project_id), None)
    line = _task_line(p, task)
    if not task or not p or not line:
        return {"ok": False, "error": "这个任务没有待合并的分支"}
    tasks = [t for t in store.get_tasks(project_id)
             if t.get("line") == line or (task.get("session_id") and t.get("session_id") == task.get("session_id"))]
    if any(t["status"] == "in_progress" for t in tasks):
        return {"ok": False, "error": "这条线上还有任务在执行，等它停下再合并"}
    first = min(tasks, key=lambda t: t["id"])
    res = await asyncio.to_thread(gitflow.merge, p["path"], line, first["description"].split("\n")[0],
                                  p.get("settings", {}).get("test_command", ""))
    if res.get("ok"):
        now = datetime.now().isoformat()
        for t in tasks:
            if t.get("merge_state") in ("pending", None, "none"):
                store.update_task(project_id, t["id"], merge_state="merged", merged_at=now)
        await ws_manager.broadcast({"type": "task_update", "task_id": task_id,
                                    "project_id": project_id, "status": task["status"]})
    return res


@app.get("/api/sessions/{sid}")
async def get_session(sid: str, before: Optional[int] = None, limit: int = 6,
                      task_id: Optional[int] = None):
    q0 = sender.queue(sid)
    run0 = sender.status(sid)
    if q0 and not sender.busy(sid) and (run0.get("state") != "error" or q0[0]["at"] > run0.get("ended_at", 0)):
        await _drain_direct_queue(sid)
    s, p, task, persisted, _, queued_tasks = await asyncio.to_thread(_session_details, sid, task_id)
    if not s and not task:
        return {"error": "Session not found"}
    eff_sid = (s or {}).get("sid") or (task.get("session_id") if task and task.get("session_id") else sid)
    agent = (s or {}).get("agent") or (task_agent(task) if task else "") or "claude"
    conv = {"rounds": [], "start": 0, "total": 0}
    path = ((s or {}).get("path") or await asyncio.to_thread(find_log, eff_sid, agent)) if not eff_sid.startswith("task:") else None
    mcp_task_id = (task_id or task["id"]) if task and task.get("session_linked") else None
    if path:
        try:
            conv = await asyncio.to_thread(transcripts.read, path, agent, before,
                                           max(1, min(limit, 30)), mcp_task_id)
        except OSError:
            pass
    used_fallback = False
    if not conv["rounds"] and task and p and before is None:
        conv = await asyncio.to_thread(_task_conv_fallback, p["id"], task, p)
        used_fallback = bool(conv["rounds"])
    now_ms = int(datetime.now().timestamp() * 1000)
    run = sender.status(eff_sid)
    if run.get("state") == "error" and (s or {}).get("last_at", 0) > run.get("ended_at", 0):
        # the session moved on (e.g. continued in the terminal): the failed send is history
        await asyncio.to_thread(_drop_failed_send, eff_sid, p, (s or {}).get("last_at", 0))
        run = sender.status(eff_sid)
    if before is None:
        if run.get("state") == "running" and (run.get("text") or run.get("images")):
            rb = run.get("rounds_before")
            in_log = (conv["total"] > rb) if rb is not None else bool(
                conv["rounds"] and conv["rounds"][-1]["at"] >= run["started_at"] - 1000)
            if not in_log:
                conv = {**conv, "rounds": [*conv["rounds"], _in_flight_round(run.get("text") or "", run["started_at"], run.get("images"))],
                        "total": conv["total"] + 1}
        elif run.get("state") == "cancelled" and conv["rounds"] \
                and conv["rounds"][-1]["at"] >= run.get("started_at", 0) - 1000:
            conv["rounds"][-1] = {**conv["rounds"][-1], "interrupted": True}    # you stopped it
        elif run.get("state") == "error" and (run.get("text") or run.get("images")):
            # the CLI turned it away before it reached the log: keep the instruction in view
            rb = run.get("rounds_before")
            in_log = (conv["total"] > rb) if rb is not None else bool(
                conv["rounds"] and conv["rounds"][-1]["at"] >= run["started_at"] - 1000)
            if not in_log:
                conv = {**conv, "rounds": [*conv["rounds"], {
                    **_in_flight_round(run.get("text") or "", run["started_at"], run.get("images")), "failed": run.get("error") or "发送失败"}],
                        "total": conv["total"] + 1}
        elif task and persisted and task["status"] == "in_progress":
            started_ms = _iso_ms(task.get("started_at")) or _iso_ms(task.get("created_at")) or now_ms
            rb = task.get("rounds_before")
            in_log = (conv["total"] > rb) if rb is not None else bool(
                conv["rounds"] and conv["rounds"][-1]["at"] >= started_ms - 1000)
            if not in_log:
                conv = {**conv, "rounds": [*conv["rounds"], _in_flight_round(task["description"], started_ms, task.get("images"))],
                        "total": conv["total"] + 1}
    state = session_state(s, now_ms) if s else "idle"
    if task and persisted and task["status"] != "in_progress" and state in ("wait", "done", "quiet", "run") \
            and not _moved_on(s, task):
        state = "idle"          # the worker's run is over; what its log last said doesn't matter
    if sender.busy(eff_sid) or (task and task["status"] == "in_progress"):
        state = "run"
    mode = _send_mode(s, p, task, persisted)
    busy = sender.busy(eff_sid) or state == "run" or (task and task["status"] == "in_progress" and mode == "task")
    queue = ([{
        "id": f"t{t['id']}", "task_id": t["id"], "text": t["description"],
        "at": _iso_ms(t.get("created_at")), "agent": task_agent(t) or agent, "mode": "task",
        **({"images": _image_urls(t.get("images"))} if _image_urls(t.get("images")) else {}),
    } for t in queued_tasks] + sender.queue(eff_sid))
    has_log = bool(path) or agent == "antigravity" or used_fallback or bool(
        task and task["status"] in ("todo", "pending", "review"))
    return {
        "session": {
            "sid": eff_sid, "agent": agent, "title": (s or {}).get("title") or (task or {}).get("description", "")[:80],
            "state": state, "model": conv.get("model") or (s or {}).get("model", ""),
            "effort": conv.get("effort", ""), "cwd": (s or {}).get("origin") or (s or {}).get("cwd", ""),
            "last_at": (s or {}).get("last_at", 0), "local_id": (s or {}).get("local_id", ""),
            "summary": (s or {}).get("last_text", "") if agent == "antigravity" else "",
            "turns": (s or {}).get("turns"),
            "archived": bool((s or {}).get("archived")),
            "archived_here": bool(s) and eff_sid in archived_sessions.snapshot(),
            "effort_pick": _effort_pick(eff_sid),
            "ctx": (s or {}).get("ctx", 0), "ctx_max": (s or {}).get("ctx_max", 0),
            "ctx_level": context_level(s) if s else 0,
            "hidden": bool(s) and hidden_sessions.snapshot().get(eff_sid, 0) >= (s or {}).get("last_at", 0),
        },
        "project": p and {"id": p["id"], "name": p["name"]},
        "task": task and {"id": task["id"], "status": task["status"], "agent": task_agent(task) or "",
                          "description": task["description"],
                          "summary": task.get("summary") or "", "error": task.get("error") or "",
                          "linked": bool(task.get("session_linked")), "source": task.get("source") or "",
                          "images": [img.get("url") for img in (task.get("images") or []) if img.get("url")],
                          "plan": task.get("plan_content") or "" if task["status"] == "review" else "",
                          "questions": len(task.get("plan_questions") or []),
                          "subtasks": len(task.get("plan_subtasks") or []),
                          "fallback": used_fallback},
        "conversation": conv,
        "merge": await asyncio.to_thread(_merge_info, p, task),
        "log": has_log,
        "send": {"mode": mode, "busy": bool(busy), "run": run, "queue": queue,
                 # Jarvis runs this session right now: it can stop it, and a new
                 # instruction interrupts instead of waiting
                 "ours": bool(sender.busy(eff_sid) or (task and task["status"] == "in_progress"))},
    }


def _session_git_path(sid: str, task_id=None):
    """(folder, gone-info): a worker task's worktree, otherwise the session's start
    folder; gone-info when the task's worktree has been cleaned up."""
    s, p, task, persisted, _, _ = _session_details(sid, task_id)
    line = _task_line(p, task) if task and p else None
    repo = (p or {}).get("path") or ""
    if line and repo and gitflow.is_repo(repo):
        wt = gitflow.worktree_path(repo, line)
        if not os.path.isdir(wt):
            same = [t for t in (p.get("tasks") or []) if t.get("line") == line]
            return "", {"branch": gitflow.branch_name(line), "gone": True,
                        "merged": any(t.get("merge_state") == "merged" for t in same),
                        "main": gitflow.main_branch(repo)}
        return wt, None
    return (s or {}).get("origin") or (s or {}).get("cwd") or "", None


@app.get("/api/sessions/{sid}/git")
async def session_git(sid: str, task_id: Optional[int] = None):
    """The branch this session works on, against main."""
    def build():
        path, gone = _session_git_path(sid, task_id)
        return gone or gitflow.branch_status(path)
    return await asyncio.to_thread(build)


@app.get("/api/sessions/{sid}/git/commit")
async def session_git_commit(sid: str, sha: str, task_id: Optional[int] = None):
    """The files one commit on the session's branch changed."""
    def build():
        path, gone = _session_git_path(sid, task_id)
        return {"files": []} if gone else gitflow.commit_files(path, sha)
    return await asyncio.to_thread(build)


@app.get("/api/sessions/{sid}/image")
async def session_image(sid: str, o: int, i: Optional[int] = None, j: Optional[int] = None):
    """An image inside the session's own log (pasted, or returned by a tool)."""
    def find():
        s = next((x for x in session_index.sessions() if x["sid"] == sid), None)
        agent = (s or {}).get("agent") or "claude"
        if agent not in ("claude", "codex"):
            return None
        path = (s or {}).get("path") or find_log(sid, agent)
        return image_at(path, agent, o, i, j) if path else None
    got = await asyncio.to_thread(find)
    if not got:
        return Response(status_code=404)
    mime, data = got
    # a log line never changes once written, so the image at an offset is stable
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=604800"})


@app.post("/api/sessions/{sid}/send")
async def send_to_session(sid: str, body: dict):
    text = (body.get("text") or "").strip()
    images = _clean_images(body.get("images"))
    if not text and not images:
        return {"error": "指令不能为空"}
    if sid in archived_sessions.snapshot():
        archived_sessions.hide(sid, False)        # writing to it again: back from the archive
    s, p, task, persisted, tail_task, _ = await asyncio.to_thread(_session_details, sid)
    mode = _send_mode(s, p, task, persisted)
    if not mode:
        return {"error": "这个会话不能从网页续写"}
    eff_sid = (s or {}).get("sid") or (task.get("session_id") if task and task.get("session_id") else sid)
    agent = (s or {}).get("agent") or (task_agent(task) if task else "") or "claude"
    path = ((s or {}).get("path") or await asyncio.to_thread(find_log, eff_sid, agent)) if not eff_sid.startswith("task:") else None
    want_queue = bool(body.get("queue"))
    steered = False
    if mode == "task":
        pred = tail_task or task
        if not want_queue and pred["status"] == "in_progress" and pred.get("source") != "session":
            # a new instruction while the worker runs: stop this turn and go on with it
            await worker_manager.cancel_task(p["id"], pred["id"])
            for _ in range(75):
                pred = store.get_task(p["id"], pred["id"]) or pred
                if pred["status"] != "in_progress":
                    break
                await asyncio.sleep(0.2)
            steered = True
        t_agent = task_agent(pred) or "claude"
        line = pred.get("line") or pred["id"]
        rb = await asyncio.to_thread(_round_count, eff_sid, t_agent, path) if not eff_sid.startswith("task:") else 0
        tasks = store.get_tasks(p["id"])
        tasks_by_id = {t["id"]: t for t in tasks}
        same_line_busy = any(
            (t["status"] == "in_progress" or (t["status"] == "pending" and t["id"] == pred["id"]))
            and (store._effective_line(tasks_by_id, t) == line
                 or (eff_sid and not eff_sid.startswith("task:") and t.get("session_id") == eff_sid))
            for t in tasks
        )
        cap = store.project_max_parallel(p)
        proj_workers = sum(1 for t in tasks if t["status"] == "in_progress" and t.get("source") != "session")
        global_active = len([x for x in worker_manager.worker_tasks.values() if not x.done()])
        busy = same_line_busy or proj_workers >= cap or global_active >= worker_manager.max_workers
        extra = {"line": line, "rounds_before": rb}
        if not eff_sid.startswith("task:"):
            extra["session_id"] = eff_sid
        new = store.add_task(p["id"], text or IMAGES_ONLY_PROMPT, predecessor_id=pred["id"],
                             images=images, agent=t_agent, extra=extra)
        await ws_manager.broadcast({"type": "task_created", "task": new, "project_id": p["id"]})
        if not busy:
            worker_manager.wake()
        await ws_manager.broadcast({"type": "session_changed", "sid": sid})
        if eff_sid != sid:
            await ws_manager.broadcast({"type": "session_changed", "sid": eff_sid})
        return {"mode": "task", "task": new, "queued": busy, "steered": steered}
    now_ms = int(datetime.now().timestamp() * 1000)
    cwd = (s or {}).get("origin") or (s or {}).get("cwd")
    skip = bool(p and p.get("settings", {}).get("skip_permissions"))
    model, effort = await asyncio.to_thread(session_settings, sid, s["agent"], path or "")
    if not want_queue and sender.busy(sid):
        # our own run is going: stop it and send this instead (the session keeps its history)
        steered = await _stop_session(sid)
    busy = not steered and (sender.busy(sid) or (s and session_state(s, now_ms) == "run")
                            or bool(sender.queue(sid)))
    try:
        extra_img = {"images": images} if images else {}
        if busy:
            item = sender.enqueue(sid, s["agent"], cwd, text, skip, model, effort,
                                  project_id=p["id"] if p else None, **extra_img)
            if not sender.busy(sid) and not (s and session_state(s, now_ms) == "run"):
                await _drain_direct_queue(sid)
            else:
                await ws_manager.broadcast({"type": "session_changed", "sid": sid})
            return {"mode": "direct", "queued": True, "item": item, "queue": sender.queue(sid)}
        rb = await asyncio.to_thread(_round_count, sid, s["agent"], path)
        run = sender.send(sid, s["agent"], cwd, text, skip, model, effort, rounds_before=rb, **extra_img)
    except ValueError as e:
        return {"error": str(e)}
    if p:   # the reply is a piece of work in the project too
        new = store.add_task(p["id"], text or IMAGES_ONLY_PROMPT, images=images, agent=s["agent"], extra={
            "status": "in_progress", "session_id": sid, "source": "session",
            "started_at": datetime.now().isoformat(), "rounds_before": rb})
        if new:
            await ws_manager.broadcast({"type": "task_created", "task": new, "project_id": p["id"]})
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"mode": "direct", "queued": False, "run": run, "steered": steered}


def _effort_pick(sid: str) -> str:
    from session_prefs import prefs
    return prefs.effort(sid)


def _moved_on(s, task) -> bool:
    """The session was used again (terminal, desktop app) after its task ended."""
    return moved_on(s, _iso_ms(task.get("completed_at")) if task else 0)


def _drop_failed_send(sid: str, p, before_ms: int = None) -> bool:
    """Forget a failed send from the panel, and close its reply task as cancelled."""
    run = sender.status(sid)
    if not sender.clear(sid, before_ms):
        return False
    for t in store.get_tasks(p["id"]) if p else []:
        if (t.get("session_id") == sid and t.get("source") == "session" and t["status"] == "failed"
                and t["description"].strip() == (run.get("text") or "").strip()):
            store.update_task(p["id"], t["id"], status="cancelled", summary="没发出去，已忽略")
    return True


@app.post("/api/sessions/{sid}/dismiss")
async def dismiss_failed_send(sid: str):
    """✕ on a failed send: drop the error, the unsent bubble and the fork offer."""
    s, p, _, _, _, _ = await asyncio.to_thread(_session_details, sid)
    ok = await asyncio.to_thread(_drop_failed_send, sid, p)
    if ok:
        await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"ok": ok}


@app.post("/api/sessions/{sid}/fork")
async def fork_session(sid: str, body: dict):
    """Codex: the session is open in an interactive codex, so copy it into a new
    session and send the instruction there. The failed reply is closed in favour of it."""
    text = (body.get("text") or "").strip()
    images = _clean_images(body.get("images")) or _clean_images(sender.status(sid).get("images"))
    if not text and not images:
        return {"error": "指令不能为空"}
    s, p, _, _, _, _ = await asyncio.to_thread(_session_details, sid)
    if not s or s.get("agent") != "codex":
        return {"error": "只有 Codex 会话可以复制成新会话"}
    cwd = s.get("origin") or s.get("cwd")
    skip = bool(p and p.get("settings", {}).get("skip_permissions"))
    model, effort = await asyncio.to_thread(session_settings, sid, "codex", s.get("path") or "")
    try:
        extra_img = {"images": images} if images else {}
        new_sid = await asyncio.to_thread(sender.fork, sid, cwd, text, skip, model, effort, **extra_img)
    except ValueError as e:
        return {"error": str(e)}
    task = None
    if p:
        for t in store.get_tasks(p["id"]):
            if (t.get("session_id") == sid and t.get("source") == "session" and t["status"] == "failed"
                    and t["description"].strip() == text):
                store.update_task(p["id"], t["id"], status="cancelled", error=None,
                                  summary=f"已复制成新会话 {new_sid[:8]} 继续")
        task = store.add_task(p["id"], text or IMAGES_ONLY_PROMPT, images=images, agent="codex", extra={
            "status": "in_progress", "session_id": new_sid, "source": "session",
            "started_at": datetime.now().isoformat(), "rounds_before": 0, "forked_from": sid})
        if task:
            await ws_manager.broadcast({"type": "task_created", "task": task, "project_id": p["id"]})
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"sid": new_sid, "task": task}


@app.post("/api/sessions/{sid}/unqueue")
async def unqueue_session_send(sid: str, body: dict = None):
    qid = str((body or {}).get("id") or "")
    if not qid:
        return {"ok": False, "error": "Missing queue id"}
    if qid.startswith("t") and qid[1:].isdigit():
        tid = int(qid[1:])
        for p in store.get_all_projects():
            t = store.get_task(p["id"], tid)
            if not t or (t.get("session_id") != sid and not sid.startswith(f"task:{p['id']}:")):
                continue
            if t["status"] != "pending":
                return {"ok": False, "error": "这条指令已经开始执行，无法撤回"}
            pred_id = t.get("predecessor_id")
            for other in store.get_tasks(p["id"]):
                if other.get("predecessor_id") == tid:
                    store.update_task(p["id"], other["id"], predecessor_id=pred_id)
            store.delete_task(p["id"], tid)
            await ws_manager.broadcast({"type": "task_update", "task_id": tid,
                                        "project_id": p["id"], "status": "deleted"})
            await ws_manager.broadcast({"type": "session_changed", "sid": sid})
            return {"ok": True, "text": t.get("description", ""), "images": t.get("images") or []}
        return {"ok": False, "error": "找不到这条排队指令"}
    item = sender.unqueue(sid, qid)
    if not item:
        return {"ok": False, "error": "这条指令已经开始执行，无法撤回"}
    await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"ok": True, "text": item.get("text", ""), "images": item.get("raw_images") or []}


def _running_worker_task(sid: str):
    """(project id, task) of a worker task running in this session, or (None, None)."""
    for p in store.get_all_projects():
        for t in store.get_tasks(p["id"]):
            if t["status"] == "in_progress" and t.get("session_id") == sid and t.get("source") != "session":
                return p["id"], t
    return None, None


async def _stop_session(sid: str, wait_s: float = 15) -> bool:
    """Stop whatever Jarvis is running in this session (a panel reply or a worker
    task) and wait until it has let go of the session."""
    if sender.busy(sid):
        sender.cancel(sid)
        for _ in range(int(wait_s * 5)):
            if not sender.busy(sid):
                return True
            await asyncio.sleep(0.2)
        return not sender.busy(sid)
    pid, task = await asyncio.to_thread(_running_worker_task, sid)
    if not task:
        return False
    await worker_manager.cancel_task(pid, task["id"])
    for _ in range(int(wait_s * 5)):
        t = store.get_task(pid, task["id"])
        if not t or t["status"] != "in_progress":
            return True
        await asyncio.sleep(0.2)
    store.update_task(pid, task["id"], status="cancelled", completed_at=datetime.now().isoformat())
    return True


@app.post("/api/sessions/{sid}/cancel")
async def cancel_session_send(sid: str):
    ok = await _stop_session(sid)
    if ok:
        await ws_manager.broadcast({"type": "session_changed", "sid": sid})
    return {"success": ok}


@app.get("/api/projects")
async def list_projects():
    return {"projects": store.get_all_projects()}


@app.post("/api/projects")
async def create_project(body: dict, request: Request):
    name = body.get("name", "").strip()
    path = body.get("path", "").strip()
    if not name or not path:
        return {"error": "Name and path are required"}
    inject = body.get("inject", True) is not False
    if not inject and not os.path.isdir(os.path.expanduser(path)):
        return {"error": "Folder not found"}
    reserved = {request.url.port} if request.url.port else None
    lang = body.get("lang") if body.get("lang") in ("zh", "en") else "en"
    project = store.create_project(name, path, inject=inject, reserved_ports=reserved,
                                   lang=lang, port=request.url.port or 5100)
    await ws_manager.broadcast({"type": "project_created", "project": project})
    return {"project": project}


@app.put("/api/projects/{project_id}")
async def update_project(project_id: int, body: dict, request: Request):
    reserved = {request.url.port} if request.url.port else None
    try:
        result = store.update_project(project_id, reserved_ports=reserved, **body)
    except ValueError as e:
        return {"error": str(e)}
    if not result:
        return {"error": "Project not found"}
    return {"project": result}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: int):
    # Clean up uploaded images for this project
    upload_dir = UPLOADS_DIR / str(project_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)
    return {"success": store.delete_project(project_id)}


@app.get("/api/discover-projects")
async def discover_projects():
    """Scan ~/.claude/projects/ to find local Claude Code projects."""
    from pathlib import Path
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return {"projects": []}

    # Get existing project paths to mark already-imported ones
    existing_paths = set()
    for p in store.get_all_projects():
        existing_paths.add(p.get("path", ""))

    discovered = []
    for entry in sorted(claude_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
        if not entry.is_dir():
            continue
        dir_name = entry.name
        # Convert dir name back to filesystem path.
        # Claude converts /Users/me/code/my_app/Web App to
        # -Users-me-code-my-app-Web-App (all /, _, space → -)
        # We resolve by walking the filesystem segment by segment.
        project_path = _resolve_claude_dir_name(dir_name)
        if not project_path:
            continue

        # Count session files
        session_files = [f for f in entry.iterdir()
                         if f.suffix == ".jsonl" and not f.name.startswith("agent-")]
        if not session_files:
            continue

        # Extract metadata from most recent session
        most_recent = max(session_files, key=lambda f: f.stat().st_mtime)
        first_prompt = _extract_first_prompt(most_recent)

        project_name = os.path.basename(project_path)
        discovered.append({
            "path": project_path,
            "name": project_name,
            "session_count": len(session_files),
            "first_prompt": first_prompt,
            "last_active": datetime.fromtimestamp(
                most_recent.stat().st_mtime).isoformat(),
            "already_imported": project_path in existing_paths,
        })

    return {"projects": discovered}


def _resolve_claude_dir_name(dir_name: str) -> str:
    """Resolve a Claude projects dir name back to a real filesystem path.

    Claude converts paths like /Users/me/code/my_app/Web App into
    -Users-me-code-my-app-Web-App (replacing /, _, space, . with -).
    We use BFS trying each - as /, _, -, or . to find a valid path.
    """
    parts = dir_name.lstrip("-").split("-")
    if not parts:
        return ""

    # DFS building path segment by segment.
    # At each -, we try: it was / (segment boundary) or _, -, . (part of name).
    # Key insight: we accumulate characters into `segment` and only check
    # the filesystem when we try a / boundary.
    def solve(parent_dir, segment, idx):
        """parent_dir: confirmed existing dir, segment: current segment being built,
        idx: next part index to consume"""
        if idx >= len(parts):
            # No more parts — check if parent_dir/segment exists
            full = os.path.join(parent_dir, segment) if segment else parent_dir
            return full if os.path.isdir(full) else None

        part = parts[idx]

        # Option A: the - before this part was / (segment boundary)
        # Finalize current segment, start new one
        if segment:
            check = os.path.join(parent_dir, segment)
            if os.path.isdir(check):
                result = solve(check, part, idx + 1)
                if result:
                    return result

        # Option B: the - was _, -, or . (part of current segment name)
        for sep in ("_", "-", "."):
            new_segment = (segment + sep + part) if segment else part
            result = solve(parent_dir, new_segment, idx + 1)
            if result:
                return result

        return None

    return solve("/", "", 0) or ""


def _extract_first_prompt(jsonl_path) -> str:
    """Extract first user prompt from a Claude session JSONL file."""
    import json
    try:
        with open(jsonl_path) as f:
            for i, line in enumerate(f):
                if i > 50:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    msg = record.get("message", {})
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        content = msg.get("content", "")
                        if isinstance(content, str) and content:
                            return content[:80]
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception:
        pass
    return ""


@app.get("/api/projects/{project_id}/claude-md")
async def get_claude_md(project_id: int):
    path = store.get_project_path(project_id)
    if not path: return {"error": "Project not found"}
    return {"content": ProjectStore.read_claude_md(path),
            "path": os.path.join(path, "CLAUDE.md")}


@app.put("/api/projects/{project_id}/claude-md")
async def update_claude_md(project_id: int, body: dict):
    path = store.get_project_path(project_id)
    if not path: return {"error": "Project not found"}
    ProjectStore.write_claude_md(path, body.get("content", ""))
    return {"success": True}


@app.get("/api/projects/{project_id}/progress-md")
async def get_progress_md(project_id: int):
    path = store.get_project_path(project_id)
    if not path: return {"error": "Project not found"}
    return {"content": ProjectStore.read_progress_md(path),
            "path": os.path.join(path, "PROGRESS.md")}


@app.put("/api/projects/{project_id}/progress-md")
async def update_progress_md(project_id: int, body: dict):
    path = store.get_project_path(project_id)
    if not path: return {"error": "Project not found"}
    ProjectStore.write_progress_md(path, body.get("content", ""))
    return {"success": True}


# ─── Task API ────────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/tasks")
async def get_tasks(project_id: int):
    tasks = [t for t in store.get_tasks(project_id) if t.get("status") != "archived"]
    linked = await asyncio.to_thread(linked_projects)
    extra = {t["id"]: t["session_id"] for p in linked if p["id"] == project_id
             for t in p.get("tasks", []) if t.get("session_linked")}
    for t in tasks:
        if t["id"] in extra:
            t["session_id"], t["session_linked"] = extra[t["id"]], "mcp"
    for t in tasks:
        if (t.get("plan_content") and not t.get("plan_questions")
                and t["status"] in ("review", "done")):
            questions = extract_questions(t["plan_content"])
            if questions:
                store.update_task(project_id, t["id"],
                                  plan_questions=questions)
                t["plan_questions"] = questions
        # Auto-extract sub-tasks for plan tasks
        if (t.get("plan_mode") and t.get("plan_content")
                and not t.get("plan_subtasks")):
            from utils import extract_subtasks
            subtasks = extract_subtasks(t["plan_content"])
            if subtasks:
                store.update_task(project_id, t["id"],
                                  plan_subtasks=subtasks)
                t["plan_subtasks"] = subtasks
    # Attach logs from JSONL files (last 50 for list view)
    for t in tasks:
        t["logs"] = store.get_logs(project_id, t["id"], limit=50)
        t["agent"] = task_agent(t)
    return {"tasks": tasks, "stats": store.get_task_stats(project_id)}


@app.post("/api/projects/{project_id}/tasks/{task_id}/assign")
async def assign_task(project_id: int, task_id: int, body: dict):
    """Hand a Todo (or failed/cancelled) task to claude/codex/antigravity, or to "me"."""
    task = store.assign_task(project_id, task_id, body.get("agent", ""))
    if not task:
        return {"error": "Cannot assign this task"}
    if task["status"] == "pending":
        worker_manager.wake()
    await ws_manager.broadcast({"type": "task_update", "task_id": task_id,
                                "project_id": project_id, "status": task["status"]})
    return {"task": task}


@app.get("/api/projects/{project_id}/tasks/{task_id}/logs")
async def get_task_logs(project_id: int, task_id: int,
                        limit: int = 100, offset: int = 0):
    """Get logs for a specific task (paginated)."""
    logs = store.get_logs(project_id, task_id, limit=limit, offset=offset)
    count = store.get_log_count(project_id, task_id)
    return {"logs": logs, "total": count}


def _agy_extra(body: dict):
    """An Antigravity task can ask for a model other than the default (Hydrogen)."""
    from usage import AGY_CHOICES
    choice = body.get("agy_model") or ""
    return {"agy_model": choice} if body.get("agent") == "antigravity" and choice in AGY_CHOICES else None


@app.post("/api/projects/{project_id}/tasks")
async def create_task(project_id: int, body: dict):
    desc = body.get("description", "").strip()
    images = _clean_images(body.get("images"))
    if not desc and not images:
        return {"error": "Description is required"}
    task = store.add_task(project_id, desc or IMAGES_ONLY_PROMPT,
                          body.get("plan_mode", False),
                          body.get("predecessor_id"),
                          images=images,
                          model=body.get("model", ""),
                          agent=body.get("agent", ""),
                          extra=_agy_extra(body))
    if not task: return {"error": "Project not found"}
    if task["status"] == "pending":
        worker_manager.wake()
    await ws_manager.broadcast({
        "type": "task_created", "task": task, "project_id": project_id})
    return {"task": task}


@app.get("/api/projects/{project_id}/tasks/{task_id}")
async def get_task(project_id: int, task_id: int):
    task = store.get_task(project_id, task_id)
    if not task: return {"error": "Not found"}
    return {"task": task}


@app.post("/api/projects/{project_id}/tasks/{task_id}/cancel")
async def cancel_task(project_id: int, task_id: int):
    task = store.get_task(project_id, task_id)
    if task and task.get("source") == "session" and task["status"] == "in_progress":
        return {"success": sender.cancel(task.get("session_id") or "")}   # closed when the run ends
    return {"success": await worker_manager.cancel_task(project_id, task_id)}


@app.post("/api/projects/{project_id}/tasks/{task_id}/retry")
async def retry_task(project_id: int, task_id: int):
    return {"success": await worker_manager.retry_task(project_id, task_id)}


@app.delete("/api/projects/{project_id}/tasks/{task_id}")
async def delete_task(project_id: int, task_id: int):
    task = store.get_task(project_id, task_id)
    if task and task["status"] == "in_progress":
        await worker_manager.cancel_task(project_id, task_id)
    # Clean up uploaded images
    if task:
        for img in task.get("images") or []:
            try:
                p = img.get("path")
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
    return {"success": store.delete_task(project_id, task_id)}


@app.post("/api/projects/{project_id}/tasks/{task_id}/reopen")
async def reopen_task(project_id: int, task_id: int):
    task = store.get_task(project_id, task_id)
    if not task: return {"error": "Task not found"}
    if task["status"] != "done": return {"error": "Task is not done"}
    # Plan tasks go back to review; regular tasks go back to pending
    if task.get("plan_questions"):
        new_status = "review"
        store.update_task(project_id, task_id, status=new_status, completed_at=None)
    else:
        new_status = "pending"
        store.update_task(project_id, task_id, status=new_status,
                          completed_at=None, worker_id=None, started_at=None)
        worker_manager.wake()
    await ws_manager.broadcast({
        "type": "task_update", "task_id": task_id,
        "project_id": project_id, "status": new_status})
    return {"success": True}


@app.post("/api/projects/{project_id}/tasks/{task_id}/revise")
async def revise_plan(project_id: int, task_id: int, body: dict = None):
    """Accept user feedback on a plan and re-run it with the same session."""
    if body is None:
        body = {}
    task = store.get_task(project_id, task_id)
    if not task:
        return {"error": "Task not found"}
    if task["status"] != "review":
        return {"error": "Task not in review status"}
    feedback = body.get("feedback", "").strip()
    images = _clean_images(body.get("images"))
    if not feedback and not images:
        return {"error": "Feedback is required"}
    # Keep session_id so worker can --resume; reset plan fields
    updates = {
        "status": "pending",
        "plan_feedback": feedback or IMAGES_ONLY_PROMPT,
        "plan_content": None,
        "plan_questions": None,
        "plan_subtasks": None,
        "completed_at": None,
        "worker_id": None,
        "started_at": None,
    }
    if images:
        updates["images"] = [*(task.get("images") or []), *images]
    store.update_task(project_id, task_id, **updates)
    worker_manager.wake()
    await ws_manager.broadcast({
        "type": "task_update", "task_id": task_id,
        "project_id": project_id, "status": "pending"})
    return {"success": True}


@app.post("/api/projects/{project_id}/tasks/archive")
async def archive_tasks(project_id: int, body: dict = None):
    if body is None: body = {}
    days = body.get("days", 7)
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    tasks = store.get_tasks(project_id)
    archived = []
    for t in tasks:
        if t["status"] in ("done", "failed", "cancelled"):
            completed = t.get("completed_at") or t.get("started_at") or t.get("created_at", "")
            if completed and completed < cutoff:
                store.update_task(project_id, t["id"], status="archived", archived_from=t["status"])
                archived.append(t["id"])
    if archived:
        await ws_manager.broadcast({
            "type": "tasks_archived", "task_ids": archived,
            "project_id": project_id})
    return {"success": True, "archived_count": len(archived), "task_ids": archived}


def _thread_peers(project_id: int, task: dict) -> list:
    """Tasks sharing the same thread as `task` (for archiving/unarchiving a whole line)."""
    sid = task.get("session_id")
    if sid and not task.get("session_linked"):
        return [t for t in store.get_tasks(project_id)
                if t.get("session_id") == sid and not t.get("session_linked")]
    return [task]


@app.post("/api/projects/{project_id}/tasks/{task_id}/archive")
async def archive_single_task(project_id: int, task_id: int):
    task = store.get_task(project_id, task_id)
    if not task:
        return {"error": "Task not found"}
    peers = _thread_peers(project_id, task)
    if any(t["status"] in ("todo", "pending", "in_progress", "review") for t in peers):
        return {"error": "Cannot archive an active task"}
    archived = []
    for t in peers:
        if t["status"] in ("done", "failed", "cancelled"):
            store.update_task(project_id, t["id"], status="archived", archived_from=t["status"])
            archived.append(t["id"])
    if archived:
        await ws_manager.broadcast({
            "type": "tasks_archived", "task_ids": archived, "project_id": project_id})
    return {"success": True, "archived_count": len(archived), "task_ids": archived}


@app.post("/api/projects/{project_id}/tasks/{task_id}/unarchive")
async def unarchive_task(project_id: int, task_id: int):
    task = store.get_task(project_id, task_id)
    if not task:
        return {"error": "Task not found"}
    restored = []
    for t in _thread_peers(project_id, task):
        if t.get("status") != "archived":
            continue
        prev = t.get("archived_from") or ("failed" if t.get("error") else "done")
        if prev not in ("done", "failed", "cancelled"):
            prev = "done"
        store.update_task(project_id, t["id"], status=prev)
        restored.append(t["id"])
    sid = task.get("session_id")
    if sid:
        hidden_sessions.hide(sid, False)
        archived_sessions.hide(sid, False)
    if restored:
        await ws_manager.broadcast({
            "type": "task_update", "task_id": task_id, "project_id": project_id,
            "status": store.get_task(project_id, task_id).get("status", "done")})
    return {"success": True, "restored_count": len(restored), "task_ids": restored}


@app.post("/api/projects/{project_id}/tasks/{task_id}/approve")
async def approve_plan(project_id: int, task_id: int, body: dict = None):
    if body is None: body = {}
    task = store.get_task(project_id, task_id)
    if not task or task["status"] != "review":
        return {"error": "Task not in review status"}

    # Collect question answers
    answers_text = ""
    answers = body.get("answers", {})
    questions = task.get("plan_questions") or []
    if answers and questions:
        answers_text = "\n\nUser's decisions on plan questions:\n"
        for idx_str, choice in answers.items():
            idx = int(idx_str)
            if 0 <= idx < len(questions):
                questions[idx]["answer"] = choice
                answers_text += f"- {questions[idx]['question']}\n  Answer: {choice}\n"
        store.update_task(project_id, task_id, plan_questions=questions)

    plan_content = task.get("plan_content", "")

    # Try to extract structured sub-tasks from the plan
    subtasks = body.get("subtasks")  # Frontend may send edited subtasks
    if not subtasks:
        from utils import extract_subtasks
        subtasks = extract_subtasks(plan_content)

    created_tasks = []
    if subtasks and len(subtasks) > 1:
        # Batch create sub-tasks with dependency mapping
        # Map subtask index → real task ID
        index_to_task_id = {}
        for st in subtasks:
            # Resolve dependencies: subtask index → real task ID
            pred_id = task_id  # Default: depend on the plan task
            deps = st.get("depends_on", [])
            if deps:
                # Use the last dependency (simplified single-predecessor model)
                dep_idx = deps[-1]
                pred_id = index_to_task_id.get(dep_idx, task_id)

            desc = (
                f"[From Plan #{task_id}, subtask {st['index']}] "
                f"{st['title']}\n\n{st['description']}"
            )
            if st.get("files"):
                desc += f"\n\nFiles: {', '.join(st['files'])}"
            desc += f"\n\nOriginal request: {task['description']}{answers_text}"

            new_task = store.add_task(
                project_id, desc, plan_mode=False,
                predecessor_id=pred_id,
                images=task.get("images") or [])
            if new_task:
                index_to_task_id[st["index"]] = new_task["id"]
                created_tasks.append(new_task)
    else:
        # No structured sub-tasks found — fallback to single task
        impl_desc = (
            f"Implement the following plan from task #{task_id}:\n\n"
            f"Original request: {task['description']}\n\n"
            f"Plan:\n{plan_content}{answers_text}"
        )
        new_task = store.add_task(project_id, impl_desc, plan_mode=False,
                                  predecessor_id=task_id,
                                  images=task.get("images") or [])
        if new_task:
            created_tasks.append(new_task)

    store.update_task(project_id, task_id, status="done",
                      completed_at=datetime.now().isoformat(),
                      plan_adopted_subtasks=subtasks or [],
                      plan_created_task_ids=[t["id"] for t in created_tasks])
    for t in created_tasks:
        await ws_manager.broadcast({
            "type": "task_created", "task": t, "project_id": project_id})
    if created_tasks:
        worker_manager.wake()
    return {"tasks": created_tasks, "count": len(created_tasks)}


# ─── Image Upload API ────────────────────────────────────────────────────────

_EXT_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


async def _save_upload(bucket: str, url_prefix: str, file: UploadFile) -> dict:
    ctype = (file.content_type or "").lower().split(";")[0].strip()
    if ctype == "image/jpg":
        ctype = "image/jpeg"
    if not ctype or ctype == "application/octet-stream":
        ext = os.path.splitext(file.filename or "")[1].lower()
        ctype = _EXT_IMAGE_MIME.get(ext, ctype)
    if ctype not in ALLOWED_IMAGE_TYPES:
        return {"error": f"File type not allowed: {file.content_type}"}
    contents = await file.read()
    if len(contents) > MAX_IMAGE_SIZE:
        return {"error": "File too large (max 10MB)"}

    upload_dir = UPLOADS_DIR / bucket
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c for c in (file.filename or "image") if c.isalnum() or c in ".-_") or "image"
    filename = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    file_path = upload_dir / filename
    with open(file_path, "wb") as f:
        f.write(contents)

    return {
        "url": f"{url_prefix}/{filename}",
        "path": str(file_path.resolve()),
    }


@app.post("/api/uploads")
async def upload_session_image(file: UploadFile):
    return await _save_upload("sessions", "/api/uploads/s", file)


@app.get("/api/uploads/s/{filename}")
async def serve_session_upload(filename: str):
    safe_name = "".join(c for c in filename if c.isalnum() or c in ".-_")
    file_path = UPLOADS_DIR / "sessions" / safe_name
    if not file_path.exists():
        return {"error": "File not found"}
    return FileResponse(file_path)


@app.post("/api/projects/{project_id}/uploads")
async def upload_image(project_id: int, file: UploadFile):
    if not store.get_project_path(project_id):
        return {"error": "Project not found"}
    return await _save_upload(str(project_id), f"/api/uploads/{project_id}", file)


@app.get("/api/uploads/{project_id}/{filename}")
async def serve_upload(project_id: int, filename: str):
    safe_name = "".join(c for c in filename if c.isalnum() or c in ".-_")
    file_path = UPLOADS_DIR / str(project_id) / safe_name
    if not file_path.exists():
        return {"error": "File not found"}
    return FileResponse(file_path)


# ─── Manual Execution API (for terminal/VSCode) ─────────────────────────────

@app.get("/api/projects/{project_id}/next-task")
async def get_next_task(project_id: int):
    """Get next pending task without claiming it."""
    tasks = store.get_tasks(project_id)
    for t in tasks:
        if t["status"] != "pending":
            continue
        if t.get("predecessor_id"):
            pred = next((x for x in tasks
                         if x["id"] == t["predecessor_id"]), None)
            if pred and pred["status"] not in ("done", "failed", "cancelled", "archived"):
                continue
        return {"task": t}
    return {"task": None, "message": "No pending tasks"}


@app.post("/api/projects/{project_id}/claim-next")
async def claim_next_task(project_id: int):
    """Atomically find + claim next pending task in one call.
    Designed for /loop: single curl, returns task to execute or null."""
    tasks = store.get_tasks(project_id)
    for t in tasks:
        if t["status"] != "pending":
            continue
        if t.get("predecessor_id"):
            pred = next((x for x in tasks
                         if x["id"] == t["predecessor_id"]), None)
            if pred and pred["status"] not in ("done", "failed", "cancelled", "archived"):
                continue
        # Claim it
        store.update_task(project_id, t["id"],
                          status="in_progress",
                          worker_id="manual",
                          started_at=datetime.now().isoformat())
        await ws_manager.broadcast({
            "type": "task_update", "task_id": t["id"],
            "project_id": project_id, "status": "in_progress"})
        return {"task": store.get_task(project_id, t["id"])}
    return {"task": None}


@app.post("/api/projects/{project_id}/tasks/{task_id}/claim")
async def claim_task(project_id: int, task_id: int):
    """Claim a task for manual execution. Sets status to in_progress."""
    task = store.get_task(project_id, task_id)
    if not task:
        return {"error": "Task not found"}
    if task["status"] != "pending":
        return {"error": f"Task is {task['status']}, not pending"}
    store.update_task(project_id, task_id,
                      status="in_progress",
                      worker_id="manual",
                      started_at=datetime.now().isoformat())
    return {"success": True, "task": store.get_task(project_id, task_id)}


@app.post("/api/projects/{project_id}/tasks/{task_id}/complete")
async def complete_task(project_id: int, task_id: int, body: dict = None):
    """Mark a manually executed task as done.
    body.summary: work summary (what was done, files changed, etc.)
    body.session_id: Claude Code session ID for future --resume
    """
    if body is None:
        body = {}
    task = store.get_task(project_id, task_id)
    if not task:
        return {"error": "Task not found"}

    # Store summary in task field (not logs — avoid duplication)
    summary = body.get("summary", "")

    store.update_task(project_id, task_id,
                      status="done",
                      completed_at=datetime.now().isoformat(),
                      session_id=body.get("session_id"),
                      summary=summary or None)
    await ws_manager.broadcast({
        "type": "task_update", "task_id": task_id,
        "project_id": project_id, "status": "done"})
    return {"success": True}


# ─── Directory Browser ───────────────────────────────────────────────────────

@app.get("/api/browse")
async def browse_directory(path: str = "~"):
    abs_path = os.path.expanduser(path)
    if not os.path.isdir(abs_path):
        abs_path = os.path.dirname(abs_path)
        if not os.path.isdir(abs_path):
            abs_path = os.path.expanduser("~")

    dirs = []
    try:
        for entry in sorted(os.scandir(abs_path), key=lambda e: e.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            dirs.append({
                "name": entry.name, "path": entry.path,
                "is_git": os.path.isdir(os.path.join(entry.path, ".git")),
            })
    except PermissionError:
        pass

    parent = os.path.dirname(abs_path)
    return {"current": abs_path,
            "parent": parent if parent != abs_path else None,
            "dirs": dirs}


# ─── Analytics API ────────────────────────────────────────────────────────────

@app.get("/api/projects/{project_id}/analytics")
async def get_project_analytics(project_id: int, days: int = 0):
    result = store.get_analytics(project_id, days=days)
    if result is None:
        return {"error": "Project not found"}
    return result


# ─── Other API ───────────────────────────────────────────────────────────────

@app.post("/api/projects/{project_id}/tasks/{task_id}/log")
async def push_task_log(project_id: int, task_id: int, body: dict = None):
    """Push a log entry and broadcast via WebSocket (used by MCP server)."""
    if body is None:
        body = {}
    message = body.get("message", "")
    log_type = body.get("type", "message")
    if message:
        store.append_log(project_id, task_id, type=log_type, content=message)
    await ws_manager.broadcast({
        "type": "task_log", "project_id": project_id,
        "task_id": task_id, "message": message, "log_type": log_type,
        "timestamp": datetime.now().isoformat()
    })
    return {"success": True}


@app.get("/api/workers")
async def get_workers():
    return {"workers": worker_manager.get_active_workers()}


@app.post("/api/settings")
async def update_settings(body: dict):
    if "max_workers" in body:
        worker_manager.max_workers = min(10, max(1, int(body["max_workers"])))
    return {"max_workers": worker_manager.max_workers}


# ─── WebSocket ───────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError: pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ─── Static files ────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")),
          name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5100, reload=False)
