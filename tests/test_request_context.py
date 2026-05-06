"""Tests for per-request user_id propagation.

Verifies:

1. ``request_context.current_user_id`` is async-task-local (no leakage between
   concurrent requests).
2. ``DynamoDBDatabase._user_id`` honours the contextvar when set, with the
   existing env-var fallback intact.
3. ``DynamoDBDatabase._request_user_filter`` returns ``None`` ambient and a
   non-None Attr expression when a request user is in scope.
4. ``DynamoDBDatabase._item_org_visible`` rejects items belonging to other
   users when a request user is in scope.
5. ``BearerAuthMiddleware.dispatch`` sets the contextvar around the inner
   ``call_next`` and resets it after — verified by mocking ``call_next``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_bookmarks.request_context import (
    current_user_id,
    current_tenant_id,
    set_request_identity,
    reset_request_identity,
)


# ── ContextVar isolation ────────────────────────────────────────────


def test_default_is_none():
    assert current_user_id() is None
    assert current_tenant_id() is None


def test_set_and_reset_roundtrip():
    tokens = set_request_identity("user-1", "org-1")
    try:
        assert current_user_id() == "user-1"
        assert current_tenant_id() == "org-1"
    finally:
        reset_request_identity(tokens)
    assert current_user_id() is None
    assert current_tenant_id() is None


@pytest.mark.asyncio
async def test_concurrent_tasks_isolated():
    """Two concurrent asyncio tasks must each see their own user_id."""
    seen: dict[str, str | None] = {}

    async def worker(uid: str):
        tokens = set_request_identity(uid, "org-shared")
        try:
            await asyncio.sleep(0.01)
            seen[uid] = current_user_id()
        finally:
            reset_request_identity(tokens)

    await asyncio.gather(worker("alice"), worker("bob"), worker("carol"))
    assert seen == {"alice": "alice", "bob": "bob", "carol": "carol"}
    assert current_user_id() is None


# ── DynamoDBDatabase reads contextvar ───────────────────────────────


def _build_db():
    """Construct a DynamoDBDatabase without touching real boto3."""
    from mcp_bookmarks.dynamodb import DynamoDBDatabase
    with patch("mcp_bookmarks.dynamodb._dynamo") as fake_dynamo:
        fake_dynamo.return_value.Table.return_value = object()
        return DynamoDBDatabase()


def test_dynamo_user_id_prefers_contextvar(monkeypatch):
    monkeypatch.setenv("DYNAMODB_USER_ID", "mcp-agent")
    db = _build_db()
    assert db._user_id() == "mcp-agent"
    tokens = set_request_identity("cognito-sub-123", "default")
    try:
        assert db._user_id() == "cognito-sub-123"
    finally:
        reset_request_identity(tokens)


def test_request_user_filter_none_ambient(monkeypatch):
    db = _build_db()
    assert db._request_user_filter() is None


def test_request_user_filter_set_when_request_user_in_scope(monkeypatch):
    db = _build_db()
    tokens = set_request_identity("user-X", "default")
    try:
        f = db._request_user_filter()
        assert f is not None
        # Sanity check that the Attr expression mentions our user via its
        # public attribute_values, not via repr (which is private/unstable).
        assert "user-X" in str(f.get_expression())
    finally:
        reset_request_identity(tokens)


def test_item_visibility_rejects_other_users(monkeypatch):
    """Items belonging to other users fail the per-item visibility check."""
    db = _build_db()
    mine = {"id": "1", "userId": "user-X", "url": "https://a"}
    other = {"id": "2", "userId": "user-Y", "url": "https://b"}
    no_user = {"id": "3", "url": "https://c"}

    # No request user in scope → permissive (single-tenant behaviour preserved).
    assert db._item_org_visible(mine) is True
    assert db._item_org_visible(other) is True
    assert db._item_org_visible(no_user) is True

    tokens = set_request_identity("user-X", "default")
    try:
        assert db._item_org_visible(mine) is True
        assert db._item_org_visible(other) is False
        assert db._item_org_visible(no_user) is False
    finally:
        reset_request_identity(tokens)


# ── BearerAuthMiddleware sets the contextvar around call_next ───────


@pytest.mark.asyncio
async def test_middleware_cognito_sets_contextvar_around_call_next(monkeypatch):
    """When the Cognito path succeeds, current_user_id() must equal the JWT
    sub inside call_next, and revert to None after the response."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client")
    monkeypatch.setenv("DYNAMODB_ORG_ID", "blogmarks")

    from mcp_bookmarks.bearer_auth import BearerAuthMiddleware

    seen: dict[str, str | None] = {}

    async def fake_call_next(_request):
        seen["uid_inside"] = current_user_id()
        seen["tid_inside"] = current_tenant_id()
        # Return a minimal response object that the middleware can pass through.
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    # Build a Request with a path that triggers auth (/mcp).
    request = MagicMock()
    request.method = "POST"
    request.url.path = "/mcp"
    request.headers = {"authorization": "Bearer eyJfake.cognito.token"}
    request.state = MagicMock()

    # Bypass __init__ side effects with a direct instance, then call dispatch.
    middleware = BearerAuthMiddleware(app=lambda *a, **kw: None)

    fake_claims = {"sub": "cognito-abc", "token_use": "id", "exp": 9_999_999_999}
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=fake_claims,
    ):
        resp = await middleware.dispatch(request, fake_call_next)

    assert resp.status_code == 200
    assert seen["uid_inside"] == "cognito-abc"
    assert seen["tid_inside"] == "blogmarks"
    # Outside the request, contextvar is back to default.
    assert current_user_id() is None
    assert current_tenant_id() is None


