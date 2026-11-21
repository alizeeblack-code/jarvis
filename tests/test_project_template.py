"""A new project's CLAUDE.md gets the task section in the UI's language, with the real port."""
from fastapi.testclient import TestClient

import server
import store as store_mod
from store import ProjectStore


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(store_mod, "LOCK_FILE", tmp_path / "projects.lock")
    return ProjectStore()


def test_template_language_and_port(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    zh = s.create_project("zh", str(tmp_path / "zh"), lang="zh", port=5180)
    en = s.create_project("en", str(tmp_path / "en"), lang="en", port=5180)
    zh_md = (tmp_path / "zh" / "CLAUDE.md").read_text()
    en_md = (tmp_path / "en" / "CLAUDE.md").read_text()
    assert "工作流程" in zh_md and "Workflow" not in zh_md
    assert "Workflow" in en_md and "工作流程" not in en_md
    assert f"localhost:5180/api/projects/{en['id']}/claim-next" in en_md
    assert f"localhost:5180/api/projects/{zh['id']}/claim-next" in zh_md
    assert "JARVIS_PORT" not in en_md and "PROJECT_ID" not in en_md
    # adding it again leaves the section alone
    s._ensure_claude_md(str(tmp_path / "en"), "en", en["id"], lang="zh")
    assert (tmp_path / "en" / "CLAUDE.md").read_text() == en_md


def test_api_passes_ui_language(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "store", _store(tmp_path, monkeypatch))
    c = TestClient(server.app, base_url="http://localhost:5333", client=("127.0.0.1", 50000))
    c.post("/api/projects", json={"name": "a", "path": str(tmp_path / "a"), "lang": "zh"})
    c.post("/api/projects", json={"name": "b", "path": str(tmp_path / "b")})
    assert "工作流程" in (tmp_path / "a" / "CLAUDE.md").read_text()
    b = (tmp_path / "b" / "CLAUDE.md").read_text()
    assert "Workflow" in b and "localhost:5333/" in b        # API callers without a language get English
