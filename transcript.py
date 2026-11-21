"""Read a session log as a conversation: rounds of (your instruction →
steps → reply). Claude Code jsonl and Codex rollout logs; read-only and
incremental, like the session index. Round splitting follows Agent Deck's
"one ask, one conclusion" view.
"""

import glob
import json
import os
import re
import threading
from collections import OrderedDict

import time

from config import DATA_DIR
from sessions import (CLAUDE_PROJECTS, CODEX_HOME, _CODEX_SKIP, _SKIP_PROMPT, _codex_step,
                      parse_ts, step_text)

# agy keeps its steps as protobuf; Jarvis saves the stream-json events of the
# runs it starts, one file per conversation, with the instruction that began each turn.
AGY_LOG_DIR = DATA_DIR / "agy_logs"
_AGY_ARG_KEYS = ("CommandLine", "Command", "AbsolutePath", "Path", "TargetFile", "File",
                 "DirectoryPath", "Query", "SearchPath", "Url", "url", "path", "command", "query")

ASK_MAX = 8000
TEXT_MAX = 12000
ITEMS_MAX = 400
CACHE_FILES = 24
IMGS_MAX = 40
# The desktop app also writes a pasted image as a text line pointing at a temp file.
_IMG_REF_RE = re.compile(r"\[Image(?:: source: [^\]]*| #\d+)\]")
_EXIT_RE = re.compile(r"(?:exit code|Exit code|\"exit_code\")\D{0,3}([1-9]\d*)")


_CMD = {"bash", "shell", "local_shell_call", "exec_command", "exec", "run_command",
        "run_terminal_command", "write_stdin"}
_EDIT = {"edit", "write", "multiedit", "notebookedit", "apply_patch", "edit_file", "write_file",
         "replace_file_content", "create_file", "multi_edit"}
_READ = {"read", "read_file", "view_file", "notebookread", "list_dir", "ls"}
_SEARCH = {"grep", "glob", "grep_search", "find_by_name", "codebase_search", "websearch", "web_search",
           "search_web", "toolsearch"}
_WEB = {"webfetch", "read_url_content", "fetch", "navigate"}
_AGENT = {"task", "agent"}
_INTERNAL = {"todowrite", "taskcreate", "taskupdate", "update_plan", "wrap_up"}


def tool_cat(name: str, text: str = "") -> str:
    """cmd / edit / read / search / web / agent / other — or internal: bookkeeping
    (checklists, progress reports) the page already shows another way."""
    n = (name or "").split("__")[-1].lower()
    if n in _INTERNAL or "task-manager__" in (name or "") or text == "整理待办":
        return "internal"
    if n.startswith("browser_") or "browser" in (name or "").lower() or "chrome" in (name or "").lower():
        return "web"
    for cat, names in (("cmd", _CMD), ("edit", _EDIT), ("read", _READ), ("search", _SEARCH),
                       ("web", _WEB), ("agent", _AGENT)):
        if n in names:
            return cat
    for prefix, cat in (("$ ", "cmd"), ("改", "edit"), ("读网页", "web"), ("读", "read"),
                        ("搜", "search"), ("派子代理", "agent")):
        if text.startswith(prefix):
            return cat
    return "other"


def _new_round(ask: str, at: int, imgs: list = None) -> dict:
    """imgs: where each pasted image sits in the log ({o: line offset, i, j?}),
    served by image_at() so the conversation itself stays small."""
    imgs = (imgs or [])[:IMGS_MAX]
    return {"ask": ask[:ASK_MAX], "at": at, "end": at, "images": len(imgs), "imgs": imgs,
            "items": [], "interrupted": False, "model": "", "effort": "", "_ids": {}}


def claude_settings(d: dict):
    """(model, effort) an assistant record ran with; ("", "") when it says nothing."""
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    model = msg.get("model") or ""
    if model.startswith("<"):            # "<synthetic>" error stubs
        model = ""
    return model, (d.get("effort") or "") if model else ""


