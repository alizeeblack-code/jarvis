"""Who may use the manager.

Anyone who can reach the API can start agents that run with full permissions in
any folder, so:

- This machine (loopback peer *and* a loopback Host header, which also stops DNS
  rebinding) needs no token.
- Everyone else needs the access token: cookie `jarvis_token`, header
  `Authorization: Bearer …` / `X-Jarvis-Token`, or `?token=` once (a page
  navigation then turns it into the cookie and drops it from the address bar).
- Browsers may not call in from other sites at all (Origin / Sec-Fetch-Site),
  so a web page you visit cannot drive the manager on localhost either.

The token comes from $JARVIS_TOKEN, else data/access_token (created on first use).
Behind a reverse proxy on this machine set JARVIS_LOCAL_NO_TOKEN=0: every
request would look local otherwise.
"""

import hmac
import ipaddress
import json
import os
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode, urlsplit

from config import DATA_DIR

TOKEN_FILE = DATA_DIR / "access_token"
COOKIE = "jarvis_token"
COOKIE_MAX_AGE = 400 * 24 * 3600          # browsers cap cookie lifetime at 400 days
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
_token_cache = ""


def access_token() -> str:
    global _token_cache
    env = os.environ.get("JARVIS_TOKEN", "").strip()
    if env:
        return env
    if _token_cache:
        return _token_cache
    try:
        tok = TOKEN_FILE.read_text("utf-8").strip()
    except OSError:
        tok = ""
    if not tok:
        tok = secrets.token_urlsafe(24)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok + "\n")
    _token_cache = tok
    return tok


def _hostname(host: str) -> str:
    try:
        return (urlsplit("//" + host).hostname or "").lower()
    except ValueError:
        return ""


def _loopback_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def is_local(client_ip: str, host: str) -> bool:
    """A request from this machine to a loopback name."""
    if os.environ.get("JARVIS_LOCAL_NO_TOKEN", "1") == "0":
        return False
    name = _hostname(host)
    return _loopback_ip(client_ip) and (name in _LOCAL_NAMES or name.endswith(".localhost"))


def token_ok(given: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), access_token().encode())


def cross_site(scope_type: str, method: str, path: str, host: str, headers: dict) -> bool:
    """A browser request started by some other site's page."""
    origin = headers.get("origin")
    if origin is not None:
        o = urlsplit(origin)
        if o.scheme not in ("http", "https") or (o.netloc or "").lower() != host.lower():
            return True
    site = headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        # following a link here from elsewhere is fine; anything else is not
        navigation = (scope_type == "http" and method == "GET"
                      and headers.get("sec-fetch-mode") == "navigate" and not path.startswith("/api"))
        return not navigation
    return False


def _given_token(headers: dict, query: dict) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if headers.get("x-jarvis-token"):
        return headers["x-jarvis-token"].strip()
    if query.get("token"):
        return query["token"].strip()
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except Exception:                                   # noqa: BLE001
        return ""
    return cookie[COOKIE].value if COOKIE in cookie else ""


def with_token(url: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}token={access_token()}"


LOGIN_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jarvis</title>
<style>
:root{color-scheme:light dark;--bg:#f6f6f4;--fg:#1d1d1b;--line:#d6d6d0;--btn:#1d1d1b;--btnfg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#eceae4;--line:#3a3a37;--btn:#eceae4;--btnfg:#161615}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
form{display:flex;flex-direction:column;gap:12px;width:min(320px,calc(100vw - 32px))}
h1{font-size:20px;margin:0 0 4px}
input{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit}
button{font:inherit;padding:10px;border:0;border-radius:8px;background:var(--btn);color:var(--btnfg)}
p{margin:0;font-size:13px;opacity:.65}
</style></head><body><form method="get">
<h1>Jarvis</h1>
<input name="token" type="password" placeholder="访问令牌" autocomplete="current-password" autofocus required>
<button>进入</button>
<p>令牌在运行 Jarvis 的电脑上：页头「手机访问」或 data/access_token</p>
</form></body></html>"""


class AccessMiddleware:
    """Pure ASGI, so it guards the WebSocket as well as HTTP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        query = dict(parse_qsl(scope.get("query_string", b"").decode("latin-1")))

        if cross_site(scope["type"], method, path, host, headers):
            return await self._deny(scope, receive, send, 403, "跨站请求已拒绝")

        client_ip = (scope.get("client") or ("", 0))[0]
        local = is_local(client_ip, host)
        given = _given_token(headers, query)
        if not local and not token_ok(given):
            return await self._deny(scope, receive, send, 401, "需要访问令牌")
        scope.setdefault("state", {})["local"] = local

        # ?token= on a page load: remember it in a cookie and clean up the address bar
        if scope["type"] == "http" and method == "GET" and "token" in query and token_ok(query["token"]):
            rest = urlencode([(k, v) for k, v in query.items() if k != "token"])
            secure = "; Secure" if scope.get("scheme") == "https" else ""
            await send({"type": "http.response.start", "status": 303, "headers": [
                (b"location", (path + ("?" + rest if rest else "")).encode("latin-1")),
                (b"set-cookie", f"{COOKIE}={query['token']}; Path=/; Max-Age={COOKIE_MAX_AGE}; "
                                f"HttpOnly; SameSite=Lax{secure}".encode("latin-1")),
                (b"cache-control", b"no-store"),
            ]})
            return await send({"type": "http.response.body", "body": b""})
        return await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status, message):
        if scope["type"] == "websocket":
            await receive()                               # websocket.connect
            return await send({"type": "websocket.close", "code": 4000 + status})
        wants_page = (scope.get("method") == "GET" and not scope.get("path", "").startswith("/api")
                      and status == 401)
        if wants_page:
            body, ctype = LOGIN_PAGE.encode(), b"text/html; charset=utf-8"
        else:
            body, ctype = json.dumps({"error": message}, ensure_ascii=False).encode(), b"application/json"
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
