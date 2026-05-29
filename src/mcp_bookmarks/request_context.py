"""Per-request identity propagation via :mod:`contextvars`.

The combined Starlette app authenticates each request in
:class:`mcp_bookmarks.bearer_auth.BearerAuthMiddleware` and stores the resolved
identity on ``request.state``. MCP tools, however, don't have direct access to
the Starlette ``Request`` — they call ``DynamoDBDatabase`` methods that need to
filter rows by ``userId`` to keep multi-user traffic isolated.

This module bridges that gap with a single :class:`contextvars.ContextVar`. The
middleware sets ``current_user_id`` after a successful auth; the database layer
reads it via :func:`current_user_id`. ContextVars are async-task-local and
isolate concurrent requests automatically.

When the contextvar is unset (stdio transport, single-tenant deploys without
``MCP_BEARER_AUTH=true``), the database layer falls back to the
``DYNAMODB_USER_ID`` env var, preserving existing behaviour.
"""

from __future__ import annotations

import contextvars
from typing import Any

# Default ``None`` so callers can distinguish "no auth context" from
# "authenticated as user '' (empty string)". Keep the value type permissive
# (str | None) so future call sites can stash something richer if needed.
_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_bookmarks_user_id", default=None
)
_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_bookmarks_tenant_id", default=None
)


def current_user_id() -> str | None:
    """Return the user_id resolved by the current request's auth, or ``None``."""
    return _current_user_id.get()


def current_tenant_id() -> str | None:
    """Return the tenant/organization_id resolved by the current request, or ``None``."""
    return _current_tenant_id.get()


def set_request_identity(user_id: str | None, tenant_id: str | None) -> tuple[Any, Any]:
    """Set the per-request identity. Returns reset tokens for both vars.

    Callers MUST pass the returned tokens to :func:`reset_request_identity` in
    a finally block (or use the :func:`request_identity` context manager) so
    leaks across requests can't happen even if a handler raises.
    """
    return _current_user_id.set(user_id), _current_tenant_id.set(tenant_id)


def reset_request_identity(tokens: tuple[Any, Any]) -> None:
    user_token, tenant_token = tokens
    _current_user_id.reset(user_token)
    _current_tenant_id.reset(tenant_token)


__all__ = [
    "current_user_id",
    "current_tenant_id",
    "set_request_identity",
    "reset_request_identity",
]
