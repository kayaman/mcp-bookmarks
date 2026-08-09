"""
REST API routes for browser-based bookmark saving.

These lightweight Starlette routes run alongside the MCP SSE server
and provide a simple HTTP interface for the bookmarklet and other
non-MCP clients.

Endpoints (mounted at ``/api``):
    POST /api/save      — Quick save a URL (extract OG + content)
    GET  /api/stats     — Knowledge base stats
    GET  /api/bookmarks — List/search bookmarks
    GET  /api/bookmarks/{{id}} — One bookmark (JSON; for CrewAI / clients)
    GET  /api/tags      — List all tags
    POST /api/tag       — Create a tag (slug, name, description) — Crew / automation
    POST /api/bookmarks/{{id}}/summary — Set AI summary text
    POST /api/bookmarks/{{id}}/tags    — Assign existing tag slugs
    GET  /api/usage     — Monthly usage counter (when API keys configured)
    GET  /api/ai-gateway/status — Safe metadata for the AI Gateway dashboard (no secrets)
    POST /api/ensemble  — Multi-model + LLM judge (requires ENSEMBLE_ENABLED=true)
    GET  /bookmarklet   — Bookmarklet installation page

Webhook (mount at app root):
    POST /webhooks/stripe — Stripe Billing events (requires STRIPE_WEBHOOK_SECRET)
"""

import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from . import llm_ensemble
from .api_envelope import ErrorCode, error_response
from .api_requests import (
    BookmarkSummaryRequest,
    BookmarkTagsRequest,
    CreateTagRequest,
    EnsembleRequest,
    SaveBookmarkRequest,
    parse_request_body,
)
from .auth import api_keys_configured, require_api_key
from .backend import (
    BookmarkBackend,
    UnsupportedCapability,
    backend_capabilities_payload,
)
from .bearer_auth import bm_v1_api_route
from .db import DEFAULT_DB_PATH, Database
from .security_headers import compute_script_hash
from .services import billing as billing_service
from .services import bookmark as bookmark_service
from .services import quota as quota_service
from .services import taxonomy as taxonomy_service
from .services import usage as usage_service
from .usage_meter import monthly_limit_enabled

log = logging.getLogger(__name__)


def _dynamodb_mode() -> bool:
    return os.environ.get("DYNAMODB_MODE", "").lower() in ("1", "true", "yes")


def _get_db_path() -> Path:
    return Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))


