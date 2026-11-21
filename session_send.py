"""Send a new instruction to an existing session from the web page.

The CLI resumes the session in the folder it was started in and appends to the
same log, so the conversation view picks it up like any other turn:
  claude -p --resume <id>          (stream-json; tool permissions auto-approved
                                    like the worker does)
  codex exec ... resume <id> -     (prompt on stdin)
  agy -p <text> --conversation <id>
No git branch / commit here: this is the same as typing in the desktop app.
"""

import base64
import json
import os
import subprocess
import threading
import time
import uuid

from transcript import AgyLog, agy_kind, agy_trailing_glitch, resume_args
import gitflow
from usage import agy_quota_error, tracker as usage_tracker

HARD_LIMIT_MIN = {"claude": 120, "codex": 180, "antigravity": 70}

_IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _image_paths(images) -> list:
    return [img["path"] for img in (images or [])
            if isinstance(img, dict) and img.get("path") and os.path.isfile(img["path"])]


def _image_dirs(images) -> list:
    dirs = []
    for p in _image_paths(images):
        d = os.path.dirname(os.path.abspath(p))
        if d and d not in dirs:
            dirs.append(d)
    return dirs


def _agy_prompt(text: str, images) -> str:
    paths = _image_paths(images)
    if not paths:
        return text
    base = text or "请查看附带的图片"
    lines = "\n".join(f"- {p}" for p in paths)
    return f"{base}\n\n[Attached Images]\nThe user attached image files. Use the read_file tool to view them:\n{lines}"


def _read_image_block(path: str):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = _IMAGE_MIMES.get(os.path.splitext(path)[1].lower())
    if not mime:
        return None
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(raw).decode("ascii")},
    }


def build_cmd(agent: str, sid: str, cwd: str, text: str, skip_perms: bool = False,
              model: str = "", effort: str = "", images=None):
    """(argv, stdin_text). Claude gets its prompt over the stream-json protocol.
    model / effort: what the session last ran with, so the new turn keeps them
    (Antigravity: the model to run, picked by agy_models())."""
    keep = resume_args(agent, model, effort)
    paths = _image_paths(images)
    if agent == "claude":
        return (["claude", "--output-format", "stream-json", "--input-format", "stream-json",
                 "--verbose", "--permission-prompt-tool", "stdio",
                 "--permission-mode", "bypassPermissions", *keep, "--resume", sid], None)
    if agent == "codex":
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", cwd]
        cmd += ["--dangerously-bypass-approvals-and-sandbox"] if skip_perms else ["-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"]
        cmd += gitflow.codex_optional_mcp(cwd)
        img_args = []
        for p in paths:
            img_args += ["-i", p]
        prompt = text or ("请查看附带的图片" if img_args else "")
        return cmd + ["resume", *img_args, *keep, sid, "-"], prompt
    if agent == "antigravity":
        dir_args = []
        for d in _image_dirs(images):
            dir_args += ["--add-dir", d]
        prompt = _agy_prompt(text, images)
        cmd = ["agy", "-p", prompt, "--output-format", "stream-json", "--print-timeout", "60m",
               "--dangerously-skip-permissions", *keep, *dir_args, "--conversation", sid]
        return (cmd if skip_perms else cmd + ["--sandbox"]), None
    raise ValueError(f"unknown executor: {agent}")


def project_root(cwd: str) -> str:
    """The project a session's folder belongs to: a task worktree maps back to its
    repository; otherwise the nearest folder up that keeps .claude/settings.local.json."""
    import re
    root = re.sub(r"/\.claude/worktrees/[^/]+(/.*)?$", "", cwd or "")
    cur, home = root, os.path.expanduser("~")
    while cur and cur not in ("/", home):
        if os.path.exists(os.path.join(cur, ".claude", "settings.local.json")):
            return cur
        cur = os.path.dirname(cur)
    return root


