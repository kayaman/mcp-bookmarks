"""Unit tests for the CloudFront origin shared-secret gate."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_bookmarks.origin_secret import OriginSecretMiddleware


def _client() -> TestClient:
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[Route("/mcp", ok), Route("/health", ok)],
        middleware=[Middleware(OriginSecretMiddleware)],
    )
    return TestClient(app)


def test_noop_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGIN_SHARED_SECRET", raising=False)
    assert _client().get("/mcp").status_code == 200


def test_rejects_without_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", "s3cr3t")
    assert _client().get("/mcp").status_code == 403


def test_rejects_wrong_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", "s3cr3t")
    assert _client().get("/mcp", headers={"x-origin-secret": "nope"}).status_code == 403


def test_allows_correct_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", "s3cr3t")
    r = _client().get("/mcp", headers={"x-origin-secret": "s3cr3t"})
    assert r.status_code == 200


def test_health_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", "s3cr3t")
    assert _client().get("/health").status_code == 200


def test_options_preflight_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", "s3cr3t")
    # Passes the gate (not 403); the bare route has no OPTIONS handler → 405.
    assert _client().options("/mcp").status_code != 403