def _effective_tenant(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", None)
    if tid:
        return str(tid)
    return os.environ.get("DYNAMODB_ORG_ID", "default")


async def _check_rest_quota(tenant: str) -> JSONResponse | None:
    """Thin shim — delegates to ``quota_service.check`` + ``rest_block_response``."""
    ok, used, limit = await quota_service.check(
        db_path=_get_db_path(), tenant_id=tenant, surface="rest"
    )
    return None if ok else quota_service.rest_block_response(used, limit)


async def _record_rest_usage(tenant: str, event_type: str, metadata: dict | None = None) -> None:
    """Thin shim — delegates to ``quota_service.record``."""
    await quota_service.record(
        db_path=_get_db_path(), event_type=event_type, tenant_id=tenant, metadata=metadata
    )


async def _db(request: Request | None = None) -> BookmarkBackend:
    tenant_id = _effective_tenant(request) if request is not None else "default"
    db: BookmarkBackend
    if _dynamodb_mode():
        from .dynamodb import DynamoDBDatabase
        from .models import Tenant

        db = DynamoDBDatabase(tenant=Tenant(organization_id=tenant_id))
    else:
        db = Database(_get_db_path(), tenant_id=tenant_id)
    await db.connect()
    return db


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Require API key when ``MCP_API_KEYS`` is set. OPTIONS passes through."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        # Admin tag-editing carve-out: PUT /bookmarks/{id}/tags, GET
        # /bookmarks/recent and GET /tag-edits are authenticated by
        # BearerAuthMiddleware on the OUTER app (bm_v1 scoped token in the
        # same Authorization: Bearer header) — the static-key check here
        # must not 401 them. Starlette's Mount strips the /api prefix from
        # scope["path"] (used for routing) but NOT from request.url.path
        # (reconstructed via root_path + path), so this app still sees the
        # full /api-prefixed path when mounted — strip it before matching.
        # When api_app is exercised standalone (no Mount, as in the
        # TenantAuthMiddleware unit tests) the prefix is already absent.
        api_path = request.url.path
        if api_path.startswith("/api"):
            api_path = api_path[len("/api") :]
        if bm_v1_api_route(request.method, api_path):
            return await call_next(request)
        ok, tenant = require_api_key(request.headers)
        if not ok:
            return error_response(
                ErrorCode.UNAUTHORIZED,
                "Missing or invalid API key",
                status=401,
            )
        request.state.tenant_id = tenant
        return await call_next(request)


# ── Endpoints ─────────────────────────────────────────────────────


async def api_save(request: Request) -> JSONResponse:
    """POST /save — Save a URL with OG extraction."""
    tenant = _effective_tenant(request)
    blocked = await _check_rest_quota(tenant)
    if blocked:
        return blocked

    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body_model, err = await parse_request_body(request, SaveBookmarkRequest)
        if err is not None:
            return err
        assert body_model is not None  # parse_request_body invariant; helps mypy narrow
        url = body_model.url
    else:
        # Form-encoded bookmarklet POST or query-string fallback.
        form = await request.form()
        url = form.get("url") or request.query_params.get("url")
        if not url:
            return error_response(
                ErrorCode.INVALID_REQUEST,
                "Missing 'url' parameter",
                status=400,
                details={"fields": [{"loc": "url", "type": "missing", "message": "required"}]},
            )

    db = await _db(request)
    try:
        bookmark, og = await bookmark_service.save_with_metadata(db=db, url=url)
        assert bookmark.id is not None  # upsert_bookmark always returns an id
        word_count = await bookmark_service.extract_and_persist_content(
            db=db, bookmark_id=bookmark.id, url=url
        )

        await _record_rest_usage(tenant, "rest_bookmark_save", {"url": og.url})

        return JSONResponse(
            {
                "status": "saved",
                "bookmark_id": bookmark.id,
                "title": bookmark.title,
                "description": bookmark.description,
                "word_count": word_count,
                "message": "Saved! Connect via MCP to tag and summarize.",
            }
        )
    finally:
        await db.close()


async def api_stats(request: Request) -> JSONResponse:
    """GET /stats — Knowledge base statistics."""
    db = await _db(request)
    try:
        stats = await db.get_stats()
        return JSONResponse(stats)
    finally:
        await db.close()


async def api_bookmarks(request: Request) -> JSONResponse:
    """GET /bookmarks — List or search bookmarks."""
    query = request.query_params.get("query")
    tag = request.query_params.get("tag")
    limit = int(request.query_params.get("limit", "20"))

    db = await _db(request)
    try:
        bookmarks = await db.search_bookmarks(query=query, tag=tag, limit=limit)
        return JSONResponse(
            {
                "total": len(bookmarks),
                "bookmarks": [
                    {
                        "id": b.id,
                        "url": b.url,
                        "title": b.title,
                        "tags": b.tags,
                        "summary": b.summary,
                        "word_count": b.word_count,
                    }
                    for b in bookmarks
                ],
            }
        )
    finally:
        await db.close()


async def api_tags(request: Request) -> JSONResponse:
    """GET /tags — List all tags."""
    db = await _db(request)
    try:
        tags = await db.get_all_tags()
        return JSONResponse(
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
            }
        )
    finally:
        await db.close()


_MAX_BOOKMARK_CONTENT_JSON = 400_000


async def api_get_bookmark(request: Request) -> JSONResponse:
    """GET /bookmarks/{bookmark_id} — Full bookmark including content."""
    bookmark_id = request.path_params["bookmark_id"]
    db = await _db(request)
    try:
        bm = await db.get_bookmark_by_id(bookmark_id)
        if not bm:
            return error_response(
                ErrorCode.NOT_FOUND,
                f"Bookmark {bookmark_id} not found",
                status=404,
            )
        payload = bm.model_dump(mode="json")
        content = payload.get("content") or ""
        if isinstance(content, str) and len(content) > _MAX_BOOKMARK_CONTENT_JSON:
            payload["content"] = (
                content[:_MAX_BOOKMARK_CONTENT_JSON] + "\n…[truncated for JSON response]"
            )
            payload["content_truncated"] = True
        return JSONResponse(payload)
    finally:
        await db.close()


async def api_create_tag_rest(request: Request) -> JSONResponse:
    """POST /tag — Create taxonomy tag (body JSON: slug, name, description?)."""
    tenant = _effective_tenant(request)
    blocked = await _check_rest_quota(tenant)
    if blocked:
        return blocked
    body_model, err = await parse_request_body(request, CreateTagRequest)
    if err is not None:
        return err
    assert body_model is not None  # parse_request_body invariant
    slug = body_model.slug.strip()
    name = body_model.name.strip()
    description = body_model.description.strip()
    db = await _db(request)
    try:
        tag, created = await taxonomy_service.create(
            db=db, slug=slug, name=name, description=description
        )
        if not created:
            return error_response(
                ErrorCode.CONFLICT,
                f"Tag '{slug}' already exists",
                status=409,
                details={"slug": slug},
            )
        assert tag is not None  # `created=True` implies `tag` is not None
        await _record_rest_usage(tenant, "rest_create_tag", {"slug": slug})
        return JSONResponse(
            {
                "created": {
                    "slug": tag.slug,
                    "name": tag.name,
                    "description": tag.description,
                }
            },
            status_code=201,
        )
    finally:
        await db.close()


