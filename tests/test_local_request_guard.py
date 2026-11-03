"""DNS rebinding and cross-site writes must not reach the login-free local server."""

import asyncio

import pytest

from utils.local_request_guard import LocalRequestGuard, problem


@pytest.mark.parametrize("host", [
    "127.0.0.1:8002", "localhost:8002", "[::1]:8002", "movie-searcher.localhost",
    "tvnik-movies.localhost", "localhost:8012", "LOCALHOST:8002",
])
def test_loopback_hosts_are_served(host):
    assert problem("GET", {"host": host}) is None


@pytest.mark.parametrize("host", [
    "rebind.attacker.example:8002", "127.0.0.1.nip.io:8002", "localhost.attacker.example",
    "192.168.1.27:8002", "",
])
def test_rebinding_hosts_are_refused(host):
    assert problem("GET", {"host": host}) == "host"


@pytest.mark.parametrize("method,origin,fetch_site", [
    ("POST", "https://attacker.example", None),
    ("POST", "null", None),
    ("PUT", "http://127.0.0.1.attacker.example:8002", None),
    ("DELETE", None, "cross-site"),
])
def test_cross_site_writes_are_refused(method, origin, fetch_site):
    headers = {"host": "127.0.0.1:8002"}
    if origin is not None:
        headers["origin"] = origin
    if fetch_site is not None:
        headers["sec-fetch-site"] = fetch_site
    assert problem(method, headers) is not None


@pytest.mark.parametrize("host,origin", [
    ("127.0.0.1:8002", "http://127.0.0.1:8002"),
    ("movie-searcher.localhost", "http://movie-searcher.localhost"),
    ("[::1]:8002", "http://[::1]:8002"),
    ("127.0.0.1:8002", None),
])
def test_own_pages_and_scripts_may_write(host, origin):
    headers = {"host": host} if origin is None else {"host": host, "origin": origin}
    assert problem("POST", headers) is None


def _call(headers, method="POST"):
    reached = []
    sent = []

    async def app(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "method": method, "path": "/api/server/restart",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }
    asyncio.run(LocalRequestGuard(app)(scope, receive, send))
    return reached, sent


def test_refused_request_never_reaches_the_app():
    reached, sent = _call({"host": "rebind.attacker.example:8002", "origin": "http://rebind.attacker.example:8002"})
    assert reached == []
    assert sent[0]["status"] == 403


def test_allowed_request_reaches_the_app():
    reached, sent = _call({"host": "127.0.0.1:8002", "origin": "http://127.0.0.1:8002"})
    assert reached == ["/api/server/restart"]
    assert sent == []
