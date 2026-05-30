"""REST integration tests for the routes in ``mcp_bookmarks.api`` (WDN-396 / OSS-6).

Drives the Starlette app in-process via httpx + ASGITransport (the same
pattern as ``tests/unit/test_correlation.py``) so we exercise the real
middleware, request parsing, and error-envelope wiring without a network
hop. Each test gets a fresh SQLite file under ``tmp_path`` and disables
the tenant-auth middleware by clearing ``MCP_API_KEYS``.

Routes covered (1 happy path + 2-3 error cases each):

  - GET  /api/bookmarks/{id}
  - POST /api/tag
  - POST /api/bookmarks/{id}/tags
  - POST /api/bookmarks/{id}/summary
  - GET  /api/usage           (incl. UnsupportedCapability 403 envelope)
  - GET  /api/capabilities    (SQLite drift-guard)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

# ── Shared helper ─────────────────────────────────────────────────────


@asynccontextmanager
async def _app_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Yield ``(client, db_path)`` with a fresh SQLite DB and auth disabled.

    ``BOOKMARKS_DB_PATH`` must be set BEFORE ``create_api_app()`` because
    the app's ``_db`` helper reads the env var on each request (api.py:69)
    and the middleware-mounting branch (api.py:1051) reads
    ``api_keys_configured()`` at app-build time.
    """
    db_path = tmp_path / "bookmarks.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))
    monkeypatch.setenv("MCP_API_KEYS", "")  # disables TenantAuthMiddleware
    monkeypatch.delenv("MCP_MONTHLY_USAGE_LIMIT", raising=False)  # quota off
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)  # force SQLite branch

    from mcp_bookmarks.api import create_api_app

    app = create_api_app()
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c, db_path


async def _seed_bookmark(db_path: Path, **kwargs) -> int:
    """Open a fresh ``Database``, insert one bookmark, close. Returns its id.

    We deliberately do NOT hold the connection open across the HTTP call —
    the api opens its own connection via ``_db`` and SQLite handles
    concurrent file access fine across separate connect/close pairs.
    """
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        bm = await db.upsert_bookmark(**kwargs)
        return bm.id
    finally:
        await db.close()


async def _seed_tag(db_path: Path, slug: str, name: str, description: str = "") -> None:
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        await db.create_tag(slug, name, description)
    finally:
        await db.close()


# ── GET /api/bookmarks/{id} ───────────────────────────────────────────


