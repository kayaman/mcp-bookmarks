"""Optional CrewAI workflow: suggest frequent themes from a URL list (requires [crew] extra)."""

from __future__ import annotations


def run_topic_crew(urls: list[str]) -> str:
    """Use a tiny Crew to summarize cross-cutting topics (needs LLM API key from env)."""
    try:
        from crewai import Agent, Crew, Task
    except ImportError as e:
        raise ImportError(
            "CrewAI is not installed. Run: uv sync --extra crew"
        ) from e

    if not urls:
        return "No URLs provided."

    url_block = "\n".join(f"- {u}" for u in urls[:50])
    if len(urls) > 50:
        url_block += f"\n... and {len(urls) - 50} more (truncated for the agent)"

    analyst = Agent(
        role="Reading list analyst",
        goal="Identify recurring themes across the given URLs for a future article outline.",
        backstory=(
            "You synthesize reading lists. You output concise topic clusters, "
            "not full articles. You do not invent URLs."
        ),
        verbose=False,
        allow_delegation=False,
        max_iter=10,
    )

    task = Task(
        description=(
            "Given this list of saved article URLs (titles unknown), "
            "propose 3-7 **topic clusters** that might unite several of them, "
            "and one sentence each on why they matter. "
            "If the list is heterogeneous, say so. "
            "Do not fetch URLs; work from domains and path hints only.\n\n"
            f"{url_block}"
        ),
        expected_output="Markdown: ## Topic clusters with short rationale each.",
        agent=analyst,
    )

    crew = Crew(agents=[analyst], tasks=[task], verbose=False)
    return str(crew.kickoff())
