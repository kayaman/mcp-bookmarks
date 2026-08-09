"""Bearer-token auth middleware tests.

Covers:
- `_CognitoVerifier` (configuration, issuer/jwks URLs, JWT decode via
  monkeypatched `jwt.decode`, token_use filter, failure flattening)
- `_ScopedTokenVerifier` (SHA-256 hash, DynamoDB GSI Query + scan
  fallback via moto, paused/revoked filtering, missing token)
- `BearerAuthMiddleware` (enable flag, OPTIONS pass-through, public-path
  short-circuit, /api/* delegation, missing bearer 401, valid Cognito,
  valid scoped token, invalid token fallthrough 401)
- `request_user_id` helper

Deferred (would need live AWS): the actual `PyJWKClient.get_signing_key_from_jwt`
HTTP roundtrip — we patch `jwt.decode` directly so we never call out.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_bookmarks.bearer_auth import (
    BearerAuthMiddleware,
    _CognitoVerifier,
    _ScopedTokenVerifier,
    request_user_id,
)

# ── _CognitoVerifier: pure parsing / configuration ─────────────────────


def test_cognito_verifier_not_configured_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)
    v = _CognitoVerifier()
    assert v.configured is False
    # Even with a valid-looking JWT, returns None when not configured.
    assert v.verify("eyJfake") is None


def test_cognito_verifier_configured_when_both_envs_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    v = _CognitoVerifier()
    assert v.configured is True


def test_cognito_verifier_issuer_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    v = _CognitoVerifier()
    assert v.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AAA"
    assert v.jwks_url == v.issuer + "/.well-known/jwks.json"


def test_cognito_verifier_region_defaults_to_us_east_1(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    monkeypatch.delenv("COGNITO_REGION", raising=False)
    v = _CognitoVerifier()
    assert "us-east-1" in v.issuer


def test_cognito_verifier_rejects_non_jwt_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    v = _CognitoVerifier()
    # Tokens that don't start with `eyJ` (the base64 of `{"typ":...`) short-circuit.
    assert v.verify("bm_v1_some_scoped_token") is None
    assert v.verify("random") is None


def test_cognito_verifier_returns_claims_for_id_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")

    fake_claims = {
        "sub": "user-abc",
        "token_use": "id",
        "exp": 9999999999,
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AAA",
        "aud": "client123",
    }
    # Patch the PyJWKClient so we never make a network call.
    monkeypatch.setattr(
        "mcp_bookmarks.bearer_auth.PyJWKClient",
        lambda *a, **kw: MagicMock(get_signing_key_from_jwt=lambda t: MagicMock(key="fake-key")),
    )
    monkeypatch.setattr(jwt, "decode", lambda *a, **kw: fake_claims)
    v = _CognitoVerifier()
    claims = v.verify("eyJhbGciOiJSUzI1NiJ9.payload.sig")
    assert claims == fake_claims


def test_cognito_verifier_rejects_access_token(monkeypatch: pytest.MonkeyPatch):
    """Access tokens (token_use=access) are rejected — only id-tokens accepted."""
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    monkeypatch.setattr(
        "mcp_bookmarks.bearer_auth.PyJWKClient",
        lambda *a, **kw: MagicMock(get_signing_key_from_jwt=lambda t: MagicMock(key="k")),
    )
    monkeypatch.setattr(jwt, "decode", lambda *a, **kw: {"token_use": "access", "sub": "u"})
    v = _CognitoVerifier()
    assert v.verify("eyJfake") is None


def test_cognito_verifier_returns_none_on_jwt_error(monkeypatch: pytest.MonkeyPatch):
    """Signature failure, expired, missing audience all flatten to None."""
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    monkeypatch.setattr(
        "mcp_bookmarks.bearer_auth.PyJWKClient",
        lambda *a, **kw: MagicMock(get_signing_key_from_jwt=lambda t: MagicMock(key="k")),
    )

    def _raise(*a, **kw):
        raise jwt.ExpiredSignatureError("expired")

    monkeypatch.setattr(jwt, "decode", _raise)
    v = _CognitoVerifier()
    assert v.verify("eyJfake") is None


def test_cognito_client_is_lazy_and_cached(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    construct_count = {"n": 0}

    def _factory(*args, **kwargs):
        construct_count["n"] += 1
        return MagicMock()

    monkeypatch.setattr("mcp_bookmarks.bearer_auth.PyJWKClient", _factory)
    v = _CognitoVerifier()
    assert v._client() is v._client()
    assert construct_count["n"] == 1


# ── _ScopedTokenVerifier: SHA-256 hash + DynamoDB lookup ───────────────


def test_scoped_hash_is_sha256_hex():
    token = "bm_v1_abc"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert _ScopedTokenVerifier._hash(token) == expected


async def test_scoped_verifier_rejects_non_bm_v1_prefix():
    v = _ScopedTokenVerifier()
    assert await v.verify("eyJfake") is None
    assert await v.verify("random") is None


async def _create_connections_table(table_name: str):
    """Create the mcp-connections table with the tokenHash-index GSI."""
    import boto3

    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "id", "AttributeType": "S"},
            {"AttributeName": "tokenHash", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "tokenHash-index",
                "KeySchema": [{"AttributeName": "tokenHash", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


async def test_scoped_verifier_finds_token_via_gsi(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")

    token = "bm_v1_alpha"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with mock_aws():
        await _create_connections_table("test-conns")
        import boto3

        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns").put_item(
            Item={
                "id": "conn-1",
                "tokenHash": token_hash,
                "userId": "user-alpha",
                "organizationId": "org-alpha",
            }
        )

        v = _ScopedTokenVerifier()
        row = await v.verify(token)
        assert row is not None
        assert row["userId"] == "user-alpha"
        assert row["organizationId"] == "org-alpha"


async def test_scoped_verifier_returns_none_when_token_unknown(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")

    with mock_aws():
        await _create_connections_table("test-conns")
        v = _ScopedTokenVerifier()
        assert await v.verify("bm_v1_nonexistent") is None


async def test_scoped_verifier_rejects_paused_row(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")

    token = "bm_v1_paused"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with mock_aws():
        await _create_connections_table("test-conns")
        import boto3

        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns").put_item(
            Item={
                "id": "conn-paused",
                "tokenHash": token_hash,
                "userId": "u",
                "paused": True,
            }
        )

        v = _ScopedTokenVerifier()
        assert await v.verify(token) is None


async def test_scoped_verifier_rejects_revoked_row(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")

    token = "bm_v1_revoked"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with mock_aws():
        await _create_connections_table("test-conns")
        import boto3

        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns").put_item(
            Item={
                "id": "conn-revoked",
                "tokenHash": token_hash,
                "userId": "u",
                "revoked": True,
            }
        )

        v = _ScopedTokenVerifier()
        assert await v.verify(token) is None


async def test_scoped_verifier_fallback_scan_when_gsi_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the GSI query raises (no index), fall back to a paginated scan."""
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns-noindex")

    token = "bm_v1_fallback"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with mock_aws():
        # Build the table WITHOUT the tokenHash-index — forces the except branch.
        import boto3

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-conns-noindex",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns-noindex").put_item(
            Item={
                "id": "conn-fb",
                "tokenHash": token_hash,
                "userId": "u-fb",
            }
        )

        v = _ScopedTokenVerifier()
        row = await v.verify(token)
        assert row is not None
        assert row["userId"] == "u-fb"


