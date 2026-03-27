"""MVP: propose normalized topic slugs from one bookmark's text (CrewAI + REST GET)."""

from __future__ import annotations

import json

import httpx

from .api_base_util import rest_api_prefix


def _fetch_bookmark(api_base: str, bookmark_id: str, api_key: str | None) -> dict:
    base = rest_api_prefix(api_base)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = httpx.get(f"{base}/bookmarks/{bookmark_id}", headers=headers, timeout=120)
    r.raise_for_status()
    return r.json()


def run_topic_suggest_crew(bookmark_id: str, api_base: str, api_key: str | None = None) -> str:
    """Return Markdown topic suggestions from bookmark content (requires [crew] + LLM env)."""
    try:
        from crewai import Agent, Crew, Task
    except ImportError as e:
        raise ImportError("CrewAI is not installed. Run: uv sync --extra crew") from e

    data = _fetch_bookmark(api_base, bookmark_id, api_key)
    title = data.get("title") or ""
    url = data.get("url") or ""
    content = (data.get("content") or "")[:120_000]
    blob = json.dumps(
        {"title": title, "url": url, "content_excerpt": content[:50_000]},
        ensure_ascii=False,
    )

    analyst = Agent(
        role="Topic analyst",
        goal="Propose 5-12 short topic slugs (kebab-case) for clustering this bookmark.",
        backstory=(
            "Output only analysis — do not claim to call external APIs. "
            "Slugs must be lowercase hyphenated (e.g. rust-async, postgresql)."
        ),
        verbose=False,
        allow_delegation=False,
    )

    task = Task(
        description=(
            "Given this JSON with title, url, and content excerpt, list:\n"
            "## Candidate topic slugs\n"
            "- slug — one line rationale\n"
            "Avoid duplicates; prefer reusable library/framework names.\n\n"
            f"{blob}"
        ),
        expected_output="Markdown with ## Candidate topic slugs",
        agent=analyst,
    )

    crew = Crew(agents=[analyst], tasks=[task], verbose=False)
    return str(crew.kickoff())
