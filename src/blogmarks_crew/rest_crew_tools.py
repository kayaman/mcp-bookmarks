"""httpx-backed CrewAI tools for the running mcp-bookmarks REST API."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable

import httpx
from crewai.tools import tool

from .api_base_util import rest_api_prefix

_RUST_BINARY = "blogmarks-fetch"
_RUST_BINARY_PATHS = [
    "rust/blogmarks-fetch/target/release/blogmarks-fetch",
    "rust/blogmarks-fetch/target/debug/blogmarks-fetch",
]


def _find_rust_binary() -> str | None:
    """Locate the blogmarks-fetch binary (PATH first, then known build dirs)."""
    found = shutil.which(_RUST_BINARY)
    if found:
        return found
    from pathlib import Path

    for rel in _RUST_BINARY_PATHS:
        p = Path(rel)
        if p.is_file():
            return str(p.resolve())
    return None


def make_bookmarks_rest_tools(
    api_base: str,
    api_key: str | None = None,
) -> list[Callable]:
    """Build CrewAI @tool callables bound to the REST API (``/api`` mount)."""
    base = rest_api_prefix(api_base)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    @tool("List taxonomy tags")
    def list_taxonomy_tags() -> str:
        """Return JSON from GET /tags: all canonical tags with slug, name, description."""
        r = httpx.get(f"{base}/tags", headers=headers, timeout=60)
        return r.text

    @tool("Create a new tag")
    def create_taxonomy_tag(slug: str, name: str, description: str) -> str:
        """Create tag with slug (lowercase-hyphens), human name, and scope description. Fails if slug exists."""
        r = httpx.post(
            f"{base}/tag",
            headers=headers,
            json={"slug": slug.strip(), "name": name.strip(), "description": description.strip()},
            timeout=60,
        )
        return r.text

    @tool("Assign tags to bookmark")
    def assign_tags(bookmark_id: str, tag_slugs_comma_separated: str) -> str:
        """Assign existing tags only. tag_slugs_comma_separated: e.g. 'python,machine-learning'."""
        slugs = [s.strip() for s in tag_slugs_comma_separated.split(",") if s.strip()]
        r = httpx.post(
            f"{base}/bookmarks/{bookmark_id.strip()}/tags",
            headers=headers,
            json={"tag_slugs": slugs},
            timeout=60,
        )
        return r.text

    @tool("Load bookmark JSON")
    def load_bookmark(bookmark_id: str) -> str:
        """GET one bookmark including title, url, content, tags, summary."""
        r = httpx.get(f"{base}/bookmarks/{bookmark_id.strip()}", headers=headers, timeout=120)
        return r.text

    @tool("Save bookmark summary")
    def save_summary(bookmark_id: str, summary_text: str) -> str:
        """Store an AI-written summary (plain text) for the bookmark."""
        r = httpx.post(
            f"{base}/bookmarks/{bookmark_id.strip()}/summary",
            headers=headers,
            json={"summary": summary_text},
            timeout=60,
        )
        return r.text

    @tool("Fetch URL content")
    def fetch_url_content(url: str) -> str:
        """Fetch a URL's HTML and return JSON with ok, status, html_bytes, title.

        Uses the Rust blogmarks-fetch binary when available, otherwise falls
        back to httpx.
        """
        rust_bin = _find_rust_binary()
        if rust_bin:
            try:
                proc = subprocess.run(
                    [rust_bin, url],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if proc.stdout.strip():
                    return proc.stdout.strip()
            except (subprocess.TimeoutExpired, OSError):
                pass

        try:
            r = httpx.get(url, timeout=60, follow_redirects=True)
            title = None
            if r.status_code < 400:
                import re
                m = re.search(r"(?is)<title[^>]*>([^<]{1,2000})</title>", r.text)
                if m:
                    title = " ".join(m.group(1).split())
            return json.dumps({
                "url": url,
                "ok": r.status_code < 400,
                "status": r.status_code,
                "html_bytes": len(r.content),
                "title": title,
                "error": None,
                "source": "httpx",
            })
        except httpx.HTTPError as e:
            return json.dumps({
                "url": url,
                "ok": False,
                "status": 0,
                "html_bytes": 0,
                "title": None,
                "error": str(e),
                "source": "httpx",
            })

    return [
        list_taxonomy_tags,
        create_taxonomy_tag,
        assign_tags,
        load_bookmark,
        save_summary,
        fetch_url_content,
    ]