async def test_get_bookmark_returns_full_dict(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(
            db_path,
            url="https://example.com/a",
            title="Example A",
            description="A description",
        )
        r = await client.get(f"/bookmarks/{bid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == bid
    assert body["url"] == "https://example.com/a"
    assert body["title"] == "Example A"
    # No content was set, so no truncation marker should be present.
    assert body.get("content_truncated") is not True


async def test_get_bookmark_404_on_missing(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _db_path):
        r = await client.get("/bookmarks/9999")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found"
    assert "9999" in err["message"]


async def test_get_bookmark_truncates_long_content(monkeypatch, tmp_path):
    """Content over ``_MAX_BOOKMARK_CONTENT_JSON`` (400_000) is truncated in the response."""
    from mcp_bookmarks.db import Database

    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        # ``upsert_bookmark`` doesn't accept ``content`` directly, so write
        # the long body via raw SQL after the upsert.
        bid = await _seed_bookmark(db_path, url="https://example.com/big", title="Big article")
        long_body = "x" * 500_000
        db = Database(db_path)
        await db.connect()
        try:
            await db.db.execute("UPDATE bookmarks SET content = ? WHERE id = ?", (long_body, bid))
            await db.db.commit()
        finally:
            await db.close()

        r = await client.get(f"/bookmarks/{bid}")
    assert r.status_code == 200
    body = r.json()
    assert body["content_truncated"] is True
    # 400_000 chars + the "[truncated]" suffix line.
    assert len(body["content"]) < 500_000
    assert body["content"].endswith("[truncated for JSON response]")


# ── POST /api/tag ─────────────────────────────────────────────────────


async def test_create_tag_returns_201_with_payload(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post(
            "/tag",
            json={"slug": "python", "name": "Python", "description": "lang"},
        )
    assert r.status_code == 201
    created = r.json()["created"]
    assert created == {"slug": "python", "name": "Python", "description": "lang"}


async def test_create_tag_409_on_duplicate_slug(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        await _seed_tag(db_path, "python", "Python", "lang")
        r = await client.post(
            "/tag", json={"slug": "python", "name": "Python", "description": "lang"}
        )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "conflict"
    assert err["details"] == {"slug": "python"}


async def test_create_tag_422_on_empty_slug(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post("/tag", json={"slug": "", "name": "X", "description": ""})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    # Pydantic flattens the offending field path into details.fields.
    locs = [f["loc"] for f in err["details"]["fields"]]
    assert "slug" in locs


# ── POST /api/bookmarks/{id}/tags ─────────────────────────────────────


async def test_assign_tags_returns_assigned_slugs(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, url="https://example.com/x", title="X")
        await _seed_tag(db_path, "python", "Python")
        await _seed_tag(db_path, "web", "Web")
        r = await client.post(f"/bookmarks/{bid}/tags", json={"tag_slugs": ["python", "web"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # URL path param echo: api returns the string form even when the db id is int.
    assert str(body["bookmark_id"]) == str(bid)
    assert body["tag_slugs"] == ["python", "web"]


async def test_assign_tags_404_on_missing_bookmark(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        await _seed_tag(db_path, "python", "Python")
        r = await client.post("/bookmarks/9999/tags", json={"tag_slugs": ["python"]})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_assign_tags_400_on_unknown_tag_slug(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, url="https://example.com/y", title="Y")
        r = await client.post(f"/bookmarks/{bid}/tags", json={"tag_slugs": ["nonexistent-tag"]})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_request"
    assert "nonexistent-tag" in err["message"]


# ── POST /api/bookmarks/{id}/summary ──────────────────────────────────


async def test_set_summary_returns_ok(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, url="https://example.com/s", title="S")
        r = await client.post(
            f"/bookmarks/{bid}/summary",
            json={"summary": "An AI-generated summary."},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert str(body["bookmark_id"]) == str(bid)

    # Verify the summary was actually persisted.
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        bm = await db.get_bookmark_by_id(bid)
        assert bm is not None
        assert bm.summary == "An AI-generated summary."
    finally:
        await db.close()


async def test_set_summary_404_on_missing_bookmark(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post("/bookmarks/9999/summary", json={"summary": "hi"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_set_summary_422_on_empty_summary(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, url="https://example.com/e", title="E")
        r = await client.post(f"/bookmarks/{bid}/summary", json={"summary": ""})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    locs = [f["loc"] for f in err["details"]["fields"]]
    assert "summary" in locs


# ── GET /api/usage ────────────────────────────────────────────────────


async def test_usage_returns_monthly_limit_and_enforced_flag(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.get("/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "default"
    assert body["events_this_month"] == 0  # fresh DB
    assert body["monthly_limit"] == 0  # env var not set
    assert body["limit_enforced"] is False
    assert "limit_enforced" in body  # explicit drift-guard


async def test_usage_returns_403_unsupported_envelope_when_backend_lacks_metering(
    monkeypatch, tmp_path
):
    """If ``count_events_this_month`` raises ``UnsupportedCapability``,
    the handler must return a 403 with the ``unsupported``-shaped envelope
    (api.py:388-394). We force the path by patching the service.
    """
    from mcp_bookmarks import api as api_module
    from mcp_bookmarks.backend import UnsupportedCapability

    async def _raise(*, db, tenant_id):
        raise UnsupportedCapability(
            capability="usage_metering",
            backend="dynamodb",
            method="count_usage_events_month",
        )

    monkeypatch.setattr(api_module.usage_service, "count_events_this_month", _raise)

    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.get("/usage")
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "forbidden"
    # ``to_envelope()`` puts backend + capability in details for client branching.
    assert err["details"]["backend"] == "dynamodb"
    assert err["details"]["capability"] == "usage_metering"


# ── GET /api/capabilities ─────────────────────────────────────────────


async def test_capabilities_reports_sqlite_payload(monkeypatch, tmp_path):
    """Drift-guard: the SQLite backend must report its declared capability flags.

    If somebody flips a flag in ``backend.SQLITE_CAPABILITIES`` without
    updating client expectations, this test fails. We assert the FULL
    shape so a renamed/added flag is caught too.
    """
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.get("/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "sqlite"
    assert body["capabilities"] == {
        "semantic_search": True,
        "paged_search": False,
        "integer_bookmark_ids": True,
        "usage_metering": True,
        "subscription_storage": True,
    }
