"""CorrelationMiddleware (WDN-397 / OSS-7).

Verifies:
- Inbound ``X-Correlation-ID`` is honored (any header case).
- A UUID4 is minted when absent.
- The contextvar is set during the request and unset after.
- The resolved id is echoed back on the response.
"""

from __future__ import annotations

import re

import httpx
from asgi_lifespan import LifespanManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_bookmarks.correlation import CorrelationMiddleware
from mcp_bookmarks.logging_config import correlation_id_var


def _build_app() -> Starlette:
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "cid_on_state": getattr(request.state, "correlation_id", None),
                "cid_in_var": correlation_id_var.get("-"),
            }
        )

    app = Starlette(routes=[Route("/echo", echo)])
    app.add_middleware(CorrelationMiddleware)
    return app


async def _client(app: Starlette) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_inbound_correlation_id_is_honored():
    app = _build_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.get("/echo", headers={"X-Correlation-ID": "abc-123"})
    assert r.status_code == 200
    assert r.headers["X-Correlation-ID"] == "abc-123"
    body = r.json()
    assert body["cid_on_state"] == "abc-123"
    assert body["cid_in_var"] == "abc-123"


async def test_inbound_correlation_id_is_case_insensitive():
    """Starlette's headers are case-insensitive; we should pick up either case."""
    app = _build_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.get("/echo", headers={"x-correlation-id": "from-lowercase"})
    assert r.headers["X-Correlation-ID"] == "from-lowercase"


async def test_missing_correlation_id_is_minted_as_uuid_hex():
    app = _build_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.get("/echo")
    cid = r.headers["X-Correlation-ID"]
    # UUID4 hex (no dashes) is 32 lowercase hex chars
    assert re.fullmatch(r"[0-9a-f]{32}", cid), f"unexpected cid format: {cid!r}"
    assert r.json()["cid_on_state"] == cid


async def test_contextvar_is_unset_after_response():
    """The middleware uses ``finally: reset(token)`` — confirm no leak between requests."""
    app = _build_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            await c.get("/echo", headers={"X-Correlation-ID": "first"})
    # After the async context closes, the contextvar should be back to default
    assert correlation_id_var.get("-") == "-"


async def test_empty_header_is_treated_as_missing():
    """Empty-string header is falsy → middleware's `or`-short-circuit mints a fresh id.

    (Note: whitespace-only is *truthy* and would be preserved — the middleware
    does not trim. That's a deliberate non-policy: if a caller sends garbage,
    they get it back in the response header to debug.)
    """
    app = _build_app()
    async with LifespanManager(app):
        async with await _client(app) as c:
            r = await c.get("/echo", headers={"X-Correlation-ID": ""})
    cid = r.headers["X-Correlation-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", cid), f"expected minted UUID hex, got {cid!r}"
