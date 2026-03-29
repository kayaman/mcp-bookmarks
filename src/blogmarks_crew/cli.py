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
    ingest_p.add_argument(
        "--force",
        action="store_true",
        help="Re-save URLs even if they already exist (default: upsert is already idempotent).",
    )
    ingest_p.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Process URLs in batches of this size (default: 10).",
    )
    ingest_p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between batches (default: 1.0).",
    )
    ingest_p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Estimated max USD cost; warns and asks for confirmation before proceeding.",
    )
    ingest_p.add_argument(
        "--failures-file",
        type=Path,
        default=None,
        help="Append failed URLs to this file for later retry.",
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
    enrich_p.add_argument(
        "--force",
        action="store_true",
        help="Re-enrich even if the bookmark already has tags and a summary.",
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

        if args.max_cost is not None:
            est_cost_per_item = 0.003
            est_total = len(urls) * est_cost_per_item
            if est_total > args.max_cost:
                print(
                    f"Estimated cost ~${est_total:.2f} ({len(urls)} items × ~${est_cost_per_item}/item) "
                    f"exceeds --max-cost ${args.max_cost:.2f}. Aborting.",
                    file=sys.stderr,
                )
                return 1
            print(f"[cost] estimated ~${est_total:.2f} for {len(urls)} items (limit: ${args.max_cost:.2f})")

        ok, fail = ingest_urls(
            urls,
            args.api_base,
            api_key=args.api_key,
            batch_size=args.batch_size,
            delay=args.delay,
            failures_file=args.failures_file,
        )
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
            out = run_enrichment_crew(args.url, args.api_base, api_key=args.api_key, force=args.force)
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
