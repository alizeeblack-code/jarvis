"""Access control: this machine is free, everyone else needs the token, other sites never."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import access
from access import AccessMiddleware

TOKEN = "t0k3n-for-tests"
LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.101.102.103", 50000)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("JARVIS_TOKEN", TOKEN)
    monkeypatch.delenv("JARVIS_LOCAL_NO_TOKEN", raising=False)


def _app():
    app = FastAPI()
    app.add_middleware(AccessMiddleware)

    @app.get("/")
    async def index():
        return {"page": True}

    @app.post("/api/run")
    async def run():
        return {"ok": True}

    @app.get("/api/data")
    async def data():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()
    return app


def client(peer, base="http://localhost:5180", **kw):
    return TestClient(_app(), base_url=base, client=peer, follow_redirects=False, **kw)


def test_local_needs_no_token():
    assert client(LOCAL).post("/api/run").json() == {"ok": True}


def test_remote_without_token_is_refused():
    c = client(REMOTE, base="http://100.101.102.103:5180")
    r = c.post("/api/run")
    assert r.status_code == 401 and r.json()["error"]
    page = c.get("/")
    assert page.status_code == 401 and "访问令牌" in page.text


def test_remote_with_wrong_token_is_refused():
    c = client(REMOTE, base="http://100.101.102.103:5180")
    assert c.post("/api/run", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_remote_with_header_token():
    c = client(REMOTE, base="http://100.101.102.103:5180")
    assert c.post("/api/run", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"ok": True}
    assert c.post("/api/run", headers={"X-Jarvis-Token": TOKEN}).json() == {"ok": True}


def test_token_link_sets_cookie_and_cleans_the_address():
    c = client(REMOTE, base="http://100.101.102.103:5180")
    r = c.get(f"/?token={TOKEN}&tab=board")
    assert r.status_code == 303
    assert r.headers["location"] == "/?tab=board"
    cookie = r.headers["set-cookie"]
    assert f"{access.COOKIE}={TOKEN}" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    c.cookies.set(access.COOKIE, TOKEN)
    assert c.post("/api/run").json() == {"ok": True}


def test_loopback_peer_with_foreign_host_needs_token():
    """DNS rebinding: evil.example resolves to 127.0.0.1, but the Host header gives it away."""
    c = client(LOCAL, base="http://evil.example:5180")
    assert c.post("/api/run").status_code == 401


def test_other_site_cannot_call_even_locally():
    c = client(LOCAL)
    assert c.post("/api/run", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/run", headers={"Origin": "http://localhost:3000"}).status_code == 403
    assert c.post("/api/run", headers={"Origin": "null"}).status_code == 403
    assert c.get("/api/data", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}).status_code == 403
    assert c.get("/api/data", headers={"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors"}).status_code == 403


def test_same_origin_and_link_navigation_pass():
    c = client(LOCAL)
    assert c.post("/api/run", headers={"Origin": "http://localhost:5180",
                                       "Sec-Fetch-Site": "same-origin"}).json() == {"ok": True}
    nav = c.get("/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})
    assert nav.status_code == 200


def test_websocket_guarded():
    # TestClient's websocket ignores base_url, so the Host header is given here
    local_host = {"Host": "localhost:5180"}
    with client(LOCAL).websocket_connect("/ws", headers=local_host) as ws:
        assert ws.receive_text() == "hi"
    remote = client(REMOTE)
    remote_host = {"Host": "100.101.102.103:5180"}
    with pytest.raises(WebSocketDisconnect):
        with remote.websocket_connect("/ws", headers=remote_host) as ws:
            ws.receive_text()
    with remote.websocket_connect("/ws", headers={**remote_host, "Cookie": f"{access.COOKIE}={TOKEN}"}) as ws:
        assert ws.receive_text() == "hi"
    with pytest.raises(WebSocketDisconnect):
        with client(LOCAL).websocket_connect("/ws", headers={**local_host, "Origin": "https://evil.example"}) as ws:
            ws.receive_text()
    with client(LOCAL).websocket_connect("/ws", headers={**local_host, "Origin": "http://localhost:5180"}) as ws:
        assert ws.receive_text() == "hi"


def test_reverse_proxy_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_NO_TOKEN", "0")
    assert client(LOCAL).post("/api/run").status_code == 401


def test_token_file_created_private(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_TOKEN")
    monkeypatch.setattr(access, "TOKEN_FILE", tmp_path / "access_token")
    monkeypatch.setattr(access, "_token_cache", "")
    tok = access.access_token()
    assert len(tok) >= 24
    assert (tmp_path / "access_token").read_text().strip() == tok
    assert (tmp_path / "access_token").stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(access, "_token_cache", "")
    assert access.access_token() == tok


def test_access_links_only_on_this_machine():
    import server
    fake = {"lan_ip": "192.168.2.10", "tailscale_ip": "100.101.102.103", "tailscale_dns": "mac.example.ts.net"}
    with patch.object(server, "_detect_network_addresses", return_value=fake), \
         patch.object(server, "_bind_host", return_value="0.0.0.0"):
        local = TestClient(server.app, base_url="http://localhost:5180", client=LOCAL).get("/api/access").json()
        remote = TestClient(server.app, base_url="http://100.101.102.103:5180", client=REMOTE).get(
            "/api/access", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert [u["url"] for u in local["urls"]] == [
        f"http://192.168.2.10:5180/?token={TOKEN}",
        f"http://100.101.102.103:5180/?token={TOKEN}",
        f"http://mac.example.ts.net:5180/?token={TOKEN}"]
    assert remote.get("error")
    with patch.object(server, "_bind_host", return_value="127.0.0.1"):
        only = TestClient(server.app, base_url="http://localhost:5180", client=LOCAL).get("/api/access").json()
    assert only == {"urls": [], "local_only": True}