async def api_bookmark_summary(request: Request) -> JSONResponse:
    """POST /bookmarks/{bookmark_id}/summary — Body: {\"summary\": \"...\"}."""
    tenant = _effective_tenant(request)
    blocked = await _check_rest_quota(tenant)
    if blocked:
        return blocked
    bookmark_id = request.path_params["bookmark_id"]
    body_model, err = await parse_request_body(request, BookmarkSummaryRequest)
    if err is not None:
        return err
    assert body_model is not None  # parse_request_body invariant
    db = await _db(request)
    try:
        bm = await bookmark_service.get_or_none(db=db, bookmark_id=bookmark_id)
        if not bm:
            return error_response(
                ErrorCode.NOT_FOUND,
                f"Bookmark {bookmark_id} not found",
                status=404,
            )
        await bookmark_service.set_summary(
            db=db, bookmark_id=bookmark_id, summary=body_model.summary
        )
        await _record_rest_usage(tenant, "rest_set_summary", {"bookmark_id": str(bookmark_id)})
        return JSONResponse({"status": "ok", "bookmark_id": bookmark_id})
    finally:
        await db.close()


async def api_bookmark_tags(request: Request) -> JSONResponse:
    """POST /bookmarks/{bookmark_id}/tags — Body: {\"tag_slugs\": [\"a\",\"b\"]}."""
    tenant = _effective_tenant(request)
    blocked = await _check_rest_quota(tenant)
    if blocked:
        return blocked
    bookmark_id = request.path_params["bookmark_id"]
    body_model, err = await parse_request_body(request, BookmarkTagsRequest)
    if err is not None:
        return err
    assert body_model is not None  # parse_request_body invariant
    slugs = body_model.tag_slugs
    db = await _db(request)
    try:
        bm = await bookmark_service.get_or_none(db=db, bookmark_id=bookmark_id)
        if not bm:
            return error_response(
                ErrorCode.NOT_FOUND,
                f"Bookmark {bookmark_id} not found",
                status=404,
            )
        try:
            await taxonomy_service.tag_bookmark(db=db, bookmark_id=bookmark_id, tag_slugs=slugs)
        except ValueError as exc:
            return error_response(
                ErrorCode.INVALID_REQUEST,
                str(exc),
                status=400,
            )
        await _record_rest_usage(
            tenant, "rest_tag_bookmark", {"bookmark_id": str(bookmark_id), "count": len(slugs)}
        )
        return JSONResponse({"status": "ok", "bookmark_id": bookmark_id, "tag_slugs": slugs})
    finally:
        await db.close()


async def api_usage(request: Request) -> JSONResponse:
    """GET /usage — Monthly tool/event count for the authenticated tenant.

    Requires the active backend to support native ``usage_metering``. DynamoDB
    mode delegates to ``DYNAMODB_USAGE_TABLE`` (counted via ``usage_meter`` at
    write time) but cannot read aggregate counts back through the protocol, so
    we return a structured ``unsupported`` envelope in that case.
    """
    tenant = _effective_tenant(request)
    db = await _db(request)
    try:
        try:
            n = await usage_service.count_events_this_month(db=db, tenant_id=tenant)
        except UnsupportedCapability as exc:
            return error_response(
                ErrorCode.FORBIDDEN,
                str(exc),
                status=403,
                details=exc.to_envelope()["details"],
            )
    finally:
        await db.close()
    limit = int(os.environ.get("MCP_MONTHLY_USAGE_LIMIT", "0"))
    return JSONResponse(
        {
            "tenant_id": tenant,
            "events_this_month": n,
            "monthly_limit": limit,
            "limit_enforced": monthly_limit_enabled(),
        }
    )


async def api_capabilities(request: Request) -> JSONResponse:
    """GET /capabilities — Report the active backend's capability flags.

    Clients use this to branch ahead of calling a capability-gated endpoint
    instead of round-tripping for a 403. The response shape is::

        {
          "backend": "sqlite" | "dynamodb",
          "capabilities": {
            "semantic_search": bool,
            "paged_search": bool,
            "integer_bookmark_ids": bool,
            "usage_metering": bool,
            "subscription_storage": bool
          }
        }
    """
    db = await _db(request)
    try:
        return JSONResponse(backend_capabilities_payload(db))
    finally:
        await db.close()