class SessionSender:
    def __init__(self, on_done=None):
        self.lock = threading.Lock()
        self.runs: dict = {}        # sid -> {state, started_at, ended_at, error, text, rounds_before}
        self.queues: dict = {}      # sid -> [{id, text, at, agent, cwd, skip_perms, model, effort, project_id}]
        self.on_done = on_done      # called from the run thread when a run ends

    def status(self, sid: str) -> dict:
        with self.lock:
            r = self.runs.get(sid)
            return {k: v for k, v in r.items() if k != "proc"} if r else {}

    def clear(self, sid: str, before_ms: int = None) -> bool:
        """Forget a finished run (its error / unsent instruction). With before_ms,
        only when it ended before then. Returns whether something was dropped."""
        with self.lock:
            r = self.runs.get(sid)
            if not r or r.get("state") == "running":
                return False
            if before_ms is not None and r.get("ended_at", 0) >= before_ms:
                return False
            self.runs.pop(sid, None)
            return True

    def busy(self, sid: str) -> bool:
        with self.lock:
            return self.runs.get(sid, {}).get("state") == "running"

    @staticmethod
    def _public_queued(item: dict) -> dict:
        out = {"id": item["id"], "text": item["text"], "at": item["at"],
               "agent": item["agent"], "mode": "direct"}
        imgs = [img.get("url") for img in (item.get("images") or []) if isinstance(img, dict) and img.get("url")]
        if imgs:
            out["images"] = imgs
        return out

    def queue(self, sid: str) -> list:
        with self.lock:
            return [self._public_queued(x) for x in self.queues.get(sid, [])]

    def queued_sids(self) -> list:
        with self.lock:
            return [sid for sid, q in self.queues.items() if q]

    def enqueue(self, sid: str, agent: str, cwd: str, text: str, skip_perms: bool = False,
                model: str = "", effort: str = "", project_id=None, images=None) -> dict:
        if not cwd or not os.path.isdir(cwd):
            raise ValueError(f"会话原来的目录不存在：{cwd or '（未知）'}")
        imgs = list(images or [])
        build_cmd(agent, sid, cwd, text, skip_perms, model, effort, *([imgs] if imgs else []))
        item = {
            "id": uuid.uuid4().hex[:8], "text": text[:8000], "at": int(time.time() * 1000),
            "agent": agent, "cwd": cwd, "skip_perms": skip_perms,
            "model": model, "effort": effort, "project_id": project_id, "images": imgs,
        }
        with self.lock:
            self.queues.setdefault(sid, []).append(item)
        return self._public_queued(item)

    def unqueue(self, sid: str, qid: str = ""):
        with self.lock:
            q = self.queues.get(sid, [])
            if not qid:
                item = q.pop() if q else None
            else:
                item = next((q.pop(i) for i, x in enumerate(q) if x["id"] == qid), None)
        if not item:
            return None
        out = self._public_queued(item)
        if item.get("images"):
            out["raw_images"] = list(item["images"])
        return out

    def start_next(self, sid: str, rounds_before=None, model: str = "", effort: str = ""):
        """Atomically pop the next queued instruction for `sid` and start running it.
        Returns (queued_item, run_status) or None if already running or queue is empty."""
        with self.lock:
            if self.runs.get(sid, {}).get("state") == "running":
                return None
            q = self.queues.get(sid)
            if not q:
                return None
            item = q.pop(0)
            m, e = model or item["model"], effort or item["effort"]
            models = _models(item["agent"], m)
            imgs = list(item.get("images") or [])
            cmd, stdin_text = build_cmd(item["agent"], sid, item["cwd"], item["text"],
                                        item["skip_perms"], models[0], e, *([imgs] if imgs else []))
            run = self.runs[sid] = {
                "state": "running", "started_at": int(time.time() * 1000),
                "ended_at": 0, "error": "", "text": item["text"][:8000],
                "agent": item["agent"], "rounds_before": rounds_before, "model": models[0],
                "images": imgs,
            }
        threading.Thread(target=self._run,
                         args=(sid, item["agent"], cmd, item["cwd"], item["text"], stdin_text, run),
                         kwargs={"retry": (item["skip_perms"], e, models[1:])},
                         daemon=True).start()
        return item, self.status(sid)

    def send(self, sid: str, agent: str, cwd: str, text: str, skip_perms: bool = False,
             model: str = "", effort: str = "", rounds_before=None, images=None) -> dict:
        if not cwd or not os.path.isdir(cwd):
            raise ValueError(f"会话原来的目录不存在：{cwd or '（未知）'}")
        imgs = list(images or [])
        models = _models(agent, model)
        cmd, stdin_text = build_cmd(agent, sid, cwd, text, skip_perms, models[0], effort,
                                    *([imgs] if imgs else []))
        with self.lock:
            if self.runs.get(sid, {}).get("state") == "running":
                raise ValueError("这个会话正在执行上一条指令")
            run = self.runs[sid] = {"state": "running", "started_at": int(time.time() * 1000),
                                    "ended_at": 0, "error": "", "text": text[:8000], "agent": agent,
                                    "rounds_before": rounds_before, "model": models[0], "images": imgs}
        threading.Thread(target=self._run, args=(sid, agent, cmd, cwd, text, stdin_text, run),
                         kwargs={"retry": (skip_perms, effort, models[1:])},
                         daemon=True).start()
        return self.status(sid)

    def _run(self, sid, agent, cmd, cwd, text, stdin_text, run, retry=None):
        """retry: (skip_perms, effort, models left) — an Antigravity turn that ran
        out of quota goes again on the next model."""
        env = gitflow.agent_env(project_root(cwd))
        skip, effort, rest = retry or (False, "", [])
        imgs = run.get("images") or []
        while True:
            err = self._once(sid, agent, cmd, cwd, text, stdin_text, run, env)
            if not (err and rest and agy_quota_error(err) and not run.get("cancelled")):
                break
            run["model"] = rest[0]
            cmd, stdin_text = build_cmd(agent, sid, cwd, text, skip, rest.pop(0), effort,
                                        *([imgs] if imgs else []))
        with self.lock:
            stopped = run.get("cancelled")
            run.update(state="cancelled" if stopped else "error" if err else "done",
                       error="" if stopped else err, ended_at=int(time.time() * 1000))
            run.pop("proc", None)
        if self.on_done:
            try:
                self.on_done(sid)
            except Exception:                            # noqa: BLE001
                pass

    def _once(self, sid, agent, cmd, cwd, text, stdin_text, run, env) -> str:
        err = ""
        glitch = False
        imgs = run.get("images") or []
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            run["proc"] = proc
            timer = threading.Timer(HARD_LIMIT_MIN[agent] * 60, proc.kill)
            timer.start()
            try:
                if agent == "claude":
                    err = self._claude_protocol(proc, text, imgs)
                else:
                    if stdin_text is not None:
                        proc.stdin.write(stdin_text.encode())
                    proc.stdin.close()
                    agy_log = AgyLog(text, sid, model=run.get("model", ""), images=imgs) if agent == "antigravity" else None
                    last_kind = ""
                    for raw in proc.stdout:
                        if agent == "codex":
                            err = _codex_error(raw) or err
                        elif agy_log:
                            ev = agy_log.feed(raw)
                            last_kind = agy_kind(ev) or last_kind
                            if (ev or {}).get("event") == "result" and agy_trailing_glitch(ev.get("result"), last_kind):
                                glitch = True     # answer written; agy tripped over an empty call after it
                                continue
                            err = _agy_error(ev) or err
                proc.wait(timeout=30)
            finally:
                timer.cancel()
            if proc.returncode and not err and not glitch:
                tail = proc.stderr.read().decode("utf-8", errors="replace").strip()[-300:]
                err = f"退出码 {proc.returncode}{'：' + tail if tail else ''}"
        except FileNotFoundError:
            err = f"找不到命令 {cmd[0]}"
        except Exception as e:                           # noqa: BLE001
            err = str(e)[:300]
        if agent == "codex" and "active writer" in err:
            err = codex_busy_message(cwd)
            run["held"] = True               # the panel offers to fork it instead
        return err

    def fork(self, sid: str, cwd: str, text: str, skip_perms: bool = False,
             model: str = "", effort: str = "", wait_s: int = 120, images=None) -> str:
        """Codex: copy session `sid` into a new one and send `text` there (works while
        the original is open in an interactive codex). Returns the new session id once
        Codex names it; the turn keeps running in the background like a send."""
        if not cwd or not os.path.isdir(cwd):
            raise ValueError(f"会话原来的目录不存在：{cwd or '（未知）'}")
        imgs = list(images or [])
        img_args = []
        for p in _image_paths(imgs):
            img_args += ["-i", p]
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", cwd]
        cmd += ["--dangerously-bypass-approvals-and-sandbox"] if skip_perms else [
            "-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"]
        cmd += gitflow.codex_optional_mcp(cwd)
        cmd += ["fork", *img_args, *resume_args("codex", model, effort), sid, "-"]
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=gitflow.agent_env(project_root(cwd)),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            raise ValueError("找不到命令 codex")
        stdin_text = text or ("请查看附带的图片" if img_args else "")
        proc.stdin.write(stdin_text.encode())
        proc.stdin.close()
        killer = threading.Timer(wait_s, proc.kill)
        killer.start()
        new = ""
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if ev.get("type") == "thread.started" and ev.get("thread_id"):
                new = ev["thread_id"]
                break
        killer.cancel()
        if not new:
            proc.kill()
            tail = proc.stderr.read().decode("utf-8", errors="replace").strip()[-300:]
            raise ValueError(f"复制会话失败{'：' + tail if tail else ''}")
        run = {"state": "running", "started_at": int(time.time() * 1000), "ended_at": 0, "error": "",
               "text": text[:8000], "agent": "codex", "rounds_before": 0, "model": model,
               "forked_from": sid, "images": imgs, "proc": proc}
        with self.lock:
            self.runs[new] = run
        threading.Thread(target=self._finish_fork, args=(new, proc, run), daemon=True).start()
        return new

    def _finish_fork(self, sid, proc, run):
        err = ""
        timer = threading.Timer(HARD_LIMIT_MIN["codex"] * 60, proc.kill)
        timer.start()
        try:
            for raw in proc.stdout:
                err = _codex_error(raw) or err
            proc.wait(timeout=30)
            if proc.returncode and not err:
                tail = proc.stderr.read().decode("utf-8", errors="replace").strip()[-300:]
                err = f"退出码 {proc.returncode}{'：' + tail if tail else ''}"
        except Exception as e:                           # noqa: BLE001
            err = str(e)[:300]
        finally:
            timer.cancel()
        with self.lock:
            stopped = run.get("cancelled")
            run.update(state="cancelled" if stopped else "error" if err else "done",
                       error="" if stopped else err, ended_at=int(time.time() * 1000))
            run.pop("proc", None)
        if self.on_done:
            try:
                self.on_done(sid)
            except Exception:                            # noqa: BLE001
                pass

    @staticmethod
    def _claude_protocol(proc, text, images=None) -> str:
        from worker import WorkerProcess
        w = proc.stdin
        w.write((json.dumps({"type": "control_request", "request_id": str(uuid.uuid4()),
                             "request": {"subtype": "initialize"}}) + "\n").encode())
        w.flush()
        time.sleep(1)                        # same pause the worker uses after initialize
        blocks = [b for p in _image_paths(images) if (b := _read_image_block(p))]
        if blocks:
            content = [*blocks, {"type": "text", "text": text or "请查看附带的图片"}]
        else:
            content = text
        w.write((json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n").encode())
        w.flush()
        err = ""
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if ev.get("type") == "control_request":
                WorkerProcess._handle_control_request_sync(w, ev)
            elif ev.get("type") == "rate_limit_event":
                usage_tracker.record_claude(ev.get("rate_limit_info") or {})
            elif ev.get("type") == "result":
                if ev.get("is_error"):
                    err = str(ev.get("result") or ev.get("subtype") or "error")[:300]
                try:
                    w.close()                # one turn: let the CLI exit
                except OSError:
                    pass
        return err

    def cancel(self, sid: str) -> bool:
        with self.lock:
            run = self.runs.get(sid, {})
            proc = run.get("proc")
            if proc:
                run["cancelled"] = True
        if proc:
            proc.terminate()
            return True
        return False


def codex_busy_message(cwd: str) -> str:
    """Codex lets one client write to a thread at a time: the session is open in an
    interactive codex (terminal or app), so `exec resume` is turned away."""
    where = ", ".join(codex_tui_ttys(cwd))
    return (f"这个 Codex 会话正开在{'终端 ' + where + ' 的 ' if where else '别处的 '}codex 里，"
            "Codex 同一时间只允许一处写入。先在那边退出（/quit），或者直接在那边继续。")


def codex_tui_ttys(cwd: str) -> list:
    """Terminals running an interactive codex in this folder."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,tty=,command="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    ttys = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[1] == "??":
            continue
        pid, tty, cmd = parts
        exe, *args = cmd.split()
        if not exe.endswith("/codex") or (args and args[0] in ("exec", "app-server", "mcp-server")):
            continue
        try:
            got = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True,
                                 text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if any(l[1:] == cwd for l in got.splitlines() if l.startswith("n")) and tty not in ttys:
            ttys.append(tty)
    return ttys


def _models(agent: str, model: str) -> list:
    """Antigravity: the models to try in order; others: just the one given."""
    return usage_tracker.agy_models(model) if agent == "antigravity" else [model]


def _agy_error(ev) -> str:
    res = (ev or {}).get("result") if (ev or {}).get("event") == "result" else None
    if isinstance(res, dict) and res.get("status") not in ("SUCCESS", None):
        return str(res.get("finished_reason") or res.get("error") or res.get("status"))[:300]
    return ""


def _codex_error(raw: bytes) -> str:
    try:
        ev = json.loads(raw)
    except ValueError:
        return ""
    if ev.get("type") == "turn.failed":
        return str((ev.get("error") or {}).get("message", "turn failed"))[:300]
    if ev.get("type") == "error":
        return str(ev.get("message", "error"))[:300]
    return ""