def codex_settings(p: dict):
    """(model, effort) from a turn_context payload."""
    eff = p.get("effort") or ((p.get("collaboration_mode") or {}).get("settings") or {}).get(
        "reasoning_effort") or ""
    return p.get("model") or "", eff


class _State:
    def __init__(self, agent: str):
        self.agent = agent
        self.offset = 0
        self.size = 0
        self.line = 0                # offset of the line being ingested (image refs point at it)
        self.rounds: list = []
        self.model = ""              # what the latest turn ran with
        self.effort = ""

    def cur(self, ts: int) -> dict:
        if not self.rounds:
            self.rounds.append(_new_round("", ts))
        r = self.rounds[-1]
        r["end"] = max(r["end"], ts)
        if self.model:
            r["model"], r["effort"] = self.model, self.effort
        return r

    def add_imgs(self, it: dict, refs: list):
        if refs:
            have = it.setdefault("imgs", [])
            have.extend(refs[:max(0, IMGS_MAX - len(have))])

    def item(self, r: dict, kind: str, text: str, ref: str = "", name: str = ""):
        it = {"k": kind, "t": text}
        if kind == "tool":
            it["c"] = tool_cat(name, text)
        r["items"].append(it)
        if ref:
            r["_ids"][ref] = it
        if len(r["items"]) > ITEMS_MAX:
            del r["items"][:-ITEMS_MAX]


_NO_RESPONSE = "No response requested."


_QA_RE = re.compile(r'"([^"]+)"="([^"]*)"')


def _result_str(content) -> str:
    return content if isinstance(content, str) else " ".join(
        x.get("text", "") for x in (content or []) if isinstance(x, dict))


def prompt_answer(ask: dict, result: str, is_error=False):
    """What was chosen, read from the tool result: {question: answer} for a
    multiple choice, "approved" / "rejected" for a plan."""
    if ask["kind"] == "plan":
        return "rejected" if is_error or "rejected" in result or "doesn't want to proceed" in result else "approved"
    if ask["kind"] == "choice":
        return {q: a for q, a in _QA_RE.findall(result or "")} or None
    return None


def claude_prompt(name: str, inp: dict):
    """A question the turn leaves for you, in a shape the panel can answer:
    the desktop app's closing box (wrap_up: a question and reply buttons), a
    multiple-choice AskUserQuestion, or a plan waiting for approval."""
    short = (name or "").split("__")[-1]
    if short == "wrap_up":
        options = [a.get("label") for a in inp.get("actions") or []
                   if isinstance(a, dict) and a.get("label") and not a.get("action")]
        if not (inp.get("ask") or options):
            return None
        return {"kind": "wrap", "ask": str(inp.get("ask") or "")[:300], "options": options[:3],
                "blocked": bool(inp.get("blocked"))}
    if short == "AskUserQuestion":
        qs = []
        for q in inp.get("questions") or []:
            if isinstance(q, dict) and q.get("question"):
                qs.append({"q": str(q["question"])[:400], "multi": bool(q.get("multiSelect")),
                           "options": [{"label": str(o.get("label", ""))[:80],
                                        "desc": str(o.get("description", ""))[:200]}
                                       for o in q.get("options") or [] if isinstance(o, dict)][:6]})
        return {"kind": "choice", "questions": qs[:4]} if qs else None
    if short == "ExitPlanMode" and inp.get("plan"):
        return {"kind": "plan", "plan": str(inp["plan"])[:TEXT_MAX]}
    return None


