"""Worker branches: a worktree per line of work, merged on request."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gitflow  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    sh(r, "git", "init", "-q", "-b", "main")
    sh(r, "git", "config", "user.email", "t@t")
    sh(r, "git", "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    (r / ".env").write_text("SECRET=1\n")
    (r / ".gitignore").write_text(".env\n")
    sh(r, "git", "add", "a.txt", ".gitignore")
    sh(r, "git", "commit", "-qm", "init")
    return str(r)


def test_worktree_leaves_the_project_folder_alone(tmp_path):
    r = repo(tmp_path)
    sh(r, "git", "checkout", "-qb", "feature")          # you are working on another branch…
    open(os.path.join(r, "a.txt"), "a").write("my edit\n")  # …with an uncommitted edit
    path, created = gitflow.ensure_worktree(r, 7)
    assert created and path.endswith(".claude/worktrees/task-7")
    gitflow.setup_worktree(r, path, "echo ready > setup.ok")
    assert open(os.path.join(path, ".env")).read() == "SECRET=1\n"   # gitignored config copied
    assert os.path.exists(os.path.join(path, "setup.ok"))
    open(os.path.join(path, "b.txt"), "w").write("b\n")
    assert gitflow.commit_all(path, "task-7: b")
    assert sh(r, "git", "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert "my edit" in open(os.path.join(r, "a.txt")).read()
    assert "worktrees" not in sh(r, "git", "status", "--porcelain")  # excluded locally
    assert gitflow.ahead(r, 7)["commits"] == 1
    assert gitflow.ensure_worktree(r, 7) == (path, False)             # follow-ups reuse it


def test_untracked_notes_are_linked_tracked_ones_left_alone(tmp_path):
    r = repo(tmp_path)
    open(os.path.join(r, ".gitignore"), "a").write("CLAUDE.local.md\nPROGRESS.md\n")
    open(os.path.join(r, "CLAUDE.local.md"), "w").write("project_id: 1\n")
    open(os.path.join(r, "PROGRESS.md"), "w").write("# log\n")
    sh(r, "git", "commit", "-qam", "ignore notes")
    path, _ = gitflow.ensure_worktree(r, 3)
    gitflow.setup_worktree(r, path)
    for name in ("CLAUDE.local.md", "PROGRESS.md"):
        assert os.path.realpath(os.path.join(path, name)) == os.path.realpath(os.path.join(r, name))
    open(os.path.join(path, "PROGRESS.md"), "a").write("entry from the task\n")
    assert "entry from the task" in open(os.path.join(r, "PROGRESS.md")).read()  # one file
    open(os.path.join(path, "b.txt"), "w").write("b\n")
    assert gitflow.commit_all(path, "task-3: b")
    assert sh(path, "git", "show", "--name-only", "--format=", "HEAD") == "b.txt"  # links not committed

    other = tmp_path / "tracked"
    other.mkdir()
    sh(other, "git", "init", "-q", "-b", "main")
    sh(other, "git", "config", "user.email", "t@t")
    sh(other, "git", "config", "user.name", "t")
    (other / "PROGRESS.md").write_text("# tracked\n")
    sh(other, "git", "add", ".")
    sh(other, "git", "commit", "-qm", "init")
    p2, _ = gitflow.ensure_worktree(str(other), 1)
    gitflow.setup_worktree(str(other), p2)
    assert not os.path.islink(os.path.join(p2, "PROGRESS.md"))                # its own checkout copy


def test_merge_without_touching_any_checkout(tmp_path):
    r = repo(tmp_path)
    sh(r, "git", "checkout", "-qb", "feature")          # main is checked out nowhere
    path, _ = gitflow.ensure_worktree(r, 3)
    open(os.path.join(path, "c.txt"), "w").write("c\n")
    bad = gitflow.merge(r, 3, "add c", test_command="exit 1")
    assert not bad["ok"] and "测试" in bad["error"]
    assert gitflow.ahead(r, 3)["commits"] == 1          # uncommitted work was committed first
    res = gitflow.merge(r, 3, "add c", test_command="test -f c.txt")
    assert res["ok"] and len(res["commits"]) == 1
    assert sh(r, "git", "show", "main:c.txt") == "c"
    assert sh(r, "git", "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert not os.path.exists(path) and "task-3" not in sh(r, "git", "branch")


def test_merge_into_a_clean_main_checkout_and_refuse_dirty_or_conflicting(tmp_path):
    r = repo(tmp_path)
    path, _ = gitflow.ensure_worktree(r, 4)
    open(os.path.join(path, "a.txt"), "w").write("from task\n")
    gitflow.commit_all(path, "task-4")
    open(os.path.join(r, "a.txt"), "a").write("dirty\n")
    assert "未提交" in gitflow.merge(r, 4)["error"]
    sh(r, "git", "checkout", "--", "a.txt")
    open(os.path.join(r, "a.txt"), "w").write("from main\n")
    sh(r, "git", "commit", "-qam", "main moved")
    assert "冲突" in gitflow.merge(r, 4)["error"]
    sh(r, "git", "reset", "-q", "--hard", "HEAD~1")
    res = gitflow.merge(r, 4, "edit a")
    assert res["ok"] and open(os.path.join(r, "a.txt")).read() == "from task\n"   # folder followed


def test_no_commits_just_cleans_up(tmp_path):
    r = repo(tmp_path)
    path, _ = gitflow.ensure_worktree(r, 9)
    res = gitflow.merge(r, 9)
    assert res["ok"] and not res["commits"] and not os.path.exists(path)


def test_project_env_from_local_settings(tmp_path):
    import json
    import session_send
    r = tmp_path / "proj"
    (r / ".claude" / "worktrees" / "task-3").mkdir(parents=True)
    assert gitflow.project_env(str(r)) == {}                     # no file: nothing added
    (r / ".claude" / "settings.local.json").write_text(json.dumps(
        {"env": {"MCP_TOKEN": "s3cret", "PORT": 8080, "FLAG": True, "OBJ": {"a": 1}},
         "permissions": {"allow": []}}))
    assert gitflow.project_env(str(r)) == {"MCP_TOKEN": "s3cret", "PORT": "8080"}
    env = gitflow.agent_env(str(r))
    assert env["MCP_TOKEN"] == "s3cret" and env["PATH"] == os.environ["PATH"]
    # a session in a task worktree, or in a subfolder, belongs to the project
    assert session_send.project_root(str(r / ".claude" / "worktrees" / "task-3")) == str(r)
    (r / "src").mkdir()
    assert session_send.project_root(str(r / "src")) == str(r)


def test_required_mcp_servers_become_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))           # no user-level config
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.demo-mcp]\nurl = "https://x"\nrequired = true\n'
        '[mcp_servers.other]\ncommand = "x"\n')
    assert gitflow.codex_optional_mcp(str(tmp_path)) == ["-c", "mcp_servers.demo-mcp.required=false"]
    assert gitflow.codex_optional_mcp(str(tmp_path / "nowhere")) == []


def test_work_merged_by_hand_is_noticed_and_cleaned_up(tmp_path):
    r = repo(tmp_path)
    path, _ = gitflow.ensure_worktree(r, 9)
    open(os.path.join(path, "c.txt"), "w").write("c\n")
    gitflow.commit_all(path, "task-9: c")
    ids = gitflow.ahead(r, 9)["ids"]
    assert not gitflow.in_main(r, ids)
    open(os.path.join(path, "d.txt"), "w").write("uncommitted\n")
    sh(r, "git", "merge", "-q", "--ff-only", "task-9")     # the session merged it itself
    assert gitflow.in_main(r, ids)
    assert not gitflow.settle_merged(r, 9)                 # leftover edit in its folder: keep it
    os.remove(os.path.join(path, "d.txt"))
    assert gitflow.settle_merged(r, 9)
    assert not os.path.exists(path) and not sh(r, "git", "branch", "--list", "task-9")


def test_branch_status_against_main(tmp_path):
    r = repo(tmp_path)
    assert gitflow.branch_status(str(tmp_path / "nope")) == {}
    path, _ = gitflow.ensure_worktree(r, 5)
    open(os.path.join(path, "b.txt"), "w").write("one\ntwo\n")
    gitflow.commit_all(path, "task-5: add b")
    open(os.path.join(r, "c.txt"), "w").write("c\n")                 # main moves on too
    sh(r, "git", "add", "c.txt")
    sh(r, "git", "commit", "-qm", "main: c")
    open(os.path.join(path, "a.txt"), "a").write("dirty\n")         # and something uncommitted
    st = gitflow.branch_status(path)
    assert (st["branch"], st["main"], st["ahead"], st["behind"], st["dirty"]) == ("task-5", "main", 1, 1, 1)
    assert (st["dirty_add"], st["dirty_del"]) == (1, 0)                # uncommitted: just the totals
    assert [c["title"] for c in st["commits"]] == ["task-5: add b"] and st["commits_of"] == "branch"
    assert st["base"]["sha"]
    files = gitflow.commit_files(path, st["commits"][0]["sha"])
    assert files["files"] == [{"file": "b.txt", "add": 2, "del": 0}] and files["add"] == 2
    assert gitflow.commit_files(path, "not a sha") == {"files": []}
    on_main = gitflow.branch_status(r)
    assert on_main["branch"] == "main" and on_main["ahead"] == 0
    assert on_main["commits_of"] == "recent" and [c["title"] for c in on_main["commits"]] == ["main: c", "init"]


def test_concurrent_worktrees_in_same_repo(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    r = repo(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda line: gitflow.ensure_worktree(r, line), [11, 12, 13, 14]))
    assert all(created and os.path.isdir(path) for path, created in results)
    assert len({path for path, _ in results}) == 4
