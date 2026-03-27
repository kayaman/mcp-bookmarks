"""CLI: batch REST ingest + optional CrewAI topic clustering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from .ingest import ingest_urls, load_urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="blogmarks-crew",
        description="Blogmarks batch tools: REST ingest and optional CrewAI agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_p = sub.add_parser(
        "ingest",
        help="POST each URL to mcp-bookmarks POST /api/save (server must be running).",
    )
    ingest_p.add_argument(
        "--urls-file",
        type=Path,
        required=True,
        help="Text file: one URL per line; lines starting with # ignored.",
    )
    ingest_p.add_argument(
        "--api-base",
        default="http://127.0.0.1:8000",
        help="Base URL of mcp-bookmarks (default: http://127.0.0.1:8000).",
    )
    ingest_p.add_argument(
        "--api-key",
        default=None,
        help="Bearer token when MCP_API_KEYS is configured on the server.",
    )

    agents_p = sub.add_parser(
        "agents",
        help="Run CrewAI to cluster topics from URLs (uv sync --extra crew; LLM API key required).",
    )
    agents_p.add_argument(
        "--urls-file",
        type=Path,
        required=True,
        help="Text file: one URL per line.",
    )

    enrich_p = sub.add_parser(
        "enrich",
        help="POST /api/save then CrewAI (librarian+editor) to tag and summarize via REST tools.",
    )
    enrich_p.add_argument("--url", required=True, help="URL to save and enrich.")
    enrich_p.add_argument(
        "--api-base",
        default="http://127.0.0.1:8000",
        help="Server origin; /api is appended if missing (default: http://127.0.0.1:8000).",
    )
    enrich_p.add_argument(
        "--api-key",
        default=None,
        help="Bearer token when MCP_API_KEYS is set on the server.",
    )

    topics_p = sub.add_parser(
        "suggest-topics",
        help="CrewAI: topic slug ideas from one bookmark (GET /api/bookmarks/<id>).",
    )
    topics_p.add_argument("--bookmark-id", required=True, help="Bookmark id (integer in SQLite).")
    topics_p.add_argument(
        "--api-base",
        default="http://127.0.0.1:8000",
        help="Server origin; /api appended if missing.",
    )
    topics_p.add_argument("--api-key", default=None, help="Bearer token if REST is protected.")

    args = parser.parse_args(argv)

    if args.command == "ingest":
        urls = load_urls(args.urls_file)
        if not urls:
            print("No URLs in file.", file=sys.stderr)
            return 1
        ok, fail = ingest_urls(urls, args.api_base, api_key=args.api_key)
        print(f"Done: {ok} ok, {fail} failed (total {len(urls)})")
        return 0 if fail == 0 else 2

    if args.command == "agents":
        from .crew_pipeline import run_topic_crew

        urls = load_urls(args.urls_file)
        if not urls:
            print("No URLs in file.", file=sys.stderr)
            return 1
        try:
            out = run_topic_crew(urls)
        except ImportError as e:
            print(e, file=sys.stderr)
            return 1
        print(out)
        return 0

    if args.command == "enrich":
        from .crew_enrich import run_enrichment_crew

        try:
            out = run_enrichment_crew(args.url, args.api_base, api_key=args.api_key)
        except ImportError as e:
            print(e, file=sys.stderr)
            return 1
        print(out)
        return 0

    if args.command == "suggest-topics":
        from .crew_topics import run_topic_suggest_crew

        try:
            out = run_topic_suggest_crew(args.bookmark_id, args.api_base, api_key=args.api_key)
        except ImportError as e:
            print(e, file=sys.stderr)
            return 1
        except httpx.HTTPError as e:
            print(e, file=sys.stderr)
            return 1
        print(out)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