def ingest_claude(st: _State, d: dict):
    if d.get("isSidechain") or d.get("isMeta"):
        return
    t = d.get("type")
    if t not in ("user", "assistant"):
        return
    ts = parse_ts(d.get("timestamp"))
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    c = msg.get("content")
    if t == "assistant":
        model, effort = claude_settings(d)
        if model:
            st.model, st.effort = model, effort
        r = st.cur(ts)
        if msg.get("model") == "<synthetic>" and _NO_RESPONSE in json.dumps(c):
            r["interrupted"] = True             # the CLI's stub for a turn that was stopped
            return
        for b in c if isinstance(c, list) else []:
            if b.get("type") == "text" and b.get("text", "").strip():
                st.item(r, "text", b["text"].strip()[:TEXT_MAX])
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", ""), b.get("input") if isinstance(b.get("input"), dict) else {}
                st.item(r, "tool", step_text(name, inp), b.get("id", ""), name)
                ask = claude_prompt(name, inp)
                if ask:
                    ask["id"] = b.get("id", "")
                    r["prompt"] = ask
                if name.startswith("mcp__task-manager__") and inp.get("task_id") is not None:
                    try:
                        r["items"][-1]["task"] = int(inp["task_id"])
                    except (TypeError, ValueError):
                        pass
        return
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        r = st.cur(ts)
        for i, b in enumerate(c):
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            it = r["_ids"].get(b.get("tool_use_id", ""))
            if not it:
                continue
            if b.get("is_error"):
                it["err"] = True
            ask = r.get("prompt")
            if ask and ask.get("id") == b.get("tool_use_id") and ask["kind"] != "wrap":
                ask["answered"] = True          # answered where it was asked (desktop app, terminal)
                ask["answer"] = prompt_answer(ask, _result_str(b.get("content")), b.get("is_error"))
            inner = b.get("content") if isinstance(b.get("content"), list) else []
            st.add_imgs(it, [{"o": st.line, "i": i, "j": j} for j, x in enumerate(inner)
                             if isinstance(x, dict) and x.get("type") == "image"])
        return
    if isinstance(c, str):
        text, imgs = c, []
    else:
        text = "\n".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
        imgs = [{"o": st.line, "i": i} for i, b in enumerate(c or [])
                if isinstance(b, dict) and b.get("type") == "image"]
    text = _IMG_REF_RE.sub("", text).strip()
    if text.startswith("[Request interrupted"):
        if st.rounds:
            st.rounds[-1]["interrupted"] = True
        return
    if (not text and not imgs) or text.startswith(_SKIP_PROMPT):
        return
    prev = st.rounds[-1].get("prompt") if st.rounds else None
    if prev and not prev.get("answered"):
        prev["answered"] = True               # replied to (here, the desktop app or a terminal)
        prev.setdefault("answer", text[:200])
    st.rounds.append(_new_round(text, ts, imgs))


def ingest_codex(st: _State, d: dict):
    t, p = d.get("type"), d.get("payload") or {}
    ts = parse_ts(d.get("timestamp"))
    if t == "event_msg" and p.get("type") == "turn_aborted" and st.rounds:
        st.rounds[-1]["interrupted"] = True
        return
    if t == "turn_context":
        model, effort = codex_settings(p)
        if model:
            st.model, st.effort = model, effort
        return
    if t != "response_item":
        return
    pt = p.get("type")
    if pt == "message":
        parts = [x for x in p.get("content") or [] if isinstance(x, dict)]
        text = "\n".join(x.get("text", "") for x in parts if x.get("text")).strip()
        if p.get("role") == "user":
            imgs = [{"o": st.line, "i": i} for i, x in enumerate(p.get("content") or [])
                    if isinstance(x, dict) and x.get("type") == "input_image"]
            if (text or imgs) and not text.startswith(_CODEX_SKIP):
                st.rounds.append(_new_round(text, ts, imgs))
        elif p.get("role") == "assistant" and text:
            st.item(st.cur(ts), "text", text[:TEXT_MAX])
    elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
        step = _codex_step(p)
        if step != "整理待办":
            st.item(st.cur(ts), "tool", step, p.get("call_id", ""), p.get("name") or pt)
    elif pt in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output")
        if isinstance(out, list):
            it = st.cur(ts)["_ids"].get(p.get("call_id", ""))
            if it:
                st.add_imgs(it, [{"o": st.line, "j": j} for j, x in enumerate(out)
                                 if isinstance(x, dict) and x.get("type") == "input_image"])
        out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        if _EXIT_RE.search(out[:400] or ""):
            it = st.cur(ts)["_ids"].get(p.get("call_id", ""))
            if it:
                it["err"] = True


