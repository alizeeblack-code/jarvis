"""Branches for worker tasks: one line of work = one branch in its own worktree.

A task (and the follow-ups that continue its session) runs in
<repo>/.claude/worktrees/task-<line> on branch task-<line>, cut from the latest
main. The project folder itself — its branch and uncommitted edits — is never
touched. Work stays on the branch until you merge it ("合并到 main"): the
project's test command runs in the worktree, then the branch is merged without
checking anything out (merge-tree + commit-tree + update-ref) unless main is
checked out somewhere clean, where a normal merge keeps that folder in step.
"""

import os
import shutil
import subprocess
import threading

WT_DIR = os.path.join(".claude", "worktrees")
COPY_FILES = (".env", ".env.local", ".env.development", ".env.development.local")
# Untracked notes that must stay one file across worktrees: an agent's edits in
# a worktree land in the project's own copy (a symlink, not a copy)
LINK_FILES = ("CLAUDE.local.md", "PROGRESS.md")
SETUP_TIMEOUT = 15 * 60
TEST_TIMEOUT = 20 * 60

_REPO_LOCKS_GUARD = threading.Lock()
_REPO_LOCKS: dict[str, threading.RLock] = {}


def _repo_lock(repo: str) -> threading.RLock:
    """Serialize .git metadata mutations (worktree add/prune/remove, merge) per repository."""
    key = os.path.realpath(repo or "")
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPO_LOCKS[key] = lock
        return lock


def git(cwd, *args, timeout=120):
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def is_repo(path) -> bool:
    return os.path.exists(os.path.join(path, ".git"))


def main_branch(repo) -> str:
    for b in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{b}")[0] == 0:
            return b
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1] or "main"


def _numstat(text: str) -> list:
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add, rem = (int(x) if x.isdigit() else 0 for x in parts[:2])
            out.append({"file": parts[2], "add": add, "del": rem})
    return out


def _log(path, *rev, n: int = 20) -> list:
    text = git(path, "log", f"-{n}", "--format=%h%x09%ct%x09%s", *rev)[1]
    return [{"sha": a, "at": int(b) * 1000, "title": c[:120]}
            for a, b, c in (l.split("\t", 2) for l in text.splitlines() if l.count("\t") >= 2)]


def branch_status(path, commits: int = 20, recent: int = 10) -> dict:
    """Where a folder's checkout stands against the project's main branch (and its
    remote, if it tracks one): {} when the folder isn't in a git repository.
    commits: the branch's own commits (on main itself: its latest ones)."""
    if not path or not os.path.isdir(path):
        return {}
    rc, root, _ = git(path, "rev-parse", "--show-toplevel")
    if rc:
        return {}
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")[1]
    detached = branch == "HEAD"
    if detached:
        branch = git(path, "rev-parse", "--short", "HEAD")[1]
    main = main_branch(path)
    status = [l for l in git(path, "status", "--porcelain")[1].splitlines() if l.strip()]
    changed = _numstat(git(path, "diff", "--numstat", "HEAD")[1])
    out = {"branch": branch, "main": main, "detached": detached, "path": root,
           "dirty": len(status), "dirty_add": sum(x["add"] for x in changed),
           "dirty_del": sum(x["del"] for x in changed), "ahead": 0, "behind": 0}
    rc, counts, _ = git(path, "rev-list", "--left-right", "--count", f"{main}...HEAD")
    if not rc and counts:
        behind, ahead = (int(x) for x in counts.split())
        out.update(ahead=ahead, behind=behind)
    if branch != main:
        base = git(path, "merge-base", main, "HEAD")[1]
        if base:
            info = git(path, "log", "-1", "--format=%h%x09%ct", base)[1].split("\t")
            out["base"] = {"sha": info[0], "at": int(info[1]) * 1000 if len(info) > 1 and info[1].isdigit() else 0}
        out["commits"] = _log(path, f"{main}..HEAD", n=commits)
        out["commits_of"] = "branch"
    else:
        out["commits"] = _log(path, n=recent)
        out["commits_of"] = "recent"
    up = git(path, "rev-parse", "--abbrev-ref", "@{u}")
    if up[0] == 0 and up[1]:
        rc, counts, _ = git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if not rc and counts:
            behind, ahead = (int(x) for x in counts.split())
            out["upstream"] = {"name": up[1], "ahead": ahead, "behind": behind}
    return out


def commit_files(path, sha: str, limit: int = 30) -> dict:
    """The files one commit changed, with lines added / removed."""
    import re
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha or "") or not path or not os.path.isdir(path):
        return {"files": []}
    stats = _numstat(git(path, "show", "--numstat", "--format=", sha)[1])
    return {"files": stats[:limit], "more": max(0, len(stats) - limit),
            "add": sum(x["add"] for x in stats), "del": sum(x["del"] for x in stats)}


def branch_name(line) -> str:
    return f"task-{line}"


