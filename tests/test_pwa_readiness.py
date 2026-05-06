"""Tests for the changes that prep mcp-bookmarks to serve the Blogmarks PWA:
/health endpoint, tightened CORS, and bearer-token auth (Cognito JWT and
``bm_v1_*`` scoped token paths).

These tests instantiate the combined Starlette app via ``create_combined_app``
and drive it through ``httpx.ASGITransport`` wrapped in
``asgi_lifespan.LifespanManager`` so the streamable HTTP session manager
actually starts. ``MCP_BEARER_AUTH`` is toggled per-test via ``monkeypatch`` so
the default (disabled) behaviour stays the same for existing deployments.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def shared_client():
    # FastMCP's StreamableHTTPSessionManager refuses to be started twice, so
    # the app + lifespan are module-scoped. Per-test config is driven via
    # env vars that the middleware re-reads on every request.
    from mcp_bookmarks.server import create_combined_app
    app = create_combined_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ── /health ─────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_health_unauthenticated(monkeypatch, shared_client):
    """ALB target group hits /health with no auth; must be 200."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client")
    r = await shared_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── CORS ────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_cors_allows_blogmarks_origin(monkeypatch, shared_client):
    """Preflight from https://blogmarks.dev returns the matching ACAO header."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "false")
    r = await shared_client.options(
        "/mcp",
        headers={
            "Origin": "https://blogmarks.dev",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://blogmarks.dev"
    allow_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allow_headers and "content-type" in allow_headers


@pytest.mark.asyncio(loop_scope="module")
async def test_cors_rejects_other_origin(monkeypatch, shared_client):
    """Preflight from a non-allow-listed origin must NOT receive an ACAO header."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "false")
    r = await shared_client.options(
        "/mcp",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-allow-origin") in (None, "")


# ── BearerAuthMiddleware: disabled by default ───────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_bearer_auth_disabled_passthrough(monkeypatch, shared_client):
    """When MCP_BEARER_AUTH is unset, middleware is inert — /mcp does not 401."""
    monkeypatch.delenv("MCP_BEARER_AUTH", raising=False)
    r = await shared_client.post("/mcp", json={})
    # The streamable-http app may return its own status (200/400/406 etc.); the
    # only thing this test asserts is that the bearer middleware did NOT block.
    assert r.status_code != 401


# ── BearerAuthMiddleware: enabled, token paths ──────────────────────


@pytest.mark.asyncio(loop_scope="module")
async def test_bearer_auth_missing_token_rejected(monkeypatch, shared_client):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    r = await shared_client.post("/mcp", json={})
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


@pytest.mark.asyncio(loop_scope="module")
async def test_bearer_auth_invalid_token_rejected(monkeypatch, shared_client):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client")
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=None,
    ), patch(
        "mcp_bookmarks.bearer_auth._ScopedTokenVerifier.verify",
        new_callable=AsyncMock,
        return_value=None,
    ):
        r = await shared_client.post(
            "/mcp",
            headers={"Authorization": "Bearer eyJfake.token.here"},
            json={},
        )
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_token"}


@pytest.mark.asyncio(loop_scope="module")
async def test_bearer_auth_cognito_accepts_valid_jwt(monkeypatch, shared_client):
    """Valid Cognito ID token populates request.state and lets the request through."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client")
    monkeypatch.setenv("DYNAMODB_ORG_ID", "blogmarks")
    fake_claims = {"sub": "user-123", "token_use": "id", "exp": 9_999_999_999}
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=fake_claims,
    ):
        r = await shared_client.post(
            "/mcp",
            headers={"Authorization": "Bearer eyJvalid.cognito.token"},
            json={},
        )
    assert r.status_code != 401


@pytest.mark.asyncio(loop_scope="module")
async def test_bearer_auth_scoped_token_accepts_active_row(monkeypatch, shared_client):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    fake_row = {"id": "conn-1", "userId": "user-456", "organizationId": "blogmarks"}
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=None,
    ), patch(
        "mcp_bookmarks.bearer_auth._ScopedTokenVerifier.verify",
        new_callable=AsyncMock,
        return_value=fake_row,
    ):
        r = await shared_client.post(
            "/mcp",
            headers={"Authorization": "Bearer bm_v1_xxxxxxxx"},
            json={},
        )
    assert r.status_code != 401


@pytest.mark.asyncio(loop_scope="module")
async def test_bearer_auth_scoped_token_paused_rejected(monkeypatch, shared_client):
    """A paused/revoked connection row → ``_ScopedTokenVerifier.verify`` returns None → 401."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=None,
    ), patch(
        "mcp_bookmarks.bearer_auth._ScopedTokenVerifier.verify",
        new_callable=AsyncMock,
        return_value=None,
    ):
        r = await shared_client.post(
            "/mcp",
            headers={"Authorization": "Bearer bm_v1_paused"},
            json={},
        )
    assert r.status_code == 401
