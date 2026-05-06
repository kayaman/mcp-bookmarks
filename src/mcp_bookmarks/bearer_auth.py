"""Bearer-token auth middleware for the combined MCP + REST app.

Resolves the Authorization header using two strategies, in order:

1. **Cognito JWT** (issued by AWS Cognito User Pool, used by the Blogmarks PWA).
   Verified against the user pool's JWKS; the resulting subject becomes the
   per-request ``user_id``.

2. **Scoped bearer token** (``bm_v1_*``, minted by the read-mcp Lambda's
   ``/mcp/connections`` endpoint and used by external MCP clients like Claude
   and Cursor). Looked up by SHA-256 hash in the ``mcp-connections`` DynamoDB
   table that the Lambda owns.

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
import time
from typing import Any

import httpx
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
        # they are what the PWA calls authenticatedJson with.
        if claims.get("token_use") != "id":
            return None
        return claims


# ── bm_v1_* scoped token lookup ─────────────────────────────────────


class _ScopedTokenVerifier:
    """Look up a ``bm_v1_*`` token in the mcp-connections DynamoDB table.

    The read-mcp Lambda mints these tokens via ``POST /mcp/connections`` and
    stores their SHA-256 hash on the row. Table name comes from
    ``MCP_CONNECTIONS_TABLE`` (default ``blogmarks-mcp-connections``).
    """

    def __init__(self) -> None:
        self.table_name = os.environ.get("MCP_CONNECTIONS_TABLE", "blogmarks-mcp-connections")
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
            # The Lambda stores tokens with `tokenHash` as a GSI; fall back to a
            # filtered scan if that GSI isn't reachable. Most lookups should
            # find a row in O(1) via the GSI.
            try:
                resp = table.query(
                    IndexName="tokenHash-index",
                    KeyConditionExpression="tokenHash = :h",
                    ExpressionAttributeValues={":h": token_hash},
                    Limit=1,
                )
                items = resp.get("Items", [])
            except Exception:
                resp = table.scan(
                    FilterExpression="tokenHash = :h",
                    ExpressionAttributeValues={":h": token_hash},
                    Limit=1,
                )
                items = resp.get("Items", [])
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
        return os.environ.get("MCP_BEARER_AUTH", "").strip().lower() in ("1", "true", "yes")

    async def dispatch(self, request: Request, call_next):
        if not self._enabled():
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/api"):
            return await call_next(request)
        if not path.startswith(_PROTECTED_PREFIXES):
            return await call_next(request)

        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        token = auth[7:].strip()
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # Cognito JWT path (PWA users).
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
            tenant_id = str(row.get("organizationId") or os.environ.get("DYNAMODB_ORG_ID", "default"))
            request.state.user_id = user_id
            request.state.tenant_id = tenant_id
            request.state.connection_id = str(row.get("id") or "")
            request.state.auth_kind = "scoped_token"
            tokens = set_request_identity(user_id, tenant_id)
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
        request = ctx.request_context.request  # type: ignore[attr-defined]
    except AttributeError:
        return None
    if request is None:
        return None
    return getattr(request.state, "user_id", None)


__all__ = [
    "BearerAuthMiddleware",
    "request_user_id",
]