def worktree_path(repo, line) -> str:
    return os.path.join(repo, WT_DIR, branch_name(line))


def _exclude(repo):
    """Keep the worktrees out of the project's own `git status` (local only, never committed)."""
    rc, common, _ = git(repo, "rev-parse", "--git-common-dir")
    if rc:
        return
    path = os.path.join(repo, common, "info", "exclude") if not os.path.isabs(common) \
        else os.path.join(common, "info", "exclude")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        text = open(path).read() if os.path.exists(path) else ""
        if "/.claude/worktrees/" not in text:
            with open(path, "a") as f:
                f.write(("" if text.endswith("\n") or not text else "\n") + "/.claude/worktrees/\n")
    except OSError:
        pass


def ensure_worktree(repo, line, base=None):
    """(path, created). The line's branch is reused while it exists (unmerged
    work, or a follow-up of the same session); otherwise it's cut from main."""
    path = worktree_path(repo, line)
    if os.path.exists(os.path.join(path, ".git")):
        return path, False
    with _repo_lock(repo):
        if os.path.exists(os.path.join(path, ".git")):
            return path, False
        if git(repo, "rev-parse", "--verify", "-q", "HEAD")[0]:
            raise RuntimeError("仓库还没有任何提交，无法开分支")
        _exclude(repo)
        git(repo, "worktree", "prune")
        br = branch_name(line)
        if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}")[0] == 0:
            rc, _, err = git(repo, "worktree", "add", path, br)
        else:
            rc, _, err = git(repo, "worktree", "add", "-b", br, path, base or main_branch(repo))
        if rc:
            raise RuntimeError(f"创建工作区失败：{err[-300:]}")
        return path, True


def setup_worktree(repo, path, command=""):
    """Gitignored config the code needs (.env…) is copied over, untracked
    CLAUDE.local.md / PROGRESS.md are linked; then the project's own setup command (e.g. `npm install`), if it has one."""
    for name in COPY_FILES:
        src, dst = os.path.join(repo, name), os.path.join(path, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass
    for name in LINK_FILES:              # tracked ones are already in the checkout
        src, dst = os.path.join(repo, name), os.path.join(path, name)
        if os.path.isfile(src) and not os.path.lexists(dst):
            try:
                os.symlink(os.path.abspath(src), dst)
            except OSError:
                pass
    if command.strip():
        ok, out = run_shell(path, command, SETUP_TIMEOUT)
        if not ok:
            raise RuntimeError(f"工作区准备命令失败：{out}")


def run_shell(cwd, command, timeout):
    try:
        p = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True,
                           timeout=timeout, executable="/bin/bash")
    except subprocess.TimeoutExpired:
        return False, f"超过 {timeout // 60} 分钟没结束"
    out = (p.stdout + "\n" + p.stderr).strip()
    return p.returncode == 0, out[-1500:]


def commit_all(path, message) -> bool:
    """Commit what the agent left uncommitted (agents in a sandbox can't run git commit)."""
    rc, status, _ = git(path, "status", "--porcelain")
    if rc or not status:
        return False
    _, check, _ = git(path, "diff", "--check")
    if "conflict" in check.lower():
        return False
    git(path, "add", "-A")
    return git(path, "commit", "-m", message)[0] == 0


def ahead(repo, line) -> dict:
    """Commits and files on the line's branch that main doesn't have yet."""
    br, main = branch_name(line), main_branch(repo)
    if git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}")[0]:
        return {"branch": br, "commits": 0, "files": 0, "ids": []}
    _, ids, _ = git(repo, "rev-list", "--reverse", f"{main}..{br}")
    _, files, _ = git(repo, "diff", "--name-only", f"{main}...{br}")
    ids = [i for i in ids.splitlines() if i]
    return {"branch": br, "commits": len(ids), "files": len([f for f in files.splitlines() if f]),
            "ids": ids}


def _checked_out(repo, branch):
    """Folder where `branch` is checked out, or None."""
    _, out, _ = git(repo, "worktree", "list", "--porcelain")
    cur = None
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            cur = ln[9:]
        elif ln == f"branch refs/heads/{branch}":
            return cur
    return None


def in_main(repo, ids) -> bool:
    """Every one of these commits is already on main (merged some other way)."""
    main = main_branch(repo)
    return bool(ids) and all(git(repo, "merge-base", "--is-ancestor", i, main)[0] == 0 for i in ids)


def settle_merged(repo, line) -> bool:
    """The line's work is all on main and its folder has nothing uncommitted:
    drop the worktree and branch. Returns whether it did."""
    path = worktree_path(repo, line)
    with _repo_lock(repo):
        if os.path.exists(path):
            rc, out, _ = git(path, "status", "--porcelain")
            if rc or out.strip():
                return False
        if ahead(repo, line)["commits"]:
            return False
        remove_line(repo, line)
        return True