async def api_ensemble(request: Request) -> JSONResponse:
    """POST /ensemble — Parallel chat completions + judge (OpenAI-compatible gateway)."""
    tenant = _effective_tenant(request)
    blocked = await _check_rest_quota(tenant)
    if blocked:
        return blocked
    if not llm_ensemble.ensemble_enabled():
        return error_response(
            ErrorCode.FORBIDDEN,
            "AI Gateway ensemble is disabled on this server",
            status=403,
            details={"hint": "Set ENSEMBLE_ENABLED=true; see docs/ai-gateway-ensemble.md"},
        )
    body_model, err = await parse_request_body(request, EnsembleRequest)
    if err is not None:
        return err
    assert body_model is not None  # parse_request_body invariant

    task = body_model.task.strip()
    mlist = None
    if body_model.models:
        mlist = [m.strip() for m in body_model.models if m.strip()]
    judge_model = body_model.judge_model.strip() if body_model.judge_model else None

    out = await llm_ensemble.run_ensemble_with_judge(
        task,
        models=mlist,
        judge_model=judge_model,
    )
    await _record_rest_usage(tenant, "rest_ensemble_judge", {"ok": out.get("ok")})
    return JSONResponse(out)


async def api_ai_gateway_status(request: Request) -> JSONResponse:
    """GET /ai-gateway/status — Safe gateway metadata for the dashboard (no secrets)."""
    return JSONResponse(llm_ensemble.gateway_status_public())


# ── /ai-gateway inline script (extracted so its SHA-256 can ride the CSP) ──
#
# Plain single-brace JavaScript. Embedded into the HTML f-string via a normal
# `{_AI_GATEWAY_INLINE_SCRIPT}` substitution; Python does not re-process the
# inner braces because f-string interpolation just stringifies the value.
# Recompute the hash whenever you edit this body — the CSP must match.
_AI_GATEWAY_INLINE_SCRIPT = """(function() {
    var STORAGE_BEARER = 'mcp-bookmarks-ai-gateway-bearer';
    var STORAGE_XKEY = 'mcp-bookmarks-ai-gateway-xkey';
    var apiKeyEl = document.getElementById('apiKey');
    var apiKeyHeaderEl = document.getElementById('apiKeyHeader');
    var resultPanel = document.getElementById('resultPanel');
    apiKeyEl.value = sessionStorage.getItem(STORAGE_BEARER) || '';
    apiKeyHeaderEl.value = sessionStorage.getItem(STORAGE_XKEY) || '';
    apiKeyEl.addEventListener('change', function() {
        var v = apiKeyEl.value.trim();
        if (v) sessionStorage.setItem(STORAGE_BEARER, v); else sessionStorage.removeItem(STORAGE_BEARER);
    });
    apiKeyHeaderEl.addEventListener('change', function() {
        var v = apiKeyHeaderEl.value.trim();
        if (v) sessionStorage.setItem(STORAGE_XKEY, v); else sessionStorage.removeItem(STORAGE_XKEY);
    });

    function authHeaders() {
        var h = {};
        var b = apiKeyEl.value.trim();
        var x = apiKeyHeaderEl.value.trim();
        if (b) h['Authorization'] = 'Bearer ' + b;
        if (x) h['X-API-Key'] = x;
        return h;
    }

    function parseModels(raw) {
        if (!raw || !raw.trim()) return undefined;
        var lines = raw.split(/[\\n,]+/).map(function(s) { return s.trim(); }).filter(Boolean);
        return lines.length ? lines : undefined;
    }

    function esc(s) {
        if (s == null) return '';
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    async function loadStatus() {
        var errEl = document.getElementById('loadErr');
        errEl.style.display = 'none';
        var bar = document.getElementById('statusBar');
        bar.innerHTML = '';
        try {
            var r = await fetch('/api/ai-gateway/status', { headers: authHeaders() });
            if (r.status === 401) {
                errEl.textContent = 'Unauthorized (401). Set REST API key above if MCP_API_KEYS is configured.';
                errEl.style.display = 'block';
                return;
            }
            if (!r.ok) throw new Error('HTTP ' + r.status);
            var d = await r.json();
            function chip(label, val, cls) {
                cls = cls || '';
                return '<div class="stat"><span class="stat-label">' + esc(label) + '</span><div class="val ' + cls + '">' + esc(String(val)) + '</div></div>';
            }
            bar.innerHTML =
                chip('Ensemble', d.ensemble_enabled ? 'enabled' : 'disabled', d.ensemble_enabled ? '' : 'warn') +
                chip('Gateway host', d.gateway_display) +
                chip('LLM API key', d.has_api_key_configured ? 'configured' : 'missing', d.has_api_key_configured ? '' : 'err') +
                chip('Default models', (d.default_models && d.default_models.length) ? d.default_models.join(', ') : '(none)') +
                chip('Default judge', d.default_judge);
        } catch (e) {
            errEl.textContent = 'Could not load status: ' + e.message;
            errEl.style.display = 'block';
        }
    }

    document.getElementById('runBtn').addEventListener('click', async function() {
        var btn = document.getElementById('runBtn');
        var task = document.getElementById('task').value.trim();
        if (!task) { alert('Enter a task'); return; }
        btn.disabled = true;
        resultPanel.style.display = 'none';
        resultPanel.setAttribute('aria-busy', 'true');
        try {
            var body = { task: task };
            var models = parseModels(document.getElementById('models').value);
            if (models) body.models = models;
            var judge = document.getElementById('judge').value.trim();
            if (judge) body.judge_model = judge;
            var headers = Object.assign({ 'Content-Type': 'application/json' }, authHeaders());
            var r = await fetch('/api/ensemble', { method: 'POST', headers: headers, body: JSON.stringify(body) });
            var data = await r.json().catch(function() { return {}; });
            if (r.status === 403) {
                var msg403 = (data.error && data.error.message) || data.error || 'Forbidden: set ENSEMBLE_ENABLED=true.';
                if (data.hint) msg403 += '\\n' + data.hint;
                else msg403 += ' See docs/ai-gateway-ensemble.md';
                alert(msg403);
                return;
            }
            if (r.status === 401) {
                alert('Unauthorized: set REST API key if MCP_API_KEYS is active.');
                return;
            }
            if (r.status === 429) {
                alert((data.error && data.error.message) || data.error || 'Quota exceeded');
                return;
            }
            if (!r.ok) {
                alert((data.error && data.error.message) || data.error || ('HTTP ' + r.status));
                return;
            }
            resultPanel.style.display = 'block';
            document.getElementById('partialBadge').style.display = data.partial ? 'inline-block' : 'none';
            var fb = document.getElementById('finalBlock');
            fb.innerHTML = '';
            if (data.answer != null) {
                var rationale = data.rationale ? '<div class="meta">Rationale: ' + esc(data.rationale) + '</div>' : '';
                var wm = (data.winner_model != null) ? '<div class="meta">Winner: ' + esc(data.winner_model) + (data.chosen_index != null ? ' (index ' + esc(String(data.chosen_index)) + ')' : '') + '</div>' : '';
                var jm = data.judge_model ? '<div class="meta">Judge: ' + esc(data.judge_model) + '</div>' : '';
                fb.innerHTML = '<div class="final"><strong>Answer</strong><div class="answer">' + esc(data.answer) + '</div>' + rationale + wm + jm + '</div>';
            } else if (data.error) {
                fb.innerHTML = '<div class="banner bad">' + esc((data.error && data.error.message) || data.error) + '</div>';
            }
            var cc = document.getElementById('candidates');
            cc.innerHTML = '';
            (data.candidates || []).forEach(function(c) {
                var div = document.createElement('div');
                div.className = 'card';
                var err = c.error;
                var bodyHtml = err
                    ? '<div class="body err">' + esc(err) + '</div>'
                    : '<div class="body">' + esc(c.content || '') + '</div>' + (c.truncated ? '<div class="hint">truncated</div>' : '');
                div.innerHTML = '<h3>' + esc(c.model || '?') + '</h3>' + bodyHtml;
                cc.appendChild(div);
            });
            if (data.judge_raw && !data.rationale) {
                var note = document.createElement('div');
                note.className = 'hint';
                note.textContent = 'Judge raw (preview): ' + (data.judge_raw || '').slice(0, 500);
                fb.appendChild(note);
            }
        } catch (e) {
            alert('Request failed: ' + e.message);
        } finally {
            btn.disabled = false;
            resultPanel.setAttribute('aria-busy', 'false');
        }
    });

    loadStatus();
})();"""

