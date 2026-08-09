"""REST integration tests for the admin tag-editing endpoints (Phase 1).

Same in-process httpx + ASGITransport pattern as test_api_rest.py; runs on
SQLite (that is WHY Task 2 keeps SQLite parity). Bearer auth is off for the
functional tests (policy no-op) and simulated via a state-injecting
middleware for the policy tests — BearerAuthMiddleware itself lives on the
outer app and is covered in tests/unit/test_bearer_auth.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from starlette.middleware.base import BaseHTTPMiddleware


@asynccontextmanager
async def _app_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, auth_state=None):
    db_path = tmp_path / "bookmarks.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))
    monkeypatch.setenv("MCP_API_KEYS", "")
    monkeypatch.delenv("MCP_MONTHLY_USAGE_LIMIT", raising=False)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)
    if auth_state is None:
        monkeypatch.delenv("MCP_BEARER_AUTH", raising=False)  # policy no-op

    from mcp_bookmarks.api import create_api_app

    app = create_api_app()
    if auth_state is not None:

        class _FakeAuth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                for k, v in auth_state.items():
                    setattr(request.state, k, v)
                return await call_next(request)

        app.add_middleware(_FakeAuth)
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c, db_path


async def _seed_bookmark(db_path: Path, url: str, tags: tuple[str, ...] = ()) -> int:
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        bm = await db.upsert_bookmark(url=url, title="T")
        for slug in tags:
            await db.create_tag(slug, slug)
        if tags:
            await db.tag_bookmark(bm.id, list(tags))
        return bm.id
    finally:
        await db.close()


async def _tags_of(db_path: Path, bid: int) -> list[str]:
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        bm = await db.get_bookmark_by_id(bid)
        return bm.tags
    finally:
        await db.close()


# ── PUT /api/bookmarks/{id}/tags ──────────────────────────────────────


async def test_put_replaces_tags_and_returns_pinned_contract(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/a", ("python", "web"))
        r = await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["python", "rust-lang"]})
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "bookmark_id": str(bid),
        "before": ["python", "web"],
        "after": ["python", "rust-lang"],
        "added": ["rust-lang"],
        "removed": ["web"],
    }


async def test_put_normalizes_input(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/n")
        r = await client.put(
            f"/bookmarks/{bid}/tags", json={"tags": ["#Machine Learning", "  python "]}
        )
    assert r.status_code == 200
    assert r.json()["after"] == ["machine-learning", "python"]


async def test_put_invalid_slug_400_nothing_written(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/b", ("python",))
        r = await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["bad_tag!"]})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_request"
        assert await _tags_of(db_path, bid) == ["python"]  # untouched


async def test_put_more_than_10_tags_400(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/c")
        r = await client.put(
            f"/bookmarks/{bid}/tags", json={"tags": [f"tag-{i}" for i in range(11)]}
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


async def test_put_404_on_missing_bookmark(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.put("/bookmarks/9999/tags", json={"tags": ["python"]})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# ── Write policy (bearer auth simulated) ──────────────────────────────


async def test_put_401_without_scoped_token_when_bearer_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    async with _app_client(monkeypatch, tmp_path, auth_state={}) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/p")
        r = await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["python"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_put_403_for_tags_scoped_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    state = {
        "auth_kind": "scoped_token",
        "write_enabled": True,
        "scope": {"type": "tags", "tags": ["python"]},
        "user_id": "u-1",
    }
    async with _app_client(monkeypatch, tmp_path, auth_state=state) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/q")
        r = await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["python"]})
    assert r.status_code == 403


async def test_put_allows_write_enabled_all_private_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BEARER_AUTH", "true")
    state = {
        "auth_kind": "scoped_token",
        "write_enabled": True,
        "scope": {"type": "all_private"},
        "user_id": "u-1",
    }
    async with _app_client(monkeypatch, tmp_path, auth_state=state) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/r")
        r = await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["python"]})
    assert r.status_code == 200


# ── GET /api/bookmarks/recent ─────────────────────────────────────────


async def test_recent_returns_contract_shape(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/rec", ("python",))
        await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["web"]})
        r = await client.get("/bookmarks/recent")
    assert r.status_code == 200
    row = r.json()["bookmarks"][0]
    assert set(row) == {"id", "url", "title", "aiTags", "aiTagsOriginal", "tagsReviewedAt"}
    assert row["aiTags"] == ["web"]
    assert row["aiTagsOriginal"] == ["python"]
    assert row["tagsReviewedAt"]


async def test_recent_not_swallowed_by_id_route(monkeypatch, tmp_path):
    """Route-ordering guard: /bookmarks/recent must NOT hit /bookmarks/{id}."""
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        r = await client.get("/bookmarks/recent")
    assert r.status_code == 200
    assert r.json() == {"bookmarks": []}


async def test_recent_limit_clamped(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, _):
        assert (await client.get("/bookmarks/recent?limit=99999")).status_code == 200
        r = await client.get("/bookmarks/recent?limit=abc")
    assert r.status_code == 400


# ── GET /api/tag-edits ────────────────────────────────────────────────


async def test_tag_edits_newest_first(monkeypatch, tmp_path):
    async with _app_client(monkeypatch, tmp_path) as (client, db_path):
        bid = await _seed_bookmark(db_path, "https://example.com/e", ("python",))
        await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["web"]})
        await client.put(f"/bookmarks/{bid}/tags", json={"tags": ["rust-lang"]})
        r = await client.get("/tag-edits")
    assert r.status_code == 200
    edits = r.json()["edits"]
    assert len(edits) == 2
    assert edits[0]["after"] == ["rust-lang"]
    assert set(edits[0]) == {"bookmarkId", "before", "after", "added", "removed", "actor", "ts"}
    assert edits[0]["actor"] == "human"