def agy_step(name: str, params: dict) -> str:
    arg = next((str(params[k]) for k in _AGY_ARG_KEYS if params.get(k)), "")
    if name in ("run_command", "run_terminal_command") and arg:
        return f"$ {arg}"[:300]
    return f"{name} {arg}".strip()[:300]


def ingest_agy(st: _State, d: dict):
    ts = d.get("ts") or 0
    ev = d.get("event")
    if ev == "prompt":
        st.model, st.effort = d.get("model") or "", ""
        r = _new_round(d.get("text") or "", ts)
        r["model"] = st.model
        if d.get("images"):
            r["image_urls"] = list(d["images"])
            r["images"] = len(r["image_urls"])
        st.rounds.append(r)
        return
    if ev == "result":
        if st.rounds and (d.get("result") or {}).get("finished_reason") in ("interrupted", "print_timeout"):
            st.rounds[-1]["interrupted"] = True
        return
    u = d.get("step_update") if ev == "step_update" else None
    if not isinstance(u, dict):
        return
    r = st.cur(ts)
    key = f"s{u.get('step_index')}"
    kind = u.get("step_type")
    if kind == "agent_response" and u.get("text_delta"):
        it = r["_ids"].get(key)
        if it:
            it["t"] = (it["t"] + u["text_delta"])[:TEXT_MAX]
        else:
            st.item(r, "text", u["text_delta"], key)
    elif kind == "tool":
        it = r["_ids"].get(key)
        if not it:
            info = u.get("tool_info") or {}
            tname = u.get("tool_name") or info.get("name") or "tool"
            st.item(r, "tool", agy_step(tname, info.get("parameters") or {}), key, tname)
            it = r["_ids"][key]
        if u.get("state") not in ("ACTIVE", "DONE", "PENDING", None):
            it["err"] = True
    if kind == "agent_response" and u.get("state") == "DONE":
        it = r["_ids"].get(key)
        if it:
            it["t"] = it["t"].strip()


def agy_last_input_tokens(sid: str, tail: int = 2 << 20) -> int:
    """Input tokens agy reports at the end of the latest turn — summed over the whole
    conversation so far (its `result` events are cumulative), from our event log."""
    path = AGY_LOG_DIR / f"{sid}.jsonl"
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - tail))
            lines = f.read().split(b"\n")
    except OSError:
        return 0
    for raw in reversed(lines):
        if b'"event": "result"' not in raw:
            continue
        try:
            return int((((json.loads(raw).get("result") or {}).get("usage")) or {}).get("input_tokens") or 0)
        except (ValueError, TypeError):
            continue
    return 0


def agy_trailing_glitch(res: dict, last_kind: str) -> bool:
    """agy sometimes ends a finished turn with an empty function call and reports
    ERROR ("Malformed function call ... Function call is empty"). When the last
    thing it did was write its answer, the turn is done."""
    err = str((res or {}).get("error") or "")
    return last_kind == "text" and "Malformed function call" in err


def agy_kind(ev) -> str:
    """'text' for a finished answer, 'tool' for a tool step, '' otherwise."""
    u = (ev or {}).get("step_update") if (ev or {}).get("event") == "step_update" else None
    if not isinstance(u, dict):
        return ""
    if u.get("step_type") == "tool":
        return "tool"
    return "text" if u.get("step_type") == "agent_response" and u.get("state") == "DONE" else ""