# SHA-256 of the literal byte stream above. Wired into the CSP by
# SecurityHeadersMiddleware so the browser can validate the inline script
# without ``'unsafe-inline'`` on script-src.
AI_GATEWAY_SCRIPT_HASH = compute_script_hash(_AI_GATEWAY_INLINE_SCRIPT)


# ── Static asset: self-hosted JetBrains Mono ──────────────────────


_STATIC_DIR = Path(__file__).parent / "_static"


def _read_font_bytes() -> bytes:
    """Load the self-hosted JetBrains Mono Regular woff2 once at module import."""
    path = _STATIC_DIR / "JetBrainsMono-Regular.woff2"
    return path.read_bytes()


_JETBRAINS_MONO_WOFF2 = _read_font_bytes()


async def static_font_jetbrains_mono(request: Request) -> Response:
    """GET /static/jetbrains-mono.woff2 — self-hosted, long-cached font asset.

    Self-hosting kills two birds: the Google Fonts CSS round-trip stops
    being render-blocking, and the font-swap layout shift on the mono
    elements (``.stat .val``, ``.card h3``) goes away.
    """
    _ = request
    return Response(
        content=_JETBRAINS_MONO_WOFF2,
        media_type="font/woff2",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Access-Control-Allow-Origin": "*",
        },
    )


