"""CrewAI enrichment: save URL via REST, then tag + summarize using REST tools."""

from __future__ import annotations

import json

import httpx

from .api_base_util import rest_api_prefix
from .rest_crew_tools import make_bookmarks_rest_tools

try:
    from crewai import Agent, Crew, Process, Task
except ImportError:
    Agent = Crew = Process = Task = None  # type: ignore[assignment,misc]


def _save_url(api_base: str, url: str, api_key: str | None) -> tuple[str | None, str]:
    """POST /save; return (bookmark_id_str, error_message)."""
    base = rest_api_prefix(api_base)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = httpx.post(f"{base}/save", json={"url": url}, headers=headers, timeout=120)
    except httpx.HTTPError as e:
        return None, str(e)
    if not r.is_success:
        return None, f"HTTP {r.status_code}: {r.text[:800]}"
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None, r.text[:800]
    bid = data.get("bookmark_id")
    if bid is None:
        return None, "missing bookmark_id in response"
    return str(bid), ""


def _is_already_enriched(api_base: str, bookmark_id: str, api_key: str | None) -> bool:
    """Return True when the bookmark already has both tags and a summary."""
    base = rest_api_prefix(api_base)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = httpx.get(f"{base}/bookmarks/{bookmark_id}", headers=headers, timeout=60)
        if not r.is_success:
            return False
        data = r.json()
    except Exception:
        return False
    has_summary = bool(data.get("summary"))
    has_tags = bool(data.get("tags"))
    return has_summary and has_tags


def run_enrichment_crew(
    url: str,
    api_base: str,
    api_key: str | None = None,
    *,
    force: bool = False,
) -> str:
    """Save ``url``, then run Librarian + Editor agents (requires ``uv sync --extra crew`` + LLM API key).

    Skips enrichment if the bookmark already has tags and a summary, unless *force* is True.
    """
    if Agent is None:
        raise ImportError("CrewAI is not installed. Run: uv sync --extra crew")

    bid, err = _save_url(api_base, url, api_key)
    if not bid:
        return f"save_bookmark failed: {err}"

    if not force and _is_already_enriched(api_base, bid, api_key):
        return f"bookmark_id={bid} already enriched (has tags + summary). Use --force to re-enrich."

    tools = make_bookmarks_rest_tools(api_base, api_key)
    t_list, t_create, t_assign, t_load, t_summary, _t_fetch = tools

    try:
        librarian = Agent(
            role="Taxonomy librarian",
            goal="Reuse existing tags from the API; create a new tag only when nothing fits.",
            backstory=(
                "You call tools to read tags, optionally create one precise tag, "
                "then assign 1-5 slugs to the bookmark. Never invent slugs that were not created or listed."
            ),
            tools=[t_list, t_create, t_assign],
            verbose=False,
            allow_delegation=False,
            max_iter=15,
        )

        editor = Agent(
            role="Technical editor",
            goal="Write a concise accurate summary from the bookmark body returned by the API.",
            backstory=(
                "You load the bookmark JSON, read title/url/content, and save a 2-4 sentence summary. "
                "If content is empty, summarize from title and URL only."
            ),
            tools=[t_load, t_summary],
            verbose=False,
            allow_delegation=False,
            max_iter=10,
        )

        task_tags = Task(
            description=(
                f"Bookmark id is **{bid}** (already saved).\n"
                "1) Call List taxonomy tags.\n"
                "2) Pick existing slugs that fit the URL/topic, or create **at most one** new tag with "
                "clear slug/name/description if needed.\n"
                "3) Call Assign tags to bookmark with bookmark_id and comma-separated slugs.\n"
                "Report which slugs you applied."
            ),
            expected_output="Bullet list: tags applied and any tag created.",
            agent=librarian,
        )

        task_summary = Task(
            description=(
                f"Bookmark id is **{bid}**.\n"
                "1) Load bookmark JSON.\n"
                "2) Write a short faithful summary (no fabrication beyond the text).\n"
                "3) Call Save bookmark summary with the same bookmark id and your summary text."
            ),
            expected_output="The summary text you stored (repeat it here).",
            agent=editor,
        )

        crew = Crew(
            agents=[librarian, editor],
            tasks=[task_tags, task_summary],
            process=Process.sequential,
            verbose=False,
        )
        out = str(crew.kickoff())
    except Exception as e:
        return f"bookmark_id={bid}\n\nCrew execution failed: {e}"
    return f"bookmark_id={bid}\n\n{out}"