@pytest.mark.asyncio
async def test_middleware_scoped_token_sets_contextvar(monkeypatch):
    """bm_v1_* token path also sets the contextvar (using the row's userId)."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")

    from mcp_bookmarks.bearer_auth import BearerAuthMiddleware

    seen: dict[str, str | None] = {}

    async def fake_call_next(_request):
        seen["uid_inside"] = current_user_id()
        seen["tid_inside"] = current_tenant_id()
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    request = MagicMock()
    request.method = "POST"
    request.url.path = "/mcp"
    request.headers = {"authorization": "Bearer bm_v1_xxxxxxx"}
    request.state = MagicMock()

    middleware = BearerAuthMiddleware(app=lambda *a, **kw: None)
    fake_row = {"id": "conn-1", "userId": "user-456", "organizationId": "blogmarks-team"}
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=None,
    ), patch(
        "mcp_bookmarks.bearer_auth._ScopedTokenVerifier.verify",
        new_callable=AsyncMock,
        return_value=fake_row,
    ):
        resp = await middleware.dispatch(request, fake_call_next)

    assert resp.status_code == 200
    assert seen["uid_inside"] == "user-456"
    assert seen["tid_inside"] == "blogmarks-team"
    assert current_user_id() is None


@pytest.mark.asyncio
async def test_middleware_resets_contextvar_on_handler_exception(monkeypatch):
    """If the inner handler raises, the contextvar must still reset."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_TEST")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "test-client")

    from mcp_bookmarks.bearer_auth import BearerAuthMiddleware

    async def boom(_request):
        raise RuntimeError("tool exploded")

    request = MagicMock()
    request.method = "POST"
    request.url.path = "/mcp"
    request.headers = {"authorization": "Bearer eyJfake.cognito.token"}
    request.state = MagicMock()

    middleware = BearerAuthMiddleware(app=lambda *a, **kw: None)
    fake_claims = {"sub": "user-X", "token_use": "id", "exp": 9_999_999_999}
    with patch(
        "mcp_bookmarks.bearer_auth._CognitoVerifier.verify",
        return_value=fake_claims,
    ):
        with pytest.raises(RuntimeError, match="tool exploded"):
            await middleware.dispatch(request, boom)

    # Critical: even on exception, the contextvar must be reset.
    assert current_user_id() is None