# ── BearerAuthMiddleware ───────────────────────────────────────────────


def _app_with_bearer(monkeypatch: pytest.MonkeyPatch) -> Starlette:
    """Tiny Starlette app with one protected route and one public route."""

    async def protected(req: Request) -> JSONResponse:
        return JSONResponse(
            {
                "user_id": getattr(req.state, "user_id", None),
                "tenant_id": getattr(req.state, "tenant_id", None),
                "auth_kind": getattr(req.state, "auth_kind", None),
            }
        )

    async def public(req: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/mcp/whoami", protected, methods=["GET"]),
            Route("/health", public, methods=["GET"]),
            Route("/api/anything", public, methods=["GET"]),
            Route("/other", public, methods=["GET"]),  # not protected prefix
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
    )


async def _send(app: Starlette, method: str, path: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.request(method, path, **kw)


async def test_middleware_disabled_passes_through(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_BEARER_AUTH", raising=False)
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "GET", "/mcp/whoami")
    assert r.status_code == 200


async def test_middleware_options_passes_through(monkeypatch: pytest.MonkeyPatch):
    """OPTIONS short-circuits in the middleware — proven by NOT getting 401.

    Starlette's router returns 405 for OPTIONS on a GET-only route; we only
    care that we didn't get the middleware's 401 unauthorized envelope.
    """
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "OPTIONS", "/mcp/whoami")
    assert r.status_code != 401


async def test_middleware_public_path_passes_through(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "GET", "/health")
    assert r.status_code == 200


