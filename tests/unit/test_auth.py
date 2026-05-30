"""Auth — API key → tenant resolution via ``MCP_API_KEYS`` env var.

Exercises the env-parsing branches and the request-header extraction.
All env mutations are scoped through ``monkeypatch`` so tests can't leak.
"""

from __future__ import annotations

import pytest

from mcp_bookmarks import auth

# ── _parse_api_key_map / api_keys_configured ───────────────────────


def test_missing_env_returns_empty_map(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_API_KEYS", raising=False)
    assert auth._parse_api_key_map() == {}
    assert auth.api_keys_configured() is False


def test_keys_without_colon_use_default_org(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "secret1,secret2")
    monkeypatch.delenv("DYNAMODB_ORG_ID", raising=False)
    mapping = auth._parse_api_key_map()
    assert mapping == {"secret1": "default", "secret2": "default"}
    assert auth.api_keys_configured() is True


def test_keys_with_colon_extract_org(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "pk_a:org-1, pk_b:org-2")
    mapping = auth._parse_api_key_map()
    assert mapping == {"pk_a": "org-1", "pk_b": "org-2"}


def test_malformed_pair_empty_org_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    """``key:`` (trailing colon, empty org) should resolve via DYNAMODB_ORG_ID/default."""
    monkeypatch.setenv("MCP_API_KEYS", "pk_x:")
    monkeypatch.setenv("DYNAMODB_ORG_ID", "fallback-org")
    mapping = auth._parse_api_key_map()
    assert mapping == {"pk_x": "fallback-org"}


def test_blank_segments_are_skipped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "pk_a:org-1,,  ,pk_b:org-2")
    assert auth._parse_api_key_map() == {"pk_a": "org-1", "pk_b": "org-2"}


# ── resolve_tenant_for_key ─────────────────────────────────────────


def test_resolve_tenant_unknown_key_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "pk_known:org-1")
    assert auth.resolve_tenant_for_key("pk_other") is None


def test_resolve_tenant_none_input_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "pk_known:org-1")
    assert auth.resolve_tenant_for_key(None) is None


# ── extract_api_key_from_request ───────────────────────────────────


def test_extract_api_key_from_bearer_header():
    headers = {"authorization": "Bearer my-secret"}
    assert auth.extract_api_key_from_request(headers) == "my-secret"


def test_extract_api_key_from_x_api_key_header():
    headers = {"x-api-key": "xk-secret"}
    assert auth.extract_api_key_from_request(headers) == "xk-secret"


def test_extract_api_key_returns_none_when_missing():
    assert auth.extract_api_key_from_request({}) is None


# ── require_api_key ────────────────────────────────────────────────


def test_require_api_key_open_when_not_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_API_KEYS", raising=False)
    monkeypatch.setenv("DYNAMODB_ORG_ID", "open-tenant")
    ok, tenant = auth.require_api_key({})
    assert ok is True
    assert tenant == "open-tenant"


def test_require_api_key_rejects_unknown(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "pk_known:org-1")
    ok, tenant = auth.require_api_key({"authorization": "Bearer unknown"})
    assert ok is False
    assert tenant is None


def test_require_api_key_accepts_known(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_API_KEYS", "pk_known:org-1")
    ok, tenant = auth.require_api_key({"x-api-key": "pk_known"})
    assert ok is True
    assert tenant == "org-1"
