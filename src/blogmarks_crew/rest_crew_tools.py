"""httpx-backed CrewAI tools for the running mcp-bookmarks REST API (SQLite mode)."""

from __future__ import annotations

import json
from typing import Callable

import httpx
from crewai.tools import tool

from .api_base_util import rest_api_prefix


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

    return [
        list_taxonomy_tags,
        create_taxonomy_tag,
        assign_tags,
        load_bookmark,
        save_summary,
    ]