async def test_middleware_api_prefix_passes_through(monkeypatch: pytest.MonkeyPatch):
    """``/api/*`` is delegated to TenantAuthMiddleware on the inner app."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "GET", "/api/anything")
    assert r.status_code == 200


async def test_middleware_unprotected_prefix_passes_through(
    monkeypatch: pytest.MonkeyPatch,
):
    """Paths outside /mcp, /sse, /messages aren't protected by this middleware."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "GET", "/other")
    assert r.status_code == 200


async def test_middleware_missing_bearer_returns_401(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "GET", "/mcp/whoami")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


async def test_middleware_empty_bearer_returns_401(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_bearer(monkeypatch)
    r = await _send(app, "GET", "/mcp/whoami", headers={"authorization": "Bearer  "})
    assert r.status_code == 401


async def test_middleware_invalid_token_returns_401(monkeypatch: pytest.MonkeyPatch):
    """Neither Cognito nor scoped lookup matches → 401 invalid_token.

    The scoped verifier needs SOMETHING to scan; moto provides an empty
    table so the lookup returns None gracefully (vs blowing up against
    a missing real-AWS table).
    """
    from moto import mock_aws

    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "empty-conns")
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_CLIENT_ID", raising=False)

    with mock_aws():
        await _create_connections_table("empty-conns")  # empty table
        app = _app_with_bearer(monkeypatch)
        r = await _send(
            app, "GET", "/mcp/whoami", headers={"authorization": "Bearer bm_v1_unknown"}
        )

    assert r.status_code == 401
    assert r.json() == {"error": "invalid_token"}


