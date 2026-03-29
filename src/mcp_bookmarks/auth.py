"""API key → tenant resolution for REST (and optional future MCP proxy).

Set MCP_API_KEYS to a comma-separated list of secrets. Optional per-key org:

  MCP_API_KEYS=pk_live_abc:org-1,pk_live_def:org-2
  MCP_API_KEYS=secret1,secret2   # uses DYNAMODB_ORG_ID or tenant "default"

When MCP_API_KEYS is unset, REST routes stay open (development default).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Tenant


def _parse_api_key_map() -> dict[str, str]:
    raw = os.environ.get("MCP_API_KEYS", "").strip()
    if not raw:
        return {}
    default_org = os.environ.get("DYNAMODB_ORG_ID", "default")
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            key, org = part.split(":", 1)
            out[key.strip()] = org.strip() or default_org
        else:
            out[part] = default_org
    return out


def api_keys_configured() -> bool:
    return bool(_parse_api_key_map())


def resolve_tenant_for_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    return _parse_api_key_map().get(api_key.strip())


def extract_api_key_from_request(headers: Any) -> str | None:
    """Starlette headers: Authorization Bearer, or X-API-Key."""
    auth = headers.get("authorization") or headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    xk = headers.get("x-api-key") or headers.get("X-API-Key")
    return (xk or "").strip() or None


def require_api_key(headers: Any) -> tuple[bool, str | None]:
    """Returns (ok, tenant_id). If keys not configured, (True, None) — open API."""
    mapping = _parse_api_key_map()
    if not mapping:
        return True, os.environ.get("DYNAMODB_ORG_ID")
    key = extract_api_key_from_request(headers)
    tenant = resolve_tenant_for_key(key)
    if tenant is None:
        return False, None
    return True, tenant


def resolve_tenant(headers: Any) -> "Tenant":
    """Resolve a Tenant from request headers.

    Uses the same ``MCP_API_KEYS`` map as ``require_api_key``. Falls back to
    ``DYNAMODB_ORG_ID`` env var, then ``"default"`` for single-tenant installs.
    Returns a ``Tenant`` regardless — never raises; callers that need to reject
    bad keys should call ``require_api_key`` first.
    """
    from .models import Tenant  # local import to avoid circular dependency

    _, tid = require_api_key(headers)
    org = tid or os.environ.get("DYNAMODB_ORG_ID") or "default"
    return Tenant(organization_id=org)