def remove_line(repo, line):
    """Drop the worktree and branch once nothing on them is left unmerged."""
    path = worktree_path(repo, line)
    with _repo_lock(repo):
        if os.path.exists(path):
            git(repo, "worktree", "remove", "--force", path)
        git(repo, "worktree", "prune")
        git(repo, "branch", "-D", branch_name(line))


def merge(repo, line, title="", test_command="") -> dict:
    """Run the tests in the worktree, then merge the line's branch into main.
    Returns {ok, error?, output?, commits}."""
    br, main = branch_name(line), main_branch(repo)
    path = worktree_path(repo, line)
    has_wt = os.path.exists(os.path.join(path, ".git"))
    if has_wt:
        commit_all(path, f"{br}: uncommitted changes before merge")
    info = ahead(repo, line)
    if not info["commits"]:
        remove_line(repo, line)
        return {"ok": True, "commits": [], "note": "没有需要合并的提交"}
    if has_wt:
        if test_command.strip():
            ok, out = run_shell(path, test_command, TEST_TIMEOUT)
            if not ok:
                return {"ok": False, "error": "测试没通过，没有合并", "output": out}
    with _repo_lock(repo):
        info = ahead(repo, line)
        if not info["commits"]:
            remove_line(repo, line)
            return {"ok": True, "commits": [], "note": "没有需要合并的提交"}
        rc, tree, err = git(repo, "merge-tree", "--write-tree", main, br)
        if rc:
            return {"ok": False, "error": "和 main 有冲突，没有合并（可以让这个会话先把 main 合进来解决冲突）",
                    "output": tree[-1500:] or err}
        tree = tree.splitlines()[0]
        msg = f"Merge {br}: {title[:60]}".strip()
        where = _checked_out(repo, main)
        if where:
            # main is checked out (usually the project folder): merge there so its files follow,
            # but only when that folder has no uncommitted edits to tracked files.
            if git(where, "status", "--porcelain", "--untracked-files=no")[1]:
                return {"ok": False, "error": f"{where} 在 {main} 上有未提交的改动，先提交或暂存再合并"}
            rc, _, err = git(where, "merge", "--no-ff", "-m", msg, br)
            if rc:
                git(where, "merge", "--abort")
                return {"ok": False, "error": f"合并失败：{err[-300:]}"}
        else:
            _, old, _ = git(repo, "rev-parse", f"refs/heads/{main}")
            rc, commit, err = git(repo, "commit-tree", tree, "-p", main, "-p", br, "-m", msg)
            if rc or git(repo, "update-ref", f"refs/heads/{main}", commit, old)[0]:
                return {"ok": False, "error": f"合并失败：{err[-300:]}"}
        remove_line(repo, line)
        return {"ok": True, "commits": info["ids"]}


def claude_project_dir(path) -> str:
    """Where Claude Code keeps the sessions of a folder (~/.claude/projects/<encoded>)."""
    import re
    return os.path.expanduser(os.path.join("~", ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", path)))


def carry_claude_session(sid, path, find_log):
    """`claude --resume` only finds sessions of the current folder. A follow-up of
    a session that ran elsewhere (the project folder, before worktrees) gets a copy
    of its log in the worktree's session folder so it can continue there."""
    dst_dir = claude_project_dir(path)
    dst = os.path.join(dst_dir, f"{sid}.jsonl")
    if os.path.exists(dst):
        return
    src = find_log(sid, "claude")
    if src and os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, dst)


def project_env(repo) -> dict:
    """Env the project keeps for its agents in .claude/settings.local.json ("env"):
    local, untracked secrets such as an MCP token. Claude Code applies them to its
    own sessions; Jarvis passes them to every agent it starts in the project
    (the worktree has no copy of this untracked file)."""
    import json
    try:
        with open(os.path.join(repo, ".claude", "settings.local.json")) as f:
            env = (json.load(f) or {}).get("env") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(k): str(v) for k, v in env.items()
            if isinstance(k, str) and isinstance(v, (str, int, float)) and not isinstance(v, bool)}


def agent_env(repo) -> dict:
    """The process environment for an agent started in this project."""
    return {**os.environ, **project_env(repo)}


def codex_optional_mcp(path) -> list:
    """`-c` overrides that make every MCP server marked `required = true` (in the
    folder's .codex/config.toml or the user's) optional for this run: a task that
    doesn't need it still starts when that server is down. Config files are untouched."""
    import re
    import tomllib
    names = []
    for cfg in (os.path.join(path, ".codex", "config.toml"), os.path.expanduser("~/.codex/config.toml")):
        try:
            with open(cfg, "rb") as f:
                servers = (tomllib.load(f) or {}).get("mcp_servers") or {}
        except (OSError, ValueError):
            continue
        names += [n for n, v in servers.items()
                  if isinstance(v, dict) and v.get("required") is True and re.fullmatch(r"[A-Za-z0-9_-]+", n)]
    out = []
    for n in dict.fromkeys(names):
        out += ["-c", f"mcp_servers.{n}.required=false"]
    return out