async def test_middleware_accepts_cognito_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_AAA")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client123")
    monkeypatch.setenv("DYNAMODB_ORG_ID", "org-cog")

    monkeypatch.setattr(
        "mcp_bookmarks.bearer_auth.PyJWKClient",
        lambda *a, **kw: MagicMock(get_signing_key_from_jwt=lambda t: MagicMock(key="k")),
    )
    monkeypatch.setattr(jwt, "decode", lambda *a, **kw: {"token_use": "id", "sub": "u-cog"})

    app = _app_with_bearer(monkeypatch)
    r = await _send(
        app, "GET", "/mcp/whoami", headers={"authorization": "Bearer eyJfake.payload.sig"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "u-cog"
    assert body["tenant_id"] == "org-cog"
    assert body["auth_kind"] == "cognito"


async def test_middleware_accepts_scoped_token(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    token = "bm_v1_x"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with mock_aws():
        await _create_connections_table("test-conns")
        import boto3

        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns").put_item(
            Item={
                "id": "conn-x",
                "tokenHash": token_hash,
                "userId": "u-scoped",
                "organizationId": "org-scoped",
            }
        )

        app = _app_with_bearer(monkeypatch)
        r = await _send(app, "GET", "/mcp/whoami", headers={"authorization": f"Bearer {token}"})

    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "u-scoped"
    assert body["tenant_id"] == "org-scoped"
    assert body["auth_kind"] == "scoped_token"


# ── request_user_id helper ─────────────────────────────────────────────


def test_request_user_id_returns_none_when_ctx_lacks_request_context():
    """ctx without request_context attribute → None (stdio transport case)."""

    class _Ctx:
        pass

    assert request_user_id(_Ctx()) is None


def test_request_user_id_returns_none_when_request_is_none():
    class _Ctx:
        class request_context:
            request = None

    assert request_user_id(_Ctx()) is None


def test_request_user_id_returns_user_id_from_state():
    class _State:
        user_id = "u-1"

    class _Req:
        state = _State()

    class _Ctx:
        class request_context:
            request = _Req()

    assert request_user_id(_Ctx()) == "u-1"


def test_request_user_id_returns_none_when_state_lacks_user_id():
    class _State:
        pass

    class _Req:
        state = _State()

    class _Ctx:
        class request_context:
            request = _Req()

    assert request_user_id(_Ctx()) is None


# ── Admin tag-editing carve-out (Phase 1) ──────────────────────────────


def _app_with_tag_routes() -> Starlette:
    """Outer-app shape: routes carry the /api prefix, BearerAuthMiddleware on top."""

    async def echo(req: Request) -> JSONResponse:
        return JSONResponse(
            {
                "user_id": getattr(req.state, "user_id", None),
                "auth_kind": getattr(req.state, "auth_kind", None),
                "write_enabled": getattr(req.state, "write_enabled", None),
            }
        )

    return Starlette(
        routes=[
            Route("/api/bookmarks/{bid}/tags", echo, methods=["POST", "PUT"]),
            Route("/api/bookmarks/recent", echo, methods=["GET"]),
            Route("/api/tag-edits", echo, methods=["GET"]),
            Route("/api/stats", echo, methods=["GET"]),
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
    )


async def test_put_bookmark_tags_requires_bearer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_tag_routes()
    r = await _send(app, "PUT", "/api/bookmarks/abc/tags")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


async def test_get_recent_and_tag_edits_require_bearer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_tag_routes()
    assert (await _send(app, "GET", "/api/bookmarks/recent")).status_code == 401
    assert (await _send(app, "GET", "/api/tag-edits")).status_code == 401


async def test_post_bookmark_tags_still_delegated(monkeypatch: pytest.MonkeyPatch):
    """The additive POST keeps static-key auth — NOT carved out."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_tag_routes()
    assert (await _send(app, "POST", "/api/bookmarks/abc/tags")).status_code == 200


async def test_other_api_paths_still_delegated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    app = _app_with_tag_routes()
    assert (await _send(app, "GET", "/api/stats")).status_code == 200


async def test_carved_route_accepts_scoped_token_and_sets_state(
    monkeypatch: pytest.MonkeyPatch,
):
    from moto import mock_aws

    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    token = "bm_v1_tagedit"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with mock_aws():
        await _create_connections_table("test-conns")
        import boto3

        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns").put_item(
            Item={
                "id": "conn-t",
                "tokenHash": token_hash,
                "userId": "u-owner",
                "writeEnabled": True,
                "scope": {"type": "all_private"},
            }
        )
        app = _app_with_tag_routes()
        r = await _send(
            app, "PUT", "/api/bookmarks/abc/tags", headers={"authorization": f"Bearer {token}"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "u-owner"
    assert body["auth_kind"] == "scoped_token"
    assert body["write_enabled"] is True


async def test_composed_mount_exempts_carved_route_from_tenant_auth(
    monkeypatch: pytest.MonkeyPatch,
):
    """Full production topology: outer BearerAuthMiddleware + Mount("/api", api_app).

    ``api_app`` (created by ``create_api_app``) registers its own routes
    WITHOUT the ``/api`` prefix and carries ``TenantAuthMiddleware`` when
    ``MCP_API_KEYS`` is set. Starlette's ``Mount`` strips the prefix from
    ``scope["path"]`` (what routing matches on) but NOT from
    ``request.url.path`` (reconstructed from ``root_path`` + ``path``), so
    ``TenantAuthMiddleware`` still sees the full ``/api``-prefixed path here
    — this proves the exemption must strip that prefix itself before
    calling ``bm_v1_api_route`` (see api.py's ``TenantAuthMiddleware.dispatch``).
    """
    from moto import mock_aws
    from starlette.routing import Mount

    from mcp_bookmarks.api import create_api_app

    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    monkeypatch.setenv("MCP_API_KEYS", "secret-key:tenant-a")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("MCP_CONNECTIONS_TABLE", "test-conns")
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)

    token = "bm_v1_composed"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    with mock_aws():
        await _create_connections_table("test-conns")
        import boto3

        boto3.resource("dynamodb", region_name="us-east-1").Table("test-conns").put_item(
            Item={
                "id": "conn-composed",
                "tokenHash": token_hash,
                "userId": "u-composed",
                "writeEnabled": True,
                "scope": {"type": "all_private"},
            }
        )
        api_app = create_api_app()  # mounts TenantAuthMiddleware (MCP_API_KEYS is set)
        outer = Starlette(
            routes=[Mount("/api", app=api_app)],
            middleware=[Middleware(BearerAuthMiddleware)],
        )

        # Carved route: no x-api-key header. TenantAuthMiddleware must exempt
        # it (proven by NOT getting its 401), reaching the real PUT handler
        # instead — which clears the write-policy check (writeEnabled +
        # all_private scope) and then 400s on the empty request body.
        r_carved = await _send(
            outer,
            "PUT",
            "/api/bookmarks/abc/tags",
            headers={"authorization": f"Bearer {token}"},
        )
        assert r_carved.status_code == 400
        assert r_carved.json()["error"]["code"] == "invalid_json"

        # Non-carved route: TenantAuthMiddleware still enforces the static key.
        r_plain = await _send(outer, "GET", "/api/stats")
    assert r_plain.status_code == 401
    assert r_plain.json()["error"]["code"] == "unauthorized"


# Silence "unused" warnings for the Any import on stricter linters.
_: Any = None
