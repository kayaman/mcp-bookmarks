"""
MCP Bookmarks Server — SSE transport.

An MCP server that exposes tools for intelligent bookmark management.
The LLM reads your tag taxonomy via get_tags and makes smart decisions
about reusing existing tags vs. creating new ones.

Run:
    uv run mcp-bookmarks
    # or directly:
    uv run python -m mcp_bookmarks.server
"""

import contextlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from .backend import BookmarkBackend, UnsupportedCapability
from .db import DEFAULT_DB_PATH, Database, _coerce_sqlite_bookmark_id
from .models import Bookmark, Tag
from .scraper import extract_article_content
from .services import bookmark as bookmark_service
from .services import embedding as embedding_service
from .services import quota as quota_service
from .services import search as search_service

# ── Lifespan: DB connection lifecycle ─────────────────────────────


@dataclass
class AppContext:
    db: BookmarkBackend
    tenant_id: str = "default"


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Open DB on startup, close on shutdown.

    Set DYNAMODB_MODE=true to use DynamoDB (mcp-bookmarks-links / mcp-bookmarks-tags)
    instead of local SQLite.

    Tenant is resolved once at startup from DYNAMODB_ORG_ID (or MCP_API_KEYS
    when a single static key is configured). For multi-tenant MCP deployments,
    run one server instance per tenant and set MCP_API_KEYS=key:org-id.
    """
    import logging

    from .auth import require_api_key
    from .models import Tenant

    log = logging.getLogger("mcp_bookmarks.lifespan")

    dynamo_mode = os.environ.get("DYNAMODB_MODE", "").lower() in ("1", "true", "yes")
    db_path = Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))

    # Resolve tenant once for the lifetime of this MCP server process.
    # Use a synthetic header dict so require_api_key can parse MCP_API_KEYS.
    mcp_key = os.environ.get("MCP_STATIC_API_KEY", "")
    _ok, tid = require_api_key({"authorization": f"Bearer {mcp_key}"} if mcp_key else {})
    tenant_id = tid or os.environ.get("DYNAMODB_ORG_ID", "default")
    tenant = Tenant(organization_id=tenant_id)

    db: BookmarkBackend
    if dynamo_mode:
        from .dynamodb import DynamoDBDatabase

        db = DynamoDBDatabase(tenant=tenant)
    else:
        db = Database(db_path, tenant_id=tenant_id)
    await db.connect()
    log.info(
        "backend_initialized",
        extra={
            "backend": "dynamodb" if dynamo_mode else "sqlite",
            "tenant_id": tenant_id,
            "db_path": str(db_path) if not dynamo_mode else None,
        },
    )
    try:
        yield AppContext(db=db, tenant_id=tenant_id)
    finally:
        log.info("backend_shutdown", extra={"backend": "dynamodb" if dynamo_mode else "sqlite"})
        await db.close()


async def _open_db_for_ready() -> BookmarkBackend:
    """Open the active backend the same way app_lifespan does — used by /ready.

    Kept thin so the readiness path bypasses MCP / FastMCP scaffolding and
    only proves the storage adapter can connect + answer a trivial query.
    """
    from .models import Tenant

    dynamo_mode = os.environ.get("DYNAMODB_MODE", "").lower() in ("1", "true", "yes")
    db_path = Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))
    tenant_id = os.environ.get("DYNAMODB_ORG_ID", "default")

    db: BookmarkBackend
    if dynamo_mode:
        from .dynamodb import DynamoDBDatabase

        db = DynamoDBDatabase(tenant=Tenant(organization_id=tenant_id))
    else:
        db = Database(db_path, tenant_id=tenant_id)
    await db.connect()
    return db


# ── Knowledge semantic-search helpers (DynamoDB/EC2 mode) ─────────────


def _dynamo_mode() -> bool:
    return os.environ.get("DYNAMODB_MODE", "").lower() in ("1", "true", "yes")


def _semantic_enabled() -> bool:
    """Semantic search runs only in DynamoDB mode with at least one owner id.

    Gating on ``index_user_ids()`` keeps default single-user SQLite/stdio and
    unconfigured DynamoDB deploys from calling Bedrock at startup.
    """
    from .knowledge_index import index_user_ids

    return bool(index_user_ids())


async def _knowledge_index_loop(db, ki, user_ids: list[str]) -> None:
    """Warm-start (load snapshot, else build) then refresh on an interval."""
    import asyncio
    import logging

    log = logging.getLogger("mcp_bookmarks.knowledge_index")
    from .embeddings_bedrock import embed_texts

    interval = int(os.environ.get("KNOWLEDGE_INDEX_REFRESH_SECONDS", "300"))
    if not ki.load():
        try:
            await ki.build(db, user_ids, embed_texts)
        except Exception:
            log.exception("knowledge_index_initial_build_failed")
    while True:
        try:
            await ki.refresh(db, user_ids, embed_texts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("knowledge_index_refresh_failed")
        await asyncio.sleep(interval)


def _record_scope_visible(rec: dict, scope: dict | None) -> bool:
    """Query-time equivalent of DynamoDBDatabase._item_scope_visible for a
    manifest record (holds ``mcpExposed`` + ``tags``)."""
    if scope is None:
        return True
    if rec.get("mcpExposed") is False:
        return False
    if scope.get("type") == "tags":
        tags = {t for t in (scope.get("tags") or []) if t}
        if tags and not (tags & set(rec.get("tags", []))):
            return False
    return True


# ── Server instance ───────────────────────────────────────────────

mcp = FastMCP(
    "Bookmarks Knowledge Base",
    instructions=(
        "Intelligent bookmark manager. Save URLs, extract metadata, "
        "and build a curated tag taxonomy. Always call get_tags before "
        "tagging to reuse existing tags and avoid duplicates."
    ),
    lifespan=app_lifespan,
    # Use MCP_HOST from env so DNS-rebinding protection is not auto-enabled
    # for the 127.0.0.1 default when deployed behind an ALB.
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    # Browser clients call /mcp with one-shot tools/call POSTs that don't
    # carry an Mcp-Session-Id. With the default stateful streamable_http
    # transport, FastMCP rejects those with HTTP 400. stateless_http makes
    # each request self-contained; json_response makes the response a
    # single JSON body (instead of an SSE stream the browser would have to
    # parse). The SSE transport (used by Claude / Cursor) is unaffected.
    stateless_http=True,
    json_response=True,
)


def _get_db(ctx: Context) -> BookmarkBackend:
    """Extract the active backend from lifespan context."""
    return ctx.request_context.lifespan_context.db


def _mcp_tenant_id(ctx: Context | None = None) -> str:
    if ctx is not None:
        try:
            return ctx.request_context.lifespan_context.tenant_id
        except AttributeError:
            pass
    return os.environ.get("DYNAMODB_ORG_ID", "default")


def _usage_db_path() -> Path:
    return Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))


async def _mcp_quota_block() -> str | None:
    """Returns a JSON string when the tenant is over quota; None otherwise.

    Thin shim — delegates to ``quota_service`` so the REST + MCP surfaces
    share quota state and structured-log behavior.
    """
    ok, used, limit = await quota_service.check(
        db_path=_usage_db_path(), tenant_id=_mcp_tenant_id(), surface="mcp"
    )
    return None if ok else quota_service.mcp_block_string(used, limit)


async def _mcp_record(event_type: str, metadata: dict | None = None) -> None:
    """Thin shim — delegates to ``quota_service.record``."""
    await quota_service.record(
        db_path=_usage_db_path(),
        event_type=event_type,
        tenant_id=_mcp_tenant_id(),
        metadata=metadata,
    )


def _bookmark_tool_id(b: Bookmark) -> int | str:
    """SQLite: integer id; DynamoDB: string UUID from save_bookmark."""
    if b.dynamo_id:
        return b.dynamo_id
    if b.id is not None:
        return b.id
    raise RuntimeError("Bookmark missing id (unexpected)")


# ═══════════════════════════════════════════════════════════════════
#  TOOLS
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def save_bookmark(
    url: str,
    ctx: Context,
    title: str | None = None,
    bookmarkType: str | None = None,
    flowId: str | None = None,
    source: str | None = None,
) -> str:
    """Save a URL and extract its Open Graph metadata.

    Fetches the page, parses og:title, og:description, og:image, etc.
    Returns the extracted metadata so you can decide how to tag it.

    First-party browser/mobile clients pass ``title``, ``bookmarkType``,
    ``flowId``, ``source`` along with the URL — they're stored on the DDB
    item under camelCase keys (``bookmarkType``, ``flowId``) so subsequent
    reads see them. When ``title`` is omitted we fall back to the OG title.

    IMPORTANT: After saving, call get_tags() to see existing tags
    before creating new ones.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)

    await ctx.info(f"Fetching metadata for {url}")
    bookmark, _og = await bookmark_service.save_with_metadata(
        db=db,
        url=url,
        title=title,
        bookmark_type=bookmarkType,
        flow_id=flowId,
        source=source,
    )

    await _mcp_record("mcp_save_bookmark", {"url": url})
    payload = bookmark.model_dump(by_alias=True, exclude_none=True)
    payload["bookmark_id"] = _bookmark_tool_id(bookmark)
    payload["existing_tags"] = bookmark.tags
    payload["has_content"] = bookmark.content is not None
    if flowId:
        payload["flowId"] = flowId
    payload["hint"] = (
        "DynamoDB: bookmark_id is a UUID string — pass it to extract_content, "
        "tag_bookmark, set_summary. SQLite: integer id. Now call get_tags() "
        "before tag_bookmark()."
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def extract_content(bookmark_id: int | str, ctx: Context) -> str:
    """Extract and store the full article text from a bookmark's URL.

    Uses trafilatura for high-quality article extraction — strips
    navigation, ads, footers, and returns clean article body text.
    The content is stored in the database for future summarization
    and knowledge base queries.

    Args:
        bookmark_id: The bookmark ID (returned by save_bookmark).
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    bookmark = await db.get_bookmark_by_id(bookmark_id)
    if not bookmark:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"})

    if bookmark.content:
        await _mcp_record(
            "mcp_extract_content", {"bookmark_id": str(bookmark_id), "status": "already_extracted"}
        )
        return json.dumps(
            {
                "bookmark_id": bookmark_id,
                "word_count": bookmark.word_count,
                "status": "already_extracted",
                "content_preview": bookmark.content[:500] + "..."
                if len(bookmark.content) > 500
                else bookmark.content,
            },
            ensure_ascii=False,
        )

    await ctx.info(f"Extracting article content from {bookmark.url}")
    # bookmark_service handles the scraper boundary + structured log + persistence.
    # Returns word count 0 on failure (extraction is best-effort here).
    try:
        article = await extract_article_content(bookmark.url)
    except Exception as e:
        return json.dumps({"error": f"Extraction failed: {e}"})

    await db.set_bookmark_content(bookmark_id, article.text, article.word_count)
    await _mcp_record("mcp_extract_content", {"bookmark_id": str(bookmark_id)})

    # Truncate for the response (full content is in DB)
    preview = article.text[:2000] + "..." if len(article.text) > 2000 else article.text

    return json.dumps(
        {
            "bookmark_id": bookmark_id,
            "word_count": article.word_count,
            "extraction_method": article.extraction_method,
            "content_preview": preview,
            "hint": "Use set_summary() to store a concise summary based on this content.",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def set_bookmark_body(bookmark_id: int | str, text: str, ctx: Context) -> str:
    """Store full article text when you already fetched it elsewhere (e.g. Bright Data or Tavily MCP).

    Same persistence as extract_content but no HTTP download. Use after save_bookmark when
    another tool returned the page body.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    bookmark = await bookmark_service.get_or_none(db=db, bookmark_id=bookmark_id)
    if not bookmark:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"})
    body = text or ""
    wc = await bookmark_service.set_body(db=db, bookmark_id=bookmark_id, text=body)
    await _mcp_record("mcp_set_bookmark_body", {"bookmark_id": str(bookmark_id)})
    preview = body[:2000] + "..." if len(body) > 2000 else body
    return json.dumps(
        {
            "bookmark_id": bookmark_id,
            "word_count": wc,
            "source": "provided_text",
            "content_preview": preview,
            "hint": "Use set_summary() when ready.",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def read_bookmark(bookmark_id: int | str, ctx: Context) -> str:
    """Read a bookmark's full details including extracted content.

    Returns all metadata, tags, summary, and the full article text
    (if previously extracted). Use this to review a bookmark before
    summarizing or to answer questions about its content.

    Args:
        bookmark_id: The bookmark ID.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    bookmark = await db.get_bookmark_by_id(bookmark_id)
    if not bookmark:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"})

    await _mcp_record("mcp_read_bookmark", {"bookmark_id": str(bookmark_id)})

    # Emit camelCase by alias (canonical wire shape: ogImage, aiSummary, etc.)
    # plus the snake_case fields kept for back-compat with existing clients.
    result = bookmark.model_dump(by_alias=True, exclude_none=True)
    result.setdefault("id", bookmark.dynamo_id or bookmark.id)
    snake = bookmark.model_dump(exclude_none=True)
    for k in ("description", "image_url", "site_name", "summary", "word_count"):
        if k in snake and k not in result:
            result[k] = snake[k]

    # Include full content if available (capped at 8k chars to be context-friendly)
    if bookmark.content:
        if len(bookmark.content) > 8000:
            result["content"] = bookmark.content[:8000]
            result["aiContent"] = result["content"]
            result["content_truncated"] = True
            result["full_word_count"] = bookmark.word_count
        else:
            result["content"] = bookmark.content
            result["aiContent"] = bookmark.content
            result["content_truncated"] = False
    else:
        result["content"] = None
        result["aiContent"] = None
        result["hint"] = "No content extracted yet. Call extract_content() first."

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def get_tags(
    query: str | None = None,
    ctx: Context = None,
) -> str:
    """Get the canonical tag taxonomy from the knowledge base.

    Returns all tags with their slug, name, description, and usage count.
    Use this BEFORE creating new tags to check if a suitable one exists.

    Args:
        query: Optional search filter (partial match on slug/name/description).
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)

    if query:
        tags = await db.search_tags(query)
    else:
        tags = await db.get_all_tags()

    if not tags:
        return json.dumps(
            {"tags": [], "hint": "No tags yet. Use create_tag() to start building your taxonomy."},
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "total": len(tags),
            "tags": [
                {
                    "slug": t.slug,
                    "name": t.name,
                    "description": t.description,
                    "usage_count": t.usage_count,
                }
                for t in tags
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def create_tag(
    slug: str,
    name: str,
    description: str,
    ctx: Context = None,
) -> str:
    """Create a new canonical tag in the taxonomy.

    Only create a tag when get_tags() confirms no existing tag covers
    this concept. Provide a clear description so future decisions can
    reuse this tag properly.

    Args:
        slug: Normalized identifier (lowercase, hyphens). E.g. 'machine-learning'.
        name: Human-readable label. E.g. 'Machine Learning'.
        description: Scope of this tag — what topics it covers and when to use it.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)

    existing = await db.get_tag_by_slug(slug)
    if existing:
        return json.dumps(
            {
                "error": f"Tag '{slug}' already exists.",
                "existing_tag": existing.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    tag = await db.create_tag(slug=slug, name=name, description=description)
    await _mcp_record("mcp_create_tag", {"slug": slug})
    return json.dumps(
        {"created": tag.model_dump(mode="json")},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def tag_bookmark(
    bookmark_id: int | str,
    tag_slugs: list[str],
    ctx: Context = None,
) -> str:
    """Assign existing tags to a bookmark.

    All tag slugs must already exist — call create_tag() first if needed.

    Args:
        bookmark_id: The bookmark ID (returned by save_bookmark).
        tag_slugs: List of tag slugs to assign. E.g. ['python', 'web-scraping'].
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)

    try:
        bookmark = await db.tag_bookmark(bookmark_id, tag_slugs)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    if not bookmark:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"})

    await _mcp_record("mcp_tag_bookmark", {"bookmark_id": str(bookmark_id)})
    return json.dumps(
        {
            "bookmark_id": _bookmark_tool_id(bookmark),
            "url": bookmark.url,
            "title": bookmark.title,
            "tags": bookmark.tags,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def search_bookmarks(
    query: str | None = None,
    tag: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
    ctx: Context = None,
) -> str:
    """Search the bookmark knowledge base.

    Args:
        query: Free-text search across title, description, and URL.
        tag: Filter by a specific tag slug.
        limit: Max results to return per page (default 20, capped at 100).
        cursor: Opaque pagination cursor returned by a prior call as
            ``nextCursor``. Pass it back to fetch the next page.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    bookmarks, next_cursor = await search_service.search_bookmarks(
        db=db, query=query, tag=tag, limit=limit, cursor=cursor
    )
    await _mcp_record("mcp_search_bookmarks", {"query": query, "tag": tag})

    items = []
    for b in bookmarks:
        item = b.model_dump(by_alias=True, exclude_none=True)
        item.setdefault("id", b.dynamo_id or b.id)
        # Trim long descriptions in list view to keep payloads tight; full
        # content lives behind read_bookmark.
        if isinstance(item.get("ogDescription"), str) and len(item["ogDescription"]) > 200:
            item["ogDescription"] = item["ogDescription"][:200]
        if isinstance(item.get("description"), str) and len(item["description"]) > 200:
            item["description"] = item["description"][:200]
        item["has_content"] = b.content is not None
        items.append(item)

    return json.dumps(
        {
            "total": len(bookmarks),
            "bookmarks": items,
            "nextCursor": next_cursor,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@mcp.tool()
async def set_summary(
    bookmark_id: int | str,
    summary: str,
    ctx: Context = None,
) -> str:
    """Store an AI-generated summary for a bookmark.

    Use this after analyzing a bookmark's content to save
    a concise summary for future reference.

    Args:
        bookmark_id: The bookmark ID.
        summary: A concise summary of the bookmark's content.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    await db.set_bookmark_summary(bookmark_id, summary)
    await _mcp_record("mcp_set_summary", {"bookmark_id": str(bookmark_id)})
    return json.dumps({"status": "ok", "bookmark_id": bookmark_id}, ensure_ascii=False)


@mcp.tool()
async def get_stats(ctx: Context = None) -> str:
    """Get knowledge base statistics (total bookmarks, total tags)."""
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    stats = await db.get_stats()
    return json.dumps(stats, ensure_ascii=False)


# ── Management tools ─────────────────────────────────────────────


@mcp.tool()
async def delete_bookmark(bookmark_id: int | str, ctx: Context = None) -> str:
    """Delete a bookmark and its tag associations.

    Tag usage counts are automatically recalculated.

    Args:
        bookmark_id: The bookmark ID to delete.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    deleted = await db.delete_bookmark(bookmark_id)
    if not deleted:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"})
    await _mcp_record("mcp_delete_bookmark", {"bookmark_id": str(bookmark_id)})
    return json.dumps({"status": "deleted", "bookmark_id": bookmark_id})


@mcp.tool()
async def update_tag(
    slug: str,
    new_name: str | None = None,
    new_description: str | None = None,
    ctx: Context = None,
) -> str:
    """Update a tag's name or description.

    Use this during taxonomy curation to improve tag descriptions
    or fix naming. The slug itself cannot be changed — merge instead.

    Args:
        slug: The tag slug to update.
        new_name: New human-readable name (optional).
        new_description: New scope description (optional).
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    tag = await db.update_tag(slug, new_name=new_name, new_description=new_description)
    if not tag:
        return json.dumps({"error": f"Tag '{slug}' not found"})
    await _mcp_record("mcp_update_tag", {"slug": slug})
    return json.dumps({"updated": tag.model_dump(mode="json")}, ensure_ascii=False, indent=2)


@mcp.tool()
async def delete_tag(slug: str, ctx: Context = None) -> str:
    """Delete a tag and remove it from all bookmarks.

    Use sparingly — prefer merge_tags to preserve associations.

    Args:
        slug: The tag slug to delete.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    deleted = await db.delete_tag(slug)
    if not deleted:
        return json.dumps({"error": f"Tag '{slug}' not found"})
    await _mcp_record("mcp_delete_tag", {"slug": slug})
    return json.dumps({"status": "deleted", "slug": slug})


@mcp.tool()
async def merge_tags(
    source_slug: str,
    target_slug: str,
    ctx: Context = None,
) -> str:
    """Merge one tag into another.

    All bookmarks tagged with source_slug get target_slug instead,
    then source is deleted. Use during taxonomy curation to consolidate
    duplicate or overlapping tags.

    Args:
        source_slug: The tag to merge FROM (will be deleted).
        target_slug: The tag to merge INTO (will absorb bookmarks).
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    try:
        result = await db.merge_tags(source_slug, target_slug)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    await _mcp_record("mcp_merge_tags", {"source": source_slug, "target": target_slug})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def untag_bookmark(
    bookmark_id: int | str,
    tag_slugs: list[str],
    ctx: Context = None,
) -> str:
    """Remove specific tags from a bookmark.

    Args:
        bookmark_id: The bookmark ID.
        tag_slugs: List of tag slugs to remove.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)
    bookmark = await db.untag_bookmark(bookmark_id, tag_slugs)
    if not bookmark:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"})
    await _mcp_record("mcp_untag_bookmark", {"bookmark_id": str(bookmark_id)})
    return json.dumps(
        {
            "bookmark_id": _bookmark_tool_id(bookmark),
            "url": bookmark.url,
            "remaining_tags": bookmark.tags,
            "removed": tag_slugs,
        },
        ensure_ascii=False,
        indent=2,
    )


# ── Export tools ─────────────────────────────────────────────────


@mcp.tool()
async def export_bookmarks(
    format: str = "json",
    tag: str | None = None,
    ctx: Context = None,
) -> str:
    """Export the knowledge base in various formats.

    Args:
        format: Output format — 'json', 'markdown', or 'opml'.
        tag: Optional tag filter — export only bookmarks with this tag.
    """
    if qb := await _mcp_quota_block():
        return qb
    db = _get_db(ctx)

    if tag:
        bookmarks = await db.search_bookmarks(tag=tag, limit=9999)
        tags = await db.get_all_tags()
    else:
        full = await db.get_full_export()
        bookmarks_data = full["bookmarks"]
        tags = [Tag(**t) for t in full["tags"]]
        bookmarks = [Bookmark(**b) for b in bookmarks_data]

    if format == "json":
        export = (
            await db.get_full_export()
            if not tag
            else {
                "tag_filter": tag,
                "total": len(bookmarks),
                "bookmarks": [
                    {"url": b.url, "title": b.title, "tags": b.tags, "summary": b.summary}
                    for b in bookmarks
                ],
            }
        )
        await _mcp_record("mcp_export_bookmarks", {"format": format, "tag": tag})
        return json.dumps(export, ensure_ascii=False, indent=2)

    elif format == "markdown":
        lines = ["# Bookmark Knowledge Base Export", ""]
        if tag:
            lines.append(f"**Filter:** tag={tag}")
            lines.append("")

        # Group by tags
        tag_groups: dict[str, list] = {}
        for b in bookmarks:
            for t in b.tags or ["untagged"]:
                tag_groups.setdefault(t, []).append(b)

        for tag_slug in sorted(tag_groups.keys()):
            tag_obj = next((t for t in tags if t.slug == tag_slug), None)
            tag_label = tag_obj.name if tag_obj else tag_slug
            lines.append(f"## {tag_label}")
            lines.append("")
            for b in tag_groups[tag_slug]:
                lines.append(f"- [{b.title or b.url}]({b.url})")
                if b.summary:
                    lines.append(f"  > {b.summary}")
            lines.append("")

        await _mcp_record("mcp_export_bookmarks", {"format": format, "tag": tag})
        return "\n".join(lines)

    elif format == "opml":
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<opml version="2.0">',
            "  <head><title>MCP Bookmarks Export</title></head>",
            "  <body>",
        ]
        for b in bookmarks:
            title = (b.title or b.url).replace("&", "&amp;").replace('"', "&quot;")
            url = b.url.replace("&", "&amp;")
            tags_str = ",".join(b.tags) if b.tags else ""
            lines.append(
                f'    <outline text="{title}" htmlUrl="{url}" type="link" category="{tags_str}"/>'
            )
        lines.extend(["  </body>", "</opml>"])
        await _mcp_record("mcp_export_bookmarks", {"format": format, "tag": tag})
        return "\n".join(lines)

    await _mcp_record("mcp_export_bookmarks", {"format": format, "error": True})
    return json.dumps({"error": f"Unknown format: {format}. Use 'json', 'markdown', or 'opml'."})


@mcp.tool()
async def index_bookmark_embedding(bookmark_id: int | str, ctx: Context) -> str:
    """Embed a bookmark for semantic search.

    - **DynamoDB/EC2 mode:** Knowledge bookmarks are indexed automatically by a
      background task. This tool forces an immediate index refresh (picks up
      newly-saved / newly-promoted Knowledge bookmarks now instead of waiting
      for the next cycle); ``bookmark_id`` is advisory.
    - **SQLite mode:** embeds title+description+content for one bookmark via
      OpenAI (requires ``OPENAI_API_KEY``).
    """
    if qb := await _mcp_quota_block():
        return qb
    if _dynamo_mode():
        from .knowledge_index import get_knowledge_index, index_user_ids

        ki = get_knowledge_index()
        if ki is None:
            return json.dumps(
                {"error": "Semantic index disabled (set OWNER_USER_ID / KNOWLEDGE_INDEX_USER_IDS)."},
                ensure_ascii=False,
            )
        from .embeddings_bedrock import embed_texts

        try:
            stats = await ki.refresh(_get_db(ctx), index_user_ids(), embed_texts)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        await _mcp_record("mcp_index_bookmark_embedding", {"mode": "dynamodb"})
        return json.dumps({"status": "refreshed", **stats}, ensure_ascii=False)
    db = _get_db(ctx)
    bookmark = await bookmark_service.get_or_none(db=db, bookmark_id=bookmark_id)
    if not bookmark:
        return json.dumps({"error": f"Bookmark {bookmark_id} not found"}, ensure_ascii=False)
    bid = _coerce_sqlite_bookmark_id(bookmark_id)
    if bid is None:
        return json.dumps(
            {"error": "Expected integer bookmark id in SQLite mode."}, ensure_ascii=False
        )
    parts = [bookmark.title or "", bookmark.description or "", (bookmark.content or "")[:20_000]]
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return json.dumps(
            {"error": "No text to embed; run extract_content first."}, ensure_ascii=False
        )

    try:
        model, chars_used = await embedding_service.index_bookmark(
            db=db, bookmark_id=bid, text=text
        )
    except UnsupportedCapability as exc:
        return json.dumps({"error": exc.to_envelope()}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    await _mcp_record("mcp_index_bookmark_embedding", {"bookmark_id": str(bid)})
    return json.dumps(
        {"status": "indexed", "bookmark_id": bid, "model": model, "chars_used": chars_used},
        ensure_ascii=False,
    )


@mcp.tool()
async def semantic_search_bookmarks(query: str, limit: int = 8, ctx: Context = None) -> str:
    """Semantic (vector) search over your **Knowledge** bookmarks.

    Matches meaning, not just keywords, against the extracted content of
    bookmarks you've curated as Knowledge. In DynamoDB/EC2 mode this queries an
    in-process Bedrock-embedded index; results are filtered to your own
    bookmarks and to your connection's scope (exposure + tag allowlist).
    """
    if qb := await _mcp_quota_block():
        return qb
    if _dynamo_mode():
        from .knowledge_index import get_knowledge_index
        from .request_context import current_scope, current_user_id

        ki = get_knowledge_index()
        if ki is None or not ki.ready:
            return json.dumps(
                {
                    "results": [],
                    "hint": "Knowledge index is still building or disabled; try again shortly.",
                },
                ensure_ascii=False,
                indent=2,
            )
        from .embeddings_bedrock import embed_model, embed_texts

        try:
            qvec = (await embed_texts([query]))[0]
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        uid = current_user_id()
        scope = current_scope()
        out: list[dict] = []
        for rec, score in ki.search(qvec, max(1, min(int(limit), 50))):
            if uid and rec.get("userId") != uid:
                continue
            if not _record_scope_visible(rec, scope):
                continue
            out.append(
                {
                    "id": rec.get("id"),
                    "url": rec.get("url"),
                    "title": rec.get("title"),
                    "score": round(score, 6),
                    "tags": rec.get("tags", []),
                    "summary": rec.get("summary", ""),
                }
            )
            if len(out) >= limit:
                break
        await _mcp_record("mcp_semantic_search_bookmarks", {"limit": limit, "mode": "dynamodb"})
        return json.dumps(
            {"query": query, "model": embed_model(), "total_indexed": ki.size, "results": out},
            ensure_ascii=False,
            indent=2,
        )
    db = _get_db(ctx)
    try:
        ranked, model = await embedding_service.semantic_search(db=db, query=query, limit=limit)
    except UnsupportedCapability as exc:
        return json.dumps({"error": exc.to_envelope()}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if not ranked:
        return json.dumps(
            {
                "results": [],
                "hint": "No embeddings yet. Call index_bookmark_embedding per bookmark (after extract_content).",
            },
            ensure_ascii=False,
            indent=2,
        )
    out: list[dict] = []
    for bid, score in ranked:
        bk = await bookmark_service.get_or_none(db=db, bookmark_id=bid)
        if not bk:
            continue
        out.append(
            {
                "id": bk.id,
                "url": bk.url,
                "title": bk.title,
                "score": round(score, 6),
                "tags": bk.tags,
                "summary": (bk.summary or "")[:400],
            }
        )
    await _mcp_record("mcp_semantic_search_bookmarks", {"limit": limit})
    return json.dumps(
        {"query": query, "model": model, "total_indexed": len(ranked), "results": out},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def ensemble_with_judge(
    task: str,
    models: str | None = None,
    judge_model: str | None = None,
    ctx: Context = None,
) -> str:
    """Run the same user task on several LLMs (OpenAI-compatible AI Gateway), then an LLM judge picks or merges the best answer.

    Costs N+1 API calls. Set ``ENSEMBLE_ENABLED=true``. Gateway: ``AI_GATEWAY_BASE_URL`` or ``OPENAI_BASE_URL`` (must end with ``/v1`` or base host); auth: ``AI_GATEWAY_API_KEY`` or ``OPENAI_API_KEY``. Default models from ``ENSEMBLE_MODELS`` (comma-separated) if ``models`` is omitted. Judge defaults to ``JUDGE_MODEL`` (e.g. ``gpt-4o-mini``).

    Args:
        task: User instruction or question for all candidates.
        models: Optional comma-separated model ids (overrides ENSEMBLE_MODELS).
        judge_model: Optional judge model id.
    """
    from . import llm_ensemble as le

    if not le.ensemble_enabled():
        return json.dumps(
            {
                "error": "Set ENSEMBLE_ENABLED=true to enable ensemble+judge (prevents accidental spend).",
                "hint": "Also set AI_GATEWAY_BASE_URL, API key, and ENSEMBLE_MODELS or pass models=.",
            },
            ensure_ascii=False,
            indent=2,
        )
    if qb := await _mcp_quota_block():
        return qb

    mlist = None
    if models and models.strip():
        mlist = [x.strip() for x in models.split(",") if x.strip()]

    out = await le.run_ensemble_with_judge(
        task,
        models=mlist,
        judge_model=judge_model.strip() if judge_model else None,
    )
    await _mcp_record(
        "mcp_ensemble_with_judge",
        {"ok": out.get("ok"), "n_models": len(mlist or [])},
    )
    return json.dumps(out, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
#  PROMPTS — reusable interaction templates
# ═══════════════════════════════════════════════════════════════════


@mcp.prompt()
def save_and_tag(url: str) -> str:
    """Complete workflow to save, tag, and summarize a bookmark.

    Orchestrates the full pipeline:
    1. save_bookmark → extract OG metadata
    2. extract_content → get full article text
    3. get_tags → review existing taxonomy
    4. create_tag (only if needed) → expand taxonomy
    5. tag_bookmark → assign tags
    6. set_summary → store concise summary
    """
    return f"""Save this URL as a bookmark and process it through the full pipeline:

URL: {url}

Follow these steps in order:

1. **save_bookmark** — Save the URL and extract OG metadata.

2. **extract_content** — Extract the full article text from the page.

3. **get_tags** — Review ALL existing tags in the taxonomy. Pay close attention
   to each tag's description and usage count.

4. **Tag decision** — Based on the article content and existing tags:
   - Reuse existing tags whenever they semantically match (prefer broader tags
     with high usage over creating narrow duplicates).
   - Only call **create_tag** if the article covers a concept that NO existing
     tag adequately describes. When creating, write a clear scope description.

5. **tag_bookmark** — Assign 2-5 tags (mix of broad and specific).

6. **set_summary** — Write a 2-3 sentence summary capturing the key insight
   or takeaway, not just what the article is "about".

Return a final report with: title, tags assigned, summary, and any new tags created."""


@mcp.prompt()
def bulk_save(urls: str) -> str:
    """Process multiple URLs through the save-tag-summarize pipeline.

    Args:
        urls: Newline-separated list of URLs to process.
    """
    return f"""Process each of these URLs through the full bookmark pipeline.

URLs (one per line):
{urls}

For EACH URL, follow this workflow:
1. save_bookmark(url)
2. extract_content(bookmark_id)
3. get_tags() — check existing tags ONCE before the first URL, then refer to
   your knowledge of the taxonomy for subsequent URLs (call again only if
   you think a new domain of tags might be needed).
4. Decide on tags — reuse aggressively, create sparingly.
5. tag_bookmark(bookmark_id, tags)
6. set_summary(bookmark_id, summary)

After processing all URLs, provide a summary table:
| # | Title | Tags | Summary |
"""


@mcp.prompt()
def curate_tags() -> str:
    """Review and clean up the tag taxonomy.

    Identifies potential duplicates, underused tags, and
    opportunities to merge or refine tag descriptions.
    """
    return """Audit the bookmark tag taxonomy for quality and consistency.

1. Call **get_tags()** to retrieve the full taxonomy.

2. Analyze the tags for:
   - **Duplicates/overlaps**: Tags that cover the same concept
     (e.g. 'ml' and 'machine-learning', 'js' and 'javascript').
   - **Vague descriptions**: Tags missing descriptions or with unhelpful ones.
   - **Underused tags**: Tags with usage_count=0 or very low usage.
   - **Missing tags**: Obvious gaps based on the existing tag patterns.

3. For each issue found, suggest a concrete action:
   - MERGE: "Merge 'ml' into 'machine-learning', update description to..."
   - UPDATE: "Update description of 'devops' to better clarify scope..."
   - DELETE: "Consider removing 'misc' (0 uses, too vague)"
   - CREATE: "Consider adding 'observability' to cover monitoring/tracing articles"

Present findings as a prioritized list of recommended actions."""


@mcp.prompt()
def knowledge_query(question: str) -> str:
    """Answer a question using the bookmark knowledge base.

    Searches bookmarks and their extracted content to find
    relevant information for the question.
    """
    return f"""Answer this question using the bookmark knowledge base:

Question: {question}

Steps:
0. (SQLite only, if you previously indexed embeddings) **semantic_search_bookmarks** —
   run this first when vectors are available; otherwise skip.
1. **search_bookmarks** — Keyword search with terms from the question.
2. **read_bookmark** — Read full content of the most relevant results.
3. **Synthesize** — Combine information from multiple bookmarks. Cite bookmarks by title + URL.

If the knowledge base doesn't contain enough information, say so clearly
and suggest what kinds of bookmarks would help answer this question."""


# ═══════════════════════════════════════════════════════════════════
#  RESOURCES — data the LLM can read as context
# ═══════════════════════════════════════════════════════════════════


@mcp.resource("bookmarks://taxonomy")
async def taxonomy_resource(ctx: Context) -> str:
    """The complete tag taxonomy as a reference document.

    LLMs can load this as context to understand the full
    scope of the knowledge base before making decisions.
    """
    db = _get_db(ctx)
    tags = await db.get_all_tags()
    stats = await db.get_stats()

    lines = [
        "# Bookmark Knowledge Base Taxonomy",
        "",
        f"Total bookmarks: {stats['total_bookmarks']}",
        f"Total tags: {stats['total_tags']}",
        "",
        "## Tags (by usage)",
        "",
    ]
    for t in tags:
        lines.append(f"- **{t.slug}** ({t.usage_count} uses): {t.description or 'No description'}")

    return "\n".join(lines)


@mcp.resource("bookmarks://recent/{count}")
async def recent_bookmarks_resource(count: str, ctx: Context) -> str:
    """Recent bookmarks as a reference document."""
    limit = min(int(count), 50)
    db = _get_db(ctx)
    bookmarks = await db.search_bookmarks(limit=limit)

    lines = [f"# Recent Bookmarks (last {limit})", ""]
    for b in bookmarks:
        tags_str = ", ".join(b.tags) if b.tags else "untagged"
        lines.append(f"- [{b.title or b.url}]({b.url}) — [{tags_str}]")
        if b.summary:
            lines.append(f"  > {b.summary}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════


def create_combined_app():
    """Create a Starlette app that serves both MCP transports and the REST API.

    Routes:
        /health           → Liveness probe (no auth, for ALB / k8s healthcheck)
        /sse, /messages/  → MCP SSE transport (Claude Code, Cursor, mcp-remote)
        /mcp              → MCP Streamable HTTP transport (ChatGPT custom connectors)
        /api/*            → REST API (bookmarklet, browser clients)
        /bookmarklet      → Bookmarklet installation page
        /ai-gateway       → AI Gateway ensemble + judge test UI
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Mount, Route

    from .api import (
        ai_gateway_page,
        bookmarklet_page,
        create_api_app,
        static_font_jetbrains_mono,
        stripe_webhook,
    )

    api_app = create_api_app()
    sse_app = mcp.sse_app()
    # streamable_http_app() lazily initializes mcp._session_manager, which we
    # need to start as a lifespan context so its anyio task group is running.
    streamable_app = mcp.streamable_http_app()

    async def root(request):
        return RedirectResponse("/bookmarklet")

    async def health(request):
        """Liveness probe: returns 200 as long as the process is up."""
        return JSONResponse({"status": "ok"})

    async def ready(request):
        """Readiness probe: returns 200 only when the active backend is reachable.

        Used by ECS/ALB target health checks and by clients that need to wait
        for the cold-start path (DB connect, DynamoDB describe) to complete.
        Returns 503 with structured details on failure so the alarm payload is
        readable.
        """
        import asyncio
        import logging

        ready_log = logging.getLogger("mcp_bookmarks.health")
        try:
            db = await asyncio.wait_for(_open_db_for_ready(), timeout=3.0)
        except TimeoutError:
            ready_log.warning("ready_check_failed", extra={"reason": "open_timeout"})
            return JSONResponse(
                {"status": "not_ready", "reason": "backend open timed out"},
                status_code=503,
            )
        except Exception as exc:
            ready_log.warning(
                "ready_check_failed", extra={"reason": "open_error", "error": str(exc)}
            )
            return JSONResponse(
                {"status": "not_ready", "reason": str(exc)},
                status_code=503,
            )
        try:
            await asyncio.wait_for(db.get_stats(), timeout=3.0)
        except Exception as exc:
            ready_log.warning(
                "ready_check_failed", extra={"reason": "ping_error", "error": str(exc)}
            )
            return JSONResponse(
                {"status": "not_ready", "reason": str(exc)},
                status_code=503,
            )
        finally:
            # Close failure must not flip /ready to not_ready — the ping already proved liveness.
            with contextlib.suppress(Exception):
                await db.close()
        return JSONResponse({"status": "ready"})

    @asynccontextmanager
    async def _lifespan(app):
        """Run the StreamableHTTP session manager for the lifetime of the app."""
        # Structured logging is configured here rather than at module import
        # time so the right format (json vs pretty) is picked up from the env
        # the uvicorn process sees, not from whatever was set when the module
        # was first imported.
        from .logging_config import configure_logging

        configure_logging()

        # Knowledge semantic index: start the background build/refresh loop ONCE
        # for the process lifetime. This lives here (not in FastMCP's
        # app_lifespan, which the session manager enters lazily per-session) so
        # it starts at uvicorn boot regardless of request traffic. It uses its
        # own DynamoDB handle — the builder only needs query_raw_by_type.
        knowledge_task = None
        if _dynamo_mode() and _semantic_enabled():
            import asyncio
            import logging

            from .dynamodb import DynamoDBDatabase
            from .knowledge_index import index_user_ids, init_knowledge_index

            ki = init_knowledge_index()
            kdb = DynamoDBDatabase()
            await kdb.connect()
            knowledge_task = asyncio.create_task(
                _knowledge_index_loop(kdb, ki, index_user_ids())
            )
            logging.getLogger("mcp_bookmarks.knowledge_index").info(
                "knowledge_index_task_started", extra={"user_ids": index_user_ids()}
            )

        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if knowledge_task is not None:
                knowledge_task.cancel()
                with contextlib.suppress(Exception):
                    await knowledge_task

    # Both MCP transport apps register their routes internally:
    #   /mcp       — Streamable HTTP (ChatGPT custom connectors)
    #   /sse       — SSE GET endpoint (Claude Code, Cursor)
    #   /messages/ — SSE POST endpoint
    # Spreading them directly into the parent router avoids conflicting catch-all
    # mounts while still keeping both transports alive.
    cors_origins_raw = os.environ.get(
        "MCP_CORS_ORIGINS",
        "http://localhost:3000",
    )
    cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    from starlette.middleware.gzip import GZipMiddleware

    from .api import AI_GATEWAY_SCRIPT_HASH
    from .bearer_auth import BearerAuthMiddleware
    from .correlation import CorrelationMiddleware
    from .origin_secret import OriginSecretMiddleware
    from .security_headers import SecurityHeadersMiddleware

    # Order matters:
    #   - GZip is outermost so the final byte stream (with every other
    #     middleware's mutations baked in) gets compressed in one pass.
    #     Starlette's GZipMiddleware auto-skips `text/event-stream` so /sse
    #     keeps streaming uncompressed.
    #   - Correlation IDs propagate next so every later log record carries
    #     the same id.
    #   - Security headers (incl. CSP for /bookmarklet + /ai-gateway) inject
    #     before CORS preflight responses fly back.
    #   - CORS runs next so preflight responses get Access-Control-Allow-*
    #     even when bearer auth would reject the inner request.
    #   - Auth runs last, gating /mcp, /sse, /messages.
    middleware = [
        # Outermost: reject any request that didn't come through CloudFront
        # (no-op unless ORIGIN_SHARED_SECRET is set). Runs before GZip so
        # rejected traffic does the least possible work.
        Middleware(OriginSecretMiddleware),
        Middleware(GZipMiddleware, minimum_size=500),
        Middleware(CorrelationMiddleware),
        Middleware(
            SecurityHeadersMiddleware,
            ai_gateway_script_hash=AI_GATEWAY_SCRIPT_HASH,
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Accept",
                "Mcp-Session-Id",
                "X-Correlation-ID",
            ],
            expose_headers=["Mcp-Session-Id", "X-Correlation-ID"],
            max_age=600,
        ),
        Middleware(BearerAuthMiddleware),
    ]
    app = Starlette(
        lifespan=_lifespan,
        middleware=middleware,
        routes=[
            Route("/", root),
            Route("/health", health),
            Route("/ready", ready),
            Route("/bookmarklet", bookmarklet_page),
            Route("/ai-gateway", ai_gateway_page),
            Route("/static/jetbrains-mono.woff2", static_font_jetbrains_mono),
            Route("/webhooks/stripe", stripe_webhook, methods=["POST"]),
            Mount("/api", app=api_app),
            *streamable_app.routes,  # Route(path='/mcp', ...)
            *sse_app.routes,  # Route(path='/sse', ...) + Mount(path='/messages', ...)
        ],
    )
    return app


def main():
    """Run the combined MCP + REST server."""
    import uvicorn

    port = int(os.environ.get("MCP_PORT", "8000"))
    host = os.environ.get("MCP_HOST", "0.0.0.0")

    print("🚀 MCP Bookmarks Server")
    print(f"   MCP SSE:          http://{host}:{port}/sse")
    print(f"   MCP Streamable:   http://{host}:{port}/mcp")
    print(f"   REST API:         http://{host}:{port}/api/")
    print(f"   Stripe hook:      http://{host}:{port}/webhooks/stripe")
    print(f"   Bookmarklet:      http://{host}:{port}/bookmarklet")
    print(f"   AI Gateway:       http://{host}:{port}/ai-gateway")
    print()

    app = create_combined_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
