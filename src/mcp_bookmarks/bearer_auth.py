"""Bearer-token auth middleware for the combined MCP + REST app.

Resolves the Authorization header using two strategies, in order:

1. **Cognito JWT** (issued by AWS Cognito User Pool, used by first-party
   browser/mobile clients). Verified against the user pool's JWKS; the
   resulting subject becomes the per-request ``user_id``.

2. **Scoped bearer token** (``bm_v1_*``, minted by an upstream provisioner
   and used by external MCP clients like Claude and Cursor). Looked up by
   SHA-256 hash in the ``mcp-connections`` DynamoDB table.

When either succeeds, the middleware sets ``request.state.user_id`` (Cognito sub
or connection user id) and ``request.state.tenant_id`` (the shared
``DYNAMODB_ORG_ID``, defaulting to ``"default"``). MCP tools can read these via
``ctx.request_context.request.state``.

Public paths (``/health``, ``/``, ``/bookmarklet``, ``/ai-gateway``,
``/webhooks/stripe``) and ``OPTIONS`` preflight requests pass through
unauthenticated. The ``/api/*`` mount keeps its own ``TenantAuthMiddleware``
which already handles static ``MCP_API_KEYS``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .request_context import reset_request_identity, set_request_identity

# Routes that never require auth.
_PUBLIC_PATHS = frozenset({"/", "/health", "/bookmarklet", "/ai-gateway", "/webhooks/stripe"})

# Path prefixes that need bearer auth when this middleware is active.
_PROTECTED_PREFIXES = ("/mcp", "/sse", "/messages")

# /api subpaths (relative to the /api mount) that authenticate with a bm_v1
# scoped token instead of the static MCP_API_KEYS (admin tag editing, Phase 1).
_BM_V1_GET_ROUTES = frozenset({"/bookmarks/recent", "/tag-edits"})


def bearer_auth_enabled() -> bool:
    return os.environ.get("MCP_BEARER_AUTH", "").strip().lower() in ("1", "true", "yes")


def bm_v1_api_route(method: str, api_path: str) -> bool:
    """True when this /api subpath authenticates via a bm_v1 bearer token.

    ``api_path`` is relative to the /api mount (no ``/api`` prefix) — the
    shape TenantAuthMiddleware sees inside the mounted app;
    BearerAuthMiddleware strips the prefix before calling. The additive
    ``POST /bookmarks/{id}/tags`` keeps static-key auth; only the
    replace-set ``PUT`` on the same path is carved out.
    """
    if method == "GET":
        return api_path in _BM_V1_GET_ROUTES
    return method == "PUT" and api_path.startswith("/bookmarks/") and api_path.endswith("/tags")


def tag_write_policy_error(request: Any) -> JSONResponse | None:
    """Write-policy gate for the admin tag-editing endpoints.

    Returns ``None`` when the request may mutate tags, else a standardized
    error response. Rules (admin-tag-editing design, "Engine auth"):
      - only ``auth_kind == "scoped_token"`` identities may write;
      - the connection row's ``writeEnabled`` flag must be true;
      - tags-scoped tokens (``scope.type == "tags"``) are rejected — only
        all_private-scoped, write-enabled tokens may mutate tags.
    Reads state via getattr-with-defaults: the Cognito path never sets
    ``write_enabled``/``scope``. No-op when bearer auth is disabled
    entirely (stdio / local SQLite installs).
    """
    if not bearer_auth_enabled():
        return None
    from .api_envelope import ErrorCode, error_response

    if getattr(request.state, "auth_kind", None) != "scoped_token":
        return error_response(
            ErrorCode.UNAUTHORIZED, "A bm_v1 scoped token is required to edit tags", status=401
        )
    if not getattr(request.state, "write_enabled", False):
        return error_response(ErrorCode.FORBIDDEN, "This token is not write-enabled", status=403)
    scope = getattr(request.state, "scope", None)
    if isinstance(scope, dict) and scope.get("type") == "tags":
        return error_response(
            ErrorCode.FORBIDDEN, "Tags-scoped tokens may not edit tags", status=403
        )
    return None


# ── Cognito JWT verification ────────────────────────────────────────


class _CognitoVerifier:
    """JWKS-cached Cognito JWT verifier.

    Set ``COGNITO_USER_POOL_ID`` and ``COGNITO_CLIENT_ID`` to enable. Region is
    read from ``COGNITO_REGION`` (default ``us-east-1``). When the user pool
    isn't configured, ``verify`` returns ``None`` and the bearer middleware
    falls through to the scoped-token path.
    """

    def __init__(self) -> None:
        self.user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
        self.client_id = os.environ.get("COGNITO_CLIENT_ID", "").strip()
        self.region = os.environ.get("COGNITO_REGION", "us-east-1").strip() or "us-east-1"
        self._jwk_client: PyJWKClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.user_pool_id and self.client_id)

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    def _client(self) -> PyJWKClient:
        if self._jwk_client is None:
            # PyJWKClient caches keys for 60 minutes by default.
            self._jwk_client = PyJWKClient(self.jwks_url, cache_keys=True, lifespan=600)
        return self._jwk_client

    def verify(self, token: str) -> dict[str, Any] | None:
        """Return decoded claims if valid; return ``None`` for any failure.

        Failure modes (signature, expiry, audience, issuer) are intentionally
        flattened to ``None`` so the middleware can fall through to the
        scoped-token lookup without leaking auth-mode details.
        """
        if not self.configured or not token.startswith("eyJ"):
            return None
        try:
            signing_key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub", "token_use"]},
            )
        except jwt.PyJWTError:
            return None
        # Cognito issues both ID and Access tokens; we accept ID tokens only —
        # they're what first-party browser/mobile clients send.
        if claims.get("token_use") != "id":
            return None
        return claims


# ── bm_v1_* scoped token lookup ─────────────────────────────────────


class _ScopedTokenVerifier:
    """Look up a ``bm_v1_*`` token in the mcp-connections DynamoDB table.

    An upstream provisioner mints these tokens (e.g. via a ``POST /mcp/connections``
    endpoint) and stores their SHA-256 hash on the row. Table name comes from
    ``MCP_CONNECTIONS_TABLE`` (default ``mcp-bookmarks-connections``).
    """

    def __init__(self) -> None:
        self.table_name = os.environ.get("MCP_CONNECTIONS_TABLE", "mcp-bookmarks-connections")
        self.region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self._table: Any | None = None

    def _get_table(self) -> Any:
        if self._table is None:
            import boto3

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)
        return self._table

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def verify(self, token: str) -> dict[str, Any] | None:
        if not token.startswith("bm_v1_"):
            return None
        token_hash = self._hash(token)
        loop = asyncio.get_running_loop()

        def _scan() -> dict[str, Any] | None:
            table = self._get_table()
            # Primary path: tokenHash-index GSI. O(1) lookup.
            #
            # Fallback: full filtered scan when the GSI is missing. NOTE: do NOT
            # combine Limit with FilterExpression here. DynamoDB applies Limit
            # to the raw items read *before* the filter runs, so `Limit=1 +
            # FilterExpression="tokenHash = :h"` reads exactly one item, applies
            # the filter, and returns empty unless that single read happens to
            # be the matching row. With more than a handful of rows in the
            # connections table, this path silently returns `invalid_token`
            # for every scoped bm_v1_ caller — make sure the GSI exists.
            #
            # Without Limit, the scan reads a 1 MB page and may need to
            # paginate via ExclusiveStartKey. The connections table is small
            # (per-user); a single page is enough in practice and the loop
            # below handles pagination defensively for the rare big-tenant
            # case.
            try:
                resp = table.query(
                    IndexName="tokenHash-index",
                    KeyConditionExpression="tokenHash = :h",
                    ExpressionAttributeValues={":h": token_hash},
                    Limit=1,
                )
                items = resp.get("Items", [])
            except Exception:
                items = []
                start_key: dict[str, Any] | None = None
                while True:
                    kwargs: dict[str, Any] = {
                        "FilterExpression": "tokenHash = :h",
                        "ExpressionAttributeValues": {":h": token_hash},
                    }
                    if start_key is not None:
                        kwargs["ExclusiveStartKey"] = start_key
                    resp = table.scan(**kwargs)
                    page = resp.get("Items", [])
                    if page:
                        items.extend(page)
                        break
                    start_key = resp.get("LastEvaluatedKey")
                    if start_key is None:
                        break
            if not items:
                return None
            row = items[0]
            if row.get("paused") is True or row.get("revoked") is True:
                return None
            return row

        return await loop.run_in_executor(None, _scan)


# ── Middleware ──────────────────────────────────────────────────────


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Resolve the bearer token and populate ``request.state``.

    Rules:
        - ``OPTIONS`` and public paths pass through.
        - ``/api/*`` is delegated to the existing ``TenantAuthMiddleware``.
        - ``/mcp*``, ``/sse*``, ``/messages*`` require a valid bearer.
        - All other paths (custom mounts) pass through unchanged.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._cognito = _CognitoVerifier()
        self._scoped = _ScopedTokenVerifier()

    @staticmethod
    def _enabled() -> bool:
        return bearer_auth_enabled()

    async def dispatch(self, request: Request, call_next):
        if not self._enabled():
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS:
            return await call_next(request)
        if path.startswith("/api"):
            # Admin tag-editing carve-out: these subpaths authenticate HERE
            # with a bm_v1 token (identity is required — the edit log is
            # keyed by userId). Every other /api path keeps its existing
            # TenantAuthMiddleware (static MCP_API_KEYS) delegation.
            if not bm_v1_api_route(request.method, path[len("/api") :]):
                return await call_next(request)
        elif not path.startswith(_PROTECTED_PREFIXES):
            return await call_next(request)

        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        token = auth[7:].strip()
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Cognito JWT path (first-party browser/mobile clients).
        claims = self._cognito.verify(token)
        if claims is not None:
            user_id = str(claims.get("sub") or "")
            tenant_id = os.environ.get("DYNAMODB_ORG_ID", "default")
            request.state.user_id = user_id
            request.state.tenant_id = tenant_id
            request.state.auth_kind = "cognito"
            tokens = set_request_identity(user_id, tenant_id)
            try:
                return await call_next(request)
            finally:
                reset_request_identity(tokens)

        # Scoped bm_v1_* token path (Claude, Cursor, external MCP clients).
        row = await self._scoped.verify(token)
        if row is not None:
            user_id = str(row.get("userId") or row.get("ownerId") or "")
            tenant_id = str(
                row.get("organizationId") or os.environ.get("DYNAMODB_ORG_ID", "default")
            )
            # The connection's ``scope`` map ({"type": "all_private"} |
            # {"type": "tags", "tags": [...]}) drives read filtering downstream,
            # at parity with read-mcp's buildScopeFilter. A non-dict/absent
            # value falls back to the all_private default in the data layer.
            scope = row.get("scope")
            if not isinstance(scope, dict):
                scope = None
            request.state.user_id = user_id
            request.state.tenant_id = tenant_id
            request.state.connection_id = str(row.get("connectionId") or row.get("id") or "")
            request.state.scope = scope
            request.state.write_enabled = bool(row.get("writeEnabled"))
            request.state.auth_kind = "scoped_token"
            tokens = set_request_identity(user_id, tenant_id, scope)
            try:
                return await call_next(request)
            finally:
                reset_request_identity(tokens)

        return JSONResponse({"error": "invalid_token"}, status_code=401)


# ── Helper for tools / handlers ─────────────────────────────────────


def request_user_id(ctx: Any) -> str | None:
    """Read user_id from the FastMCP ``Context``'s underlying request, if any.

    Returns ``None`` for stdio transport or when bearer auth isn't enabled.
    """
    try:
        request = ctx.request_context.request
    except AttributeError:
        return None
    if request is None:
        return None
    return getattr(request.state, "user_id", None)


__all__ = [
    "BearerAuthMiddleware",
    "bearer_auth_enabled",
    "bm_v1_api_route",
    "request_user_id",
    "tag_write_policy_error",
]
