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


# ── POST /api/save (round 3) ──────────────────────────────────────────


async def test_save_json_body_persists_bookmark(monkeypatch, tmp_path):
    """JSON body: scraper monkey-patched so no network; verify the row lands."""
    monkeypatch.setattr(
        "mcp_bookmarks.services.bookmark.extract_og_metadata",
        _fake_og_factory(title="Saved", description="d"),
    )
    monkeypatch.setattr(
        "mcp_bookmarks.services.bookmark.extract_article_content",
        _fake_article_factory(text="hello world", word_count=2),
    )
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        r = await client.post("/save", json={"url": "https://example.com/j"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "saved"
    assert body["title"] == "Saved"
    assert body["word_count"] == 2
    # And the bookmark is in the DB
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        bm = await db.get_bookmark_by_id(body["bookmark_id"])
    finally:
        await db.close()
    assert bm is not None
    assert bm.url == "https://example.com/j"


async def test_save_form_encoded_body_persists(monkeypatch, tmp_path):
    """Form-encoded bookmarklet POST: api.py:143-153 form/query branch."""
    monkeypatch.setattr(
        "mcp_bookmarks.services.bookmark.extract_og_metadata",
        _fake_og_factory(title="Form", description=""),
    )
    monkeypatch.setattr(
        "mcp_bookmarks.services.bookmark.extract_article_content",
        _fake_article_factory(text="", word_count=0),
    )
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post(
            "/save",
            content="url=https%3A%2F%2Fexample.com%2Ff",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "saved"


async def test_save_form_query_param_fallback(monkeypatch, tmp_path):
    """Form-encoded route with `url` in query string instead of body."""
    monkeypatch.setattr(
        "mcp_bookmarks.services.bookmark.extract_og_metadata",
        _fake_og_factory(title="Q", description=""),
    )
    monkeypatch.setattr(
        "mcp_bookmarks.services.bookmark.extract_article_content",
        _fake_article_factory(text="", word_count=0),
    )
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post(
            "/save?url=https://example.com/q",
            content="",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "saved"


async def test_save_missing_url_returns_400(monkeypatch, tmp_path):
    """Form path with no URL anywhere — api.py:147-153 error envelope."""
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post(
            "/save",
            content="",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_request"
    assert any(f["loc"] == "url" for f in err["details"]["fields"])


async def test_save_blocked_by_quota_returns_429(monkeypatch, tmp_path):
    """Quota path: monkeypatch quota_service.check to return blocked."""
    from starlette.responses import JSONResponse

    async def _blocked(*, db_path, tenant_id, surface):
        return False, 100, 100  # blocked

    monkeypatch.setattr("mcp_bookmarks.api.quota_service.check", _blocked)
    monkeypatch.setattr(
        "mcp_bookmarks.api.quota_service.rest_block_response",
        lambda used, limit: JSONResponse(
            {"error": {"code": "rate_limited", "details": {"used": used, "limit": limit}}},
            status_code=429,
        ),
    )

    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post("/save", json={"url": "https://example.com/q"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


# ── GET /api/bookmarks (list/search) ──────────────────────────────────


async def test_list_bookmarks_returns_empty_list(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.get("/bookmarks")
    assert r.status_code == 200
    body = r.json()
    assert body == {"total": 0, "bookmarks": []}


async def test_list_bookmarks_returns_seeded_rows(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        await _seed_bookmark(db_path, url="https://example.com/1", title="One")
        await _seed_bookmark(db_path, url="https://example.com/2", title="Two")
        r = await client.get("/bookmarks")
    body = r.json()
    assert body["total"] == 2
    urls = {b["url"] for b in body["bookmarks"]}
    assert urls == {"https://example.com/1", "https://example.com/2"}


async def test_list_bookmarks_filters_by_tag(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        await _seed_tag(db_path, "py", "Python")
        bid1 = await _seed_bookmark(db_path, url="https://example.com/a", title="A")
        await _seed_bookmark(db_path, url="https://example.com/b", title="B")
        # Tag only bid1
        from mcp_bookmarks.db import Database

        db = Database(db_path)
        await db.connect()
        try:
            await db.tag_bookmark(bid1, ["py"])
        finally:
            await db.close()

        r = await client.get("/bookmarks?tag=py")
    body = r.json()
    assert body["total"] == 1
    assert body["bookmarks"][0]["url"] == "https://example.com/a"


async def test_list_bookmarks_respects_limit_query_param(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        for i in range(5):
            await _seed_bookmark(db_path, url=f"https://example.com/{i}", title=f"B{i}")
        r = await client.get("/bookmarks?limit=2")
    body = r.json()
    assert body["total"] == 2


# ── GET /api/tags ─────────────────────────────────────────────────────


async def test_list_tags_empty(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.get("/tags")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "tags": []}


async def test_list_tags_returns_full_shape(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        await _seed_tag(db_path, "python", "Python", "lang")
        await _seed_tag(db_path, "web", "Web", "")
        r = await client.get("/tags")
    body = r.json()
    assert body["total"] == 2
    slugs = {t["slug"] for t in body["tags"]}
    assert slugs == {"python", "web"}
    py = next(t for t in body["tags"] if t["slug"] == "python")
    assert py["name"] == "Python"
    assert py["description"] == "lang"
    assert py["usage_count"] == 0  # nothing tagged yet


# ── GET /api/stats ────────────────────────────────────────────────────


async def test_stats_returns_counts(monkeypatch, tmp_path):
    """Covers api.py:181-186 (small guard cluster)."""
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        await _seed_bookmark(db_path, url="https://example.com/s", title="S")
        await _seed_tag(db_path, "t1", "T1")
        r = await client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_bookmarks"] == 1
    assert body["total_tags"] == 1


# ── POST /api/ensemble ────────────────────────────────────────────────


async def test_ensemble_disabled_returns_403(monkeypatch, tmp_path):
    monkeypatch.setattr("mcp_bookmarks.llm_ensemble.ensemble_enabled", lambda: False)
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post("/ensemble", json={"task": "what's 2+2?"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_ensemble_happy_path_returns_payload(monkeypatch, tmp_path):
    monkeypatch.setattr("mcp_bookmarks.llm_ensemble.ensemble_enabled", lambda: True)

    async def _fake_run(task, *, models=None, judge_model=None):
        return {"ok": True, "answer": "4", "candidates": [{"model": "m1", "text": "4"}]}

    monkeypatch.setattr("mcp_bookmarks.llm_ensemble.run_ensemble_with_judge", _fake_run)
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        # api_ensemble doesn't open Database itself, so bootstrap the schema
        # so _record_rest_usage's usage_events write doesn't 1-error.
        from mcp_bookmarks.db import Database

        db = Database(db_path)
        await db.connect()
        await db.close()
        r = await client.post(
            "/ensemble",
            json={"task": "what's 2+2?", "models": ["m1"], "judge_model": "j1"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["answer"] == "4"


async def test_ensemble_422_on_empty_task(monkeypatch, tmp_path):
    monkeypatch.setattr("mcp_bookmarks.llm_ensemble.ensemble_enabled", lambda: True)
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.post("/ensemble", json={"task": ""})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"


# ── TenantAuthMiddleware path (api.py:1052) ───────────────────────────


async def test_tenant_auth_middleware_rejects_missing_key(monkeypatch, tmp_path):
    """When MCP_API_KEYS is set, the middleware is mounted (api.py:1052).

    A request without a key gets the standard 401 envelope.
    """
    db_path = tmp_path / "x.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))
    monkeypatch.setenv("MCP_API_KEYS", "secret-key:tenant-a")
    monkeypatch.delenv("MCP_MONTHLY_USAGE_LIMIT", raising=False)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)

    from mcp_bookmarks.api import create_api_app

    app = create_api_app()
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            r = await client.get("/stats")  # no x-api-key header
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_tenant_auth_middleware_accepts_valid_key(monkeypatch, tmp_path):
    db_path = tmp_path / "x.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))
    monkeypatch.setenv("MCP_API_KEYS", "secret-key:tenant-a")
    monkeypatch.delenv("MCP_MONTHLY_USAGE_LIMIT", raising=False)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)

    from mcp_bookmarks.api import create_api_app

    app = create_api_app()
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            r = await client.get("/stats", headers={"x-api-key": "secret-key"})
    assert r.status_code == 200


# ── POST /webhooks/stripe ─────────────────────────────────────────────
#
# stripe_webhook is mounted on the OUTER app (server.py:1273), not via
# create_api_app, so we build a minimal Starlette app here.


def _stripe_app():
    from starlette.applications import Starlette
    from starlette.routing import Route

    from mcp_bookmarks.api import stripe_webhook

    return Starlette(routes=[Route("/webhooks/stripe", stripe_webhook, methods=["POST"])])


@asynccontextmanager
async def _stripe_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "subs.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))
    app = _stripe_app()
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_stripe_webhook_503_when_secret_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    async with _stripe_client(monkeypatch, tmp_path) as client:
        r = await client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "service_unavailable"


async def test_stripe_webhook_400_on_bad_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(
        "mcp_bookmarks.services.billing.verify_stripe_signature",
        lambda body, sig, secret: False,
    )
    async with _stripe_client(monkeypatch, tmp_path) as client:
        r = await client.post(
            "/webhooks/stripe",
            content=b'{"id":"evt_1"}',
            headers={"stripe-signature": "t=1,v1=bad"},
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_signature"


async def test_stripe_webhook_400_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(
        "mcp_bookmarks.services.billing.verify_stripe_signature",
        lambda body, sig, secret: True,
    )
    async with _stripe_client(monkeypatch, tmp_path) as client:
        r = await client.post(
            "/webhooks/stripe",
            content=b"not json",
            headers={"stripe-signature": "t=1,v1=ok"},
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_json"


async def test_stripe_webhook_200_ignored_for_unhandled_event(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(
        "mcp_bookmarks.services.billing.verify_stripe_signature",
        lambda body, sig, secret: True,
    )
    async with _stripe_client(monkeypatch, tmp_path) as client:
        r = await client.post(
            "/webhooks/stripe",
            content=b'{"type":"some.other.event","data":{"object":{}}}',
            headers={"stripe-signature": "t=1,v1=ok"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    # `outcome.processed` is False here → handler returns the ignored path
    assert "ignored" in body or body.get("type") is None


async def test_stripe_webhook_200_processed_for_subscription_created(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(
        "mcp_bookmarks.services.billing.verify_stripe_signature",
        lambda body, sig, secret: True,
    )

    upserts = []

    async def _capture(cid, status, plan_sku, cpe, raw):
        upserts.append((cid, status, plan_sku, cpe))

    monkeypatch.setattr("mcp_bookmarks.services.billing.upsert_from_stripe_event", _capture)

    payload = (
        b'{"type":"customer.subscription.created",'
        b'"data":{"object":{"customer":"cus_123","status":"active",'
        b'"items":{"data":[{"price":{"id":"price_x"}}]},'
        b'"current_period_end":9999}}}'
    )
    async with _stripe_client(monkeypatch, tmp_path) as client:
        r = await client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": "t=1,v1=ok"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["type"] == "customer.subscription.created"
    assert upserts == [("cus_123", "active", "price_x", "9999")]


# ── Scraper-mock helpers (used by the api_save tests above) ──────────


def _fake_og_factory(*, title: str | None, description: str = ""):
    async def _fake(url: str):
        from mcp_bookmarks.models import OGMetadata

        return OGMetadata(url=url, title=title, description=description)

    return _fake


def _fake_article_factory(*, text: str, word_count: int):
    async def _fake(url: str):
        from mcp_bookmarks.models import ArticleContent

        return ArticleContent(url=url, text=text, word_count=word_count)

    return _fake
