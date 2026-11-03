"""Refuse requests that another web page could make in the owner's browser.

The server has no login and binds 127.0.0.1. DNS rebinding points an attacker's
domain at 127.0.0.1, after which its page reads and writes this server with that
domain as Host; a cross-site form or fetch can also post straight to
127.0.0.1:8002. See docs/TECH_NOTES.md, "Local request guard".
"""

import json
import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_LOGGED_REFUSALS = 20
REFUSAL_BODY = json.dumps({"detail": "This local server only answers its own pages on localhost."}).encode()


def is_local_name(name: str | None) -> bool:
    """localhost, *.localhost, 127.0.0.1 or ::1 (with or without brackets)."""
    if not name:
        return False
    name = name.lower().removeprefix("[").removesuffix("]")
    return name in ("localhost", "127.0.0.1", "::1") or name.endswith(".localhost")


def host_without_port(host: str) -> str:
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def problem(method: str, headers: dict[str, str]) -> str | None:
    """None when the request may proceed; otherwise why it is refused.

    Requests without Origin or Sec-Fetch-Site (curl, scripts) pass.
    """
    if not is_local_name(host_without_port(headers.get("host", ""))):
        return "host"
    if method in SAFE_METHODS:
        return None
    origin = headers.get("origin")
    if origin is not None:
        return None if is_local_name(urlsplit(origin).hostname) else "origin"
    return "cross-site" if headers.get("sec-fetch-site") == "cross-site" else None


class LocalRequestGuard:
    """ASGI middleware; registered first in main.py."""

    def __init__(self, app):
        self.app = app
        self.logged_refusals = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        reason = problem(scope["method"], headers)
        if reason is None:
            await self.app(scope, receive, send)
            return
        if self.logged_refusals < MAX_LOGGED_REFUSALS:
            self.logged_refusals += 1
            logger.warning(
                "Local request guard refused %s %s (%s; host %r, origin %r)",
                scope["method"], scope["path"][:120], reason,
                headers.get("host", "")[:120], headers.get("origin", "")[:120],
            )
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        })
        await send({"type": "http.response.body", "body": REFUSAL_BODY})
