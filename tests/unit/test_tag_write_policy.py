"""tag_write_policy_error — write gate for the admin tag-edit routes (Phase 1).

Handlers read auth state via getattr-with-defaults because the Cognito path
never sets write_enabled/scope. When MCP_BEARER_AUTH is off entirely (stdio /
local SQLite installs) the policy is a no-op, matching the rest of the auth
surface.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_bookmarks.bearer_auth import tag_write_policy_error


def _req(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


def test_noop_when_bearer_auth_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_BEARER_AUTH", raising=False)
    assert tag_write_policy_error(_req()) is None


def test_rejects_missing_identity(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    resp = tag_write_policy_error(_req())
    assert resp is not None and resp.status_code == 401


def test_rejects_cognito_identity(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    resp = tag_write_policy_error(_req(auth_kind="cognito", user_id="u"))
    assert resp is not None and resp.status_code == 401


def test_rejects_read_only_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    resp = tag_write_policy_error(_req(auth_kind="scoped_token", write_enabled=False))
    assert resp is not None and resp.status_code == 403


def test_rejects_tags_scoped_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    resp = tag_write_policy_error(
        _req(auth_kind="scoped_token", write_enabled=True, scope={"type": "tags", "tags": ["a"]})
    )
    assert resp is not None and resp.status_code == 403


def test_allows_write_enabled_all_private_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    assert (
        tag_write_policy_error(
            _req(auth_kind="scoped_token", write_enabled=True, scope={"type": "all_private"})
        )
        is None
    )


def test_allows_absent_scope_as_all_private(monkeypatch: pytest.MonkeyPatch):
    """scope=None falls back to the all_private default in the data layer."""
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    assert tag_write_policy_error(_req(auth_kind="scoped_token", write_enabled=True)) is None