class AgyLog:
    """Writes one agy run's stream-json events to the conversation's file in
    AGY_LOG_DIR; events before `init` (which names the conversation) are held."""

    def __init__(self, prompt: str, conversation: str = "", log_dir=None, model: str = "", images=None):
        self.dir = log_dir or AGY_LOG_DIR
        self.conversation = conversation or ""
        rec = {"event": "prompt", "text": prompt[:ASK_MAX], "ts": int(time.time() * 1000)}
        if model:
            rec["model"] = model         # agy's own events don't say which model ran
        urls = [img["url"] for img in (images or []) if isinstance(img, dict) and img.get("url")]
        if urls:
            rec["images"] = urls
        if not self.conversation:
            rec["started"] = True        # Jarvis began this conversation (headless)
        self.pending = [rec]
        if self.conversation:
            self._flush()

    def _flush(self):
        import os
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, f"{self.conversation}.jsonl"), "a") as f:
            for rec in self.pending:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.pending = []

    def feed(self, line):
        """One stdout line -> the parsed event (None when it isn't JSON)."""
        try:
            d = json.loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(d, dict):
            return None
        d["ts"] = int(time.time() * 1000)
        body = d.get(d.get("event") or "")
        cid = d.get("conversation_id") or (body.get("conversation_id") if isinstance(body, dict) else "")
        if not self.conversation and cid:
            self.conversation = cid
        rec = dict(d)
        if rec.get("event") == "init":
            rec["init"] = {k: v for k, v in (rec.get("init") or {}).items() if k != "tools"}
        self.pending.append(rec)
        if self.conversation:
            self._flush()
        return d


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "_ids"}


def image_at(path: str, agent: str, off: int, i=None, j=None):
    """(media type, bytes) of the image a ref points at, or None."""
    import base64
    try:
        with open(path, "rb") as f:
            if off < 0 or off >= os.fstat(f.fileno()).st_size:
                return None
            f.seek(off)
            d = json.loads(f.readline())
    except (OSError, ValueError):
        return None
    try:
        if agent == "codex":
            p = d["payload"]
            part = p["output"][j] if j is not None else p["content"][i]
            url = part.get("image_url") or ""
            if part.get("type") != "input_image" or not url.startswith("data:"):
                return None
            head, data = url.split(",", 1)
            mime = head[5:].split(";")[0]
        else:
            block = d["message"]["content"][i]
            img = block["content"][j] if j is not None else block
            src = img.get("source") or {}
            if img.get("type") != "image" or src.get("type") != "base64":
                return None
            mime, data = src.get("media_type") or "", src.get("data") or ""
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        return None
    if not mime.startswith("image/"):
        return None
    try:
        return mime, base64.b64decode(data)
    except ValueError:
        return None


def find_log(sid: str, agent: str) -> str:
    """A session's log file on disk (also those older than the index window)."""
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid or ""):
        return ""
    if agent == "antigravity":
        p = AGY_LOG_DIR / f"{sid}.jsonl"
        return str(p) if p.exists() else ""
    pattern = (f"{CODEX_HOME}/sessions/**/rollout-*{sid}.jsonl" if agent == "codex"
               else f"{CLAUDE_PROJECTS}/*/{sid}.jsonl")
    hits = glob.glob(pattern, recursive=True)
    return max(hits, key=os.path.getmtime) if hits else ""


def latest_settings(path: str, agent: str, tail: int = 4 << 20) -> tuple:
    """(model, effort) of the last turn in a log — read from its tail."""
    model = effort = ""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - tail))
            if size > tail:
                f.readline()                 # drop the partial first line
            for raw in f:
                if b'"model"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if agent == "codex":
                    m, e = codex_settings(d.get("payload") or {}) if d.get("type") == "turn_context" else ("", "")
                elif agent == "antigravity":
                    m, e = (d.get("model") or "", "") if d.get("event") == "prompt" else ("", "")
                else:
                    m, e = claude_settings(d) if d.get("type") == "assistant" and not d.get("isSidechain") else ("", "")
                if m:
                    model, effort = m, e
    except OSError:
        pass
    return model, effort


