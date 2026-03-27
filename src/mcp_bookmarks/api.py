"""
REST API routes for browser-based bookmark saving.

These lightweight Starlette routes run alongside the MCP SSE server
and provide a simple HTTP interface for the bookmarklet and other
non-MCP clients.

Endpoints:
    POST /api/save      — Quick save a URL (extract OG + content)
    GET  /api/stats     — Knowledge base stats
    GET  /api/bookmarks — List/search bookmarks
    GET  /api/tags      — List all tags
    GET  /bookmarklet   — Bookmarklet installation page
"""

import json
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from starlette.middleware.cors import CORSMiddleware

from .db import Database, DEFAULT_DB_PATH
from .scraper import extract_og_metadata, extract_article_content


def _get_db_path() -> Path:
    return Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))


async def _db():
    db = Database(_get_db_path())
    await db.connect()
    return db


# ── Endpoints ─────────────────────────────────────────────────────


async def api_save(request: Request) -> JSONResponse:
    """POST /api/save — Save a URL with OG extraction.

    Body: {"url": "https://..."} or form-encoded url=...
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
        url = body.get("url")
    else:
        form = await request.form()
        url = form.get("url") or request.query_params.get("url")

    if not url:
        return JSONResponse({"error": "Missing 'url' parameter"}, status_code=400)

    db = await _db()
    try:
        # Extract OG metadata
        try:
            og = await extract_og_metadata(url)
        except Exception:
            from .models import OGMetadata
            og = OGMetadata(url=url)

        bookmark = await db.upsert_bookmark(
            url=og.url,
            title=og.title,
            description=og.description,
            image_url=og.image,
            site_name=og.site_name,
        )

        # Extract content in background-friendly way
        try:
            article = await extract_article_content(url)
            await db.set_bookmark_content(bookmark.id, article.text, article.word_count)
            word_count = article.word_count
        except Exception:
            word_count = 0

        return JSONResponse({
            "status": "saved",
            "bookmark_id": bookmark.id,
            "title": bookmark.title,
            "description": bookmark.description,
            "word_count": word_count,
            "message": f"Saved! Connect via MCP to tag and summarize.",
        })
    finally:
        await db.close()


async def api_stats(request: Request) -> JSONResponse:
    """GET /api/stats — Knowledge base statistics."""
    db = await _db()
    try:
        stats = await db.get_stats()
        return JSONResponse(stats)
    finally:
        await db.close()


async def api_bookmarks(request: Request) -> JSONResponse:
    """GET /api/bookmarks — List or search bookmarks.

    Query params: ?query=..., ?tag=..., ?limit=20
    """
    query = request.query_params.get("query")
    tag = request.query_params.get("tag")
    limit = int(request.query_params.get("limit", "20"))

    db = await _db()
    try:
        bookmarks = await db.search_bookmarks(query=query, tag=tag, limit=limit)
        return JSONResponse({
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
        })
    finally:
        await db.close()


async def api_tags(request: Request) -> JSONResponse:
    """GET /api/tags — List all tags."""
    db = await _db()
    try:
        tags = await db.get_all_tags()
        return JSONResponse({
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
        })
    finally:
        await db.close()


async def bookmarklet_page(request: Request) -> HTMLResponse:
    """GET /bookmarklet — Installation page with the bookmarklet link."""
    host = request.headers.get("host", "localhost:8000")
    scheme = request.url.scheme

    # The bookmarklet JS — minified inline
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

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MCP Bookmarks — Bookmarklet</title>
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
        .subtitle {{ color: #888; margin-bottom: 2rem; }}
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
            color: #666;
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>📚 MCP Bookmarks</h1>
        <p class="subtitle">Save any page to your AI knowledge base with one click.</p>

        <p>Drag this button to your bookmarks bar:</p>

        <a class="bookmarklet-link" href="{bookmarklet_js}">
            📌 Save to MCP
        </a>

        <div class="instructions">
            <strong>How to install:</strong>
            <ol>
                <li>Make sure your bookmarks bar is visible</li>
                <li><strong>Drag</strong> the green button above into your bookmarks bar</li>
                <li>Visit any page and click <strong>"📌 Save to MCP"</strong></li>
                <li>The page URL, title, and full article text are saved automatically</li>
                <li>Open Claude and use the MCP tools to tag and summarize</li>
            </ol>
        </div>

        <div class="server-info">
            Server: <code>{scheme}://{host}</code><br>
            API: <code>POST /api/save</code> &middot;
            <code>GET /api/bookmarks</code> &middot;
            <code>GET /api/tags</code> &middot;
            <code>GET /api/stats</code>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(html)


# ── Build the Starlette app ──────────────────────────────────────


def create_api_app() -> Starlette:
    """Create the REST API Starlette application."""
    app = Starlette(
        routes=[
            Route("/save", api_save, methods=["POST"]),
            Route("/stats", api_stats, methods=["GET"]),
            Route("/bookmarks", api_bookmarks, methods=["GET"]),
            Route("/tags", api_tags, methods=["GET"]),
        ],
    )
    # Enable CORS for bookmarklet cross-origin requests
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    return app