async def ai_gateway_page(request: Request) -> HTMLResponse:
    """GET /ai-gateway (mounted at app root) — Visual tester for POST /api/ensemble."""
    canonical = f"{request.url.scheme}://{request.url.netloc}/ai-gateway"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Browser tester for the mcp-bookmarks AI Gateway ensemble endpoint. Run a prompt across multiple models and let an LLM judge pick the best answer.">
    <meta name="theme-color" content="#0f0f1a">
    <title>AI Gateway — Ensemble + Judge · mcp-bookmarks</title>
    <link rel="canonical" href="{canonical}">
    <link rel="preload" href="/static/jetbrains-mono.woff2" as="font" type="font/woff2" crossorigin>
    <style>
        @font-face {{
            font-family: "JetBrains Mono";
            src: url("/static/jetbrains-mono.woff2") format("woff2");
            font-weight: 400;
            font-style: normal;
            font-display: swap;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            background: #0f0f1a;
            color: #e0e0e0;
            min-height: 100vh;
            padding: 1.5rem;
            line-height: 1.5;
        }}
        .wrap {{ max-width: 1100px; margin: 0 auto; }}
        h1 {{
            font-size: 1.35rem;
            color: #4ade80;
            margin-bottom: 0.25rem;
            font-weight: 700;
        }}
        .sub {{ color: #a0a0a0; font-size: 0.9rem; margin-bottom: 1.25rem; }}
        .status-bar {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 12px;
            margin-bottom: 1.5rem;
        }}
        .stat {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 14px 16px;
        }}
        .stat-label {{
            display: block;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            color: #a0a0a0;
            margin-bottom: 6px;
        }}
        .stat .val {{
            font-family: "JetBrains Mono", ui-monospace, monospace;
            font-size: 0.95rem;
            color: #a7f3d0;
            word-break: break-all;
        }}
        .stat .val.warn {{ color: #fbbf24; }}
        .stat .val.err {{ color: #f87171; }}
        .panel {{
            background: #1a1a2e;
            border: 1px solid #2a2a4a;
            border-radius: 14px;
            padding: 1.25rem 1.5rem;
            margin-bottom: 1.25rem;
        }}
        .panel h2 {{ font-size: 0.85rem; color: #94a3b8; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }}
        label.f {{ display: block; font-size: 0.8rem; color: #94a3b8; margin-bottom: 6px; }}
        textarea, input[type="text"], input[type="password"] {{
            width: 100%;
            background: #12122a;
            border: 1px solid #2a2a4a;
            border-radius: 8px;
            color: #e0e0e0;
            padding: 10px 12px;
            font-family: "JetBrains Mono", ui-monospace, monospace;
            font-size: 0.85rem;
        }}
        textarea {{ min-height: 100px; resize: vertical; }}
        .row {{ margin-bottom: 14px; }}
        .hint {{ font-size: 0.75rem; color: #94a3b8; margin-top: 4px; }}
        button.primary {{
            background: linear-gradient(135deg, #4ade80, #22d3ee);
            color: #0f0f1a;
            border: none;
            padding: 12px 28px;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
            font-size: 1rem;
        }}
        button.primary:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .banner {{
            padding: 12px 14px;
            border-radius: 8px;
            margin-bottom: 1rem;
            font-size: 0.9rem;
        }}
        .banner.info {{ background: #1e3a5f; border: 1px solid #3b82f6; color: #bfdbfe; }}
        .banner.bad {{ background: #3f1d1d; border: 1px solid #ef4444; color: #fecaca; }}
        .candidates {{ display: grid; gap: 12px; }}
        .card {{
            background: #12122a;
            border: 1px solid #2a2a4a;
            border-radius: 10px;
            padding: 14px;
        }}
        .card h3 {{
            font-family: "JetBrains Mono", monospace;
            font-size: 0.8rem;
            color: #22d3ee;
            margin-bottom: 8px;
        }}
        .card .body {{ white-space: pre-wrap; word-break: break-word; font-size: 0.85rem; }}
        .card .body.err {{ color: #f87171; }}
        .badge {{
            display: inline-block;
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 4px;
            background: #f59e0b33;
            color: #fcd34d;
            margin-left: 8px;
            vertical-align: middle;
        }}
        .final {{
            border-left: 3px solid #4ade80;
            padding-left: 14px;
            margin-top: 8px;
        }}
        .final .answer {{ font-size: 0.95rem; margin: 8px 0; white-space: pre-wrap; }}
        .final .meta {{ font-size: 0.8rem; color: #94a3b8; font-family: "JetBrains Mono", monospace; }}
        code {{ background: #2a2a4a; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
        a {{ color: #22d3ee; }}
    </style>
</head>
<body>
    <div class="wrap">
        <header>
            <h1>AI Gateway — Ensemble + Judge</h1>
            <p class="sub">Test <code>POST /api/ensemble</code> from the browser. Configure the server with <code>docs/ai-gateway-ensemble.md</code>.</p>
        </header>
        <main>
            <div id="loadErr" class="banner bad" role="alert" aria-live="assertive" style="display:none"></div>
            <div class="status-bar" id="statusBar" aria-live="polite"></div>
            <section class="panel" aria-label="Ensemble request">
                <h2>Request</h2>
                <div class="row">
                    <label class="f" for="apiKey">REST API key (optional)</label>
                    <input type="password" id="apiKey" autocomplete="off" placeholder="Bearer token if MCP_API_KEYS is set">
                    <p class="hint">Stored only in <code>sessionStorage</code> for this tab (cleared when the tab closes). Use <code>X-API-Key</code> alternative below if you prefer.</p>
                </div>
                <div class="row">
                    <label class="f" for="apiKeyHeader">Or X-API-Key (optional)</label>
                    <input type="password" id="apiKeyHeader" autocomplete="off" placeholder="Same key sent as X-API-Key header">
                </div>
                <div class="row">
                    <label class="f" for="task">Task</label>
                    <textarea id="task" placeholder="User prompt for all candidate models…"></textarea>
                </div>
                <div class="row">
                    <label class="f" for="models">Models (comma or one per line; optional if ENSEMBLE_MODELS is set)</label>
                    <textarea id="models" style="min-height:64px" placeholder="gpt-4o-mini, claude-3-haiku…"></textarea>
                </div>
                <div class="row">
                    <label class="f" for="judge">Judge model (optional)</label>
                    <input type="text" id="judge" placeholder="Default from JUDGE_MODEL env">
                </div>
                <button type="button" class="primary" id="runBtn">Run ensemble</button>
            </section>
            <section class="panel" id="resultPanel" aria-label="Ensemble result" aria-live="polite" aria-busy="false" style="display:none">
                <h2>Result <span id="partialBadge" class="badge" style="display:none">partial</span></h2>
                <div id="finalBlock"></div>
                <h2 style="margin-top:1.25rem">Candidates</h2>
                <div class="candidates" id="candidates"></div>
            </section>
        </main>
    </div>
    <script type="module">{_AI_GATEWAY_INLINE_SCRIPT}</script>
</body>
</html>"""
    return HTMLResponse(
        html,
        headers={"Cache-Control": "public, max-age=300, must-revalidate"},
    )


async def stripe_webhook(request: Request) -> JSONResponse:
    """POST /webhooks/stripe — Verify signature and persist subscription state."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        return error_response(
            ErrorCode.SERVICE_UNAVAILABLE,
            "Stripe webhook is not configured on this server",
            status=503,
            details={"hint": "Set STRIPE_WEBHOOK_SECRET"},
        )

    outcome, err = await billing_service.process_signed_payload(
        body=await request.body(),
        signature=request.headers.get("stripe-signature"),
        secret=secret,
    )
    if err == "invalid_signature":
        return error_response(
            ErrorCode.INVALID_SIGNATURE,
            "Stripe-Signature header did not match the payload",
            status=400,
        )
    if err == "invalid_json":
        return error_response(
            ErrorCode.INVALID_JSON,
            "Webhook body is not valid JSON",
            status=400,
        )
    assert outcome is not None  # err is None → outcome populated
    if outcome.processed:
        return JSONResponse({"received": True, "type": outcome.event_type})
    return JSONResponse({"received": True, "ignored": outcome.event_type or True})


async def bookmarklet_page(request: Request) -> HTMLResponse:
    """GET /bookmarklet — Installation page with the bookmarklet link."""
    host = request.headers.get("host", "localhost:8000")
    scheme = request.url.scheme

    bookmarklet_js = (
        f"javascript:void("
        f"fetch('{scheme}://{host}/api/save',"
        f"{{method:'POST',headers:{{'Content-Type':'application/json'}},"
        f"body:JSON.stringify({{url:location.href}})}}"
        f").then(r=>r.json()).then(d=>"
        f"{{let n=document.createElement('div');"
        f"n.innerHTML='<div style=\"position:fixed;top:20px;right:20px;z-index:99999;"
        f"background:#1a1a2e;color:#e0e0e0;padding:16px 24px;border-radius:12px;"
        f"font-family:system-ui;font-size:14px;box-shadow:0 4px 20px rgba(0,0,0,0.3);"
        f"max-width:360px\">'+"
        f"'<strong style=\"color:#4ade80\">✓ Bookmarked!</strong><br>'+"
        f"(d.title||d.url)+'<br>'+"
        f"'<small style=\"color:#888\">'+d.word_count+' words extracted</small>'+"
        f"'</div>';"
        f"document.body.appendChild(n);"
        f"setTimeout(()=>n.remove(),3000)}}"
        f").catch(e=>alert('Bookmark save failed: '+e))"
        f")"
    )

    canonical = f"{scheme}://{host}/bookmarklet"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Install the MCP Bookmarks bookmarklet: drag the button to your bookmarks bar and save any page to your AI-tagged knowledge base with one click.">
    <meta name="theme-color" content="#0f0f1a">
    <title>Install the MCP Bookmarks bookmarklet</title>
    <link rel="canonical" href="{canonical}">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            background: #0f0f1a;
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
        }}
        .card {{
            background: #1a1a2e;
            border: 1px solid #2a2a4a;
            border-radius: 16px;
            padding: 3rem;
            max-width: 600px;
            width: 100%;
        }}
        h1 {{ color: #4ade80; margin-bottom: 0.5rem; font-size: 1.5rem; }}
        .subtitle {{ color: #a0a0a0; margin-bottom: 2rem; }}
        .bookmarklet-link {{
            display: inline-block;
            background: linear-gradient(135deg, #4ade80, #22d3ee);
            color: #0f0f1a;
            padding: 12px 28px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 700;
            font-size: 1.1rem;
            cursor: grab;
            margin: 1.5rem 0;
            transition: transform 0.1s;
        }}
        .bookmarklet-link:hover {{ transform: scale(1.05); }}
        .bookmarklet-link:focus-visible {{ outline: 3px solid #4ade80; outline-offset: 2px; }}
        .instructions {{
            background: #12122a;
            border-radius: 8px;
            padding: 1.5rem;
            margin-top: 1.5rem;
            line-height: 1.8;
        }}
        .instructions li {{ margin-left: 1.5rem; }}
        code {{
            background: #2a2a4a;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.9em;
        }}
        .server-info {{
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 1px solid #2a2a4a;
            color: #a0a0a0;
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <main aria-label="Bookmarklet installation" class="card">
        <h1>📚 MCP Bookmarks</h1>
        <p class="subtitle">Save any page to your AI knowledge base with one click.</p>

        <p>Drag this button to your bookmarks bar:</p>

        <a class="bookmarklet-link" rel="nofollow" aria-label="Save to MCP bookmarklet (drag to bookmarks bar)" href="{bookmarklet_js}">
            📌 Save to MCP
        </a>

        <section class="instructions" aria-label="Installation steps">
            <strong>How to install:</strong>
            <ol>
                <li>Make sure your bookmarks bar is visible</li>
                <li><strong>Drag</strong> the green button above into your bookmarks bar</li>
                <li>Visit any page and click <strong>"📌 Save to MCP"</strong></li>
                <li>The page URL, title, and full article text are saved automatically</li>
                <li>Open Claude and use the MCP tools to tag and summarize</li>
            </ol>
        </section>

        <footer class="server-info">
            Server: <code>{scheme}://{host}</code><br>
            API: <code>POST /api/save</code> &middot;
            <code>GET /api/bookmarks</code> &middot;
            <code>GET /api/tags</code> &middot;
            <code>GET /api/stats</code> &middot;
            <code>GET /api/bookmarks/&lt;id&gt;</code> &middot;
            <code>POST /api/tag</code>
        </footer>
    </main>
</body>
</html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "private, max-age=600",
            "Vary": "Host",
        },
    )


# ── Build the Starlette app ──────────────────────────────────────


def create_api_app() -> Starlette:
    """Create the REST API Starlette application (mounted at ``/api``)."""
    app = Starlette(
        routes=[
            Route("/save", api_save, methods=["POST"]),
            Route("/stats", api_stats, methods=["GET"]),
            Route("/bookmarks/{bookmark_id}/summary", api_bookmark_summary, methods=["POST"]),
            Route("/bookmarks/{bookmark_id}/tags", api_bookmark_tags, methods=["POST"]),
            Route("/bookmarks/{bookmark_id}", api_get_bookmark, methods=["GET"]),
            Route("/bookmarks", api_bookmarks, methods=["GET"]),
            Route("/tag", api_create_tag_rest, methods=["POST"]),
            Route("/tags", api_tags, methods=["GET"]),
            Route("/usage", api_usage, methods=["GET"]),
            Route("/capabilities", api_capabilities, methods=["GET"]),
            Route("/ai-gateway/status", api_ai_gateway_status, methods=["GET"]),
            Route("/ensemble", api_ensemble, methods=["POST"]),
        ],
    )
    # CORS is applied at the parent app (create_combined_app in server.py) so
    # all routes — /mcp, /sse, /api/*, /health — share one origin policy. No
    # inner CORSMiddleware here.
    if api_keys_configured():
        app.add_middleware(TenantAuthMiddleware)
    return app