def session_settings(sid: str, agent: str, path: str = "") -> tuple:
    """(model, effort) the next Jarvis turn runs with: what the session last ran
    with, or the effort you picked for it in the panel — marked with a leading "!"
    (exact: no "at least high" floor)."""
    if agent not in ("claude", "codex", "antigravity") or not sid:
        return "", ""
    path = path or find_log(sid, agent)
    model, effort = latest_settings(path, agent) if path else ("", "")
    from session_prefs import prefs
    picked = prefs.effort(sid)
    if picked and agent == "antigravity":
        from usage import AGY_DEFAULT
        base = re.sub(r"-(low|medium|high|xhigh)$", "", model or AGY_DEFAULT)
        return f"!{base}-{picked}", ""
    if picked:
        return model, f"!{picked}"
    return model, effort


def exact(value: str) -> tuple:
    """("medium", True) for "!medium": a value you picked, used as is."""
    v = value or ""
    return (v[1:], True) if v.startswith("!") else (v, False)


_CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_SAFE = re.compile(r"[A-Za-z0-9._:\[\]-]{1,80}")


CLAUDE_EFFORT_FLOOR = "high"     # runs Jarvis starts never go below this (the CLI default is medium)


def claude_effort(effort: str = "") -> str:
    """The session's own effort, raised to at least CLAUDE_EFFORT_FLOOR — unless you
    picked it ("!medium")."""
    effort, picked = exact(effort)
    if picked and effort in _CLAUDE_EFFORTS:
        return effort
    floor = _CLAUDE_EFFORTS.index(CLAUDE_EFFORT_FLOOR)
    return effort if effort in _CLAUDE_EFFORTS and _CLAUDE_EFFORTS.index(effort) > floor else CLAUDE_EFFORT_FLOOR


def resume_args(agent: str, model: str, effort: str) -> list:
    """CLI flags that keep a resumed session on the model / effort it last ran with.
    Claude: --resume already restores the model (with its [1m] context, which the log
    doesn't record), only effort is lost — so pass just --effort. Codex: pass both."""
    if agent == "claude":
        return ["--effort", claude_effort(effort)]
    if agent == "codex":
        effort = exact(effort)[0]
        out = ["-m", model] if _SAFE.fullmatch(model or "") else []
        if _SAFE.fullmatch(effort or ""):
            out += ["-c", f"model_reasoning_effort={effort}"]
        return out
    if agent == "antigravity":
        model = exact(model)[0]
        return ["--model", model] if _SAFE.fullmatch(model or "") else []
    return []


class TranscriptReader:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: OrderedDict = OrderedDict()

    def _load(self, path: str, agent: str) -> _State:
        size = os.path.getsize(path)
        with self.lock:
            st = self.cache.get(path)
            if st is None or size < st.offset or st.agent != agent:
                st = _State(agent)
            self.cache[path] = st
            self.cache.move_to_end(path)
            while len(self.cache) > CACHE_FILES:
                self.cache.popitem(last=False)
        if size == st.size:
            return st
        ingest = {"codex": ingest_codex, "antigravity": ingest_agy}.get(agent, ingest_claude)
        with self.lock, open(path, "rb") as f:
            f.seek(st.offset)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break
                st.line = st.offset
                st.offset += len(raw)
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    ingest(st, d)
            st.size = size
        return st

    def read(self, path: str, agent: str, before: int = None, limit: int = 6,
             task_id: int = None) -> dict:
        """Rounds [start, end) — the latest `limit` ones, those before `before`,
        or (task_id) those up to the last round that reported on that Jarvis task."""
        st = self._load(path, agent)
        with self.lock:
            total = len(st.rounds)
            end = total if before is None else max(0, min(before, total))
            if before is None and task_id is not None:
                hit = next((i for i in range(total - 1, -1, -1)
                            if any(it.get("task") == task_id for it in st.rounds[i]["items"])), None)
                if hit is not None:
                    end = hit + 1
            start = max(0, end - limit)
            rounds = [_public(r) for r in st.rounds[start:end]]
            # the turn before this page, so the first round's change marker is right
            prev = st.rounds[start - 1] if start > 0 else None
            return {"rounds": rounds, "start": start, "total": total,
                    "model": st.model, "effort": st.effort,
                    "prev_model": prev["model"] if prev else "",
                    "prev_effort": prev["effort"] if prev else ""}
