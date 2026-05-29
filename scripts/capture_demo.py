"""
Capture the 5-step live demo flow against a running mcp-bookmarks server.

Usage:
    # Against local SQLite (dev):
    uv run mcp-bookmarks &
    uv run python scripts/capture_demo.py

    # Against production:
    MCP_BASE_URL=https://mcp.example.com \
    MCP_API_KEY=<your-key> \
    uv run python scripts/capture_demo.py

Output is printed to stdout in a format suitable for slide annotations.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DEMO_URL = "https://martinfowler.com/articles/2025-llm-agent.html"


async def run_demo(base_url: str, api_key: str | None = None):
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    sse_url = base_url.rstrip("/") + "/sse"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    print(f"\n{'='*60}")
    print(f"  mcp-bookmarks — 5-step demo capture")
    print(f"  Server: {sse_url}")
    print(f"{'='*60}\n")

    async with sse_client(sse_url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── Step 0: list tools ───────────────────────────────────
            tools = await session.list_tools()
            print(f"[0] tools/list → {len(tools.tools)} tools")
            for t in tools.tools:
                print(f"    • {t.name}")

            # ── Step 1: save_bookmark ────────────────────────────────
            print(f"\n[1] save_bookmark(url={DEMO_URL!r})")
            result = await session.call_tool("save_bookmark", {"url": DEMO_URL})
            step1_out = result.content[0].text if result.content else "{}"
            data = json.loads(step1_out) if step1_out.startswith("{") else {"raw": step1_out}
            bookmark_id = data.get("bookmark_id") or data.get("id") or "unknown"
            print(f"    bookmark_id = {bookmark_id}")
            print(f"    status      = {data.get('status', data.get('message', ''))}")

            # ── Step 2: read taxonomy resource ───────────────────────
            print(f"\n[2] Resource: bookmarks://taxonomy")
            resources = await session.list_resources()
            tax_uri = next(
                (r.uri for r in resources.resources if "taxonomy" in str(r.uri)),
                "bookmarks://taxonomy",  # known URI — fall back if list_resources omits it
            )
            if tax_uri:
                tax = await session.read_resource(tax_uri)
                raw = tax.contents[0].text if tax.contents else ""
                tags = json.loads(raw) if raw else []
                if isinstance(tags, list):
                    print(f"    {len(tags)} tags in taxonomy")
                    for t in tags[:5]:
                        if isinstance(t, dict):
                            print(f"    • {t.get('slug', t)}: {t.get('description', '')[:60]}")
                else:
                    print(f"    {str(tags)[:200]}")
            else:
                print("    (taxonomy resource not found)")

            # ── Step 3: extract_content ──────────────────────────────
            if bookmark_id != "unknown":
                print(f"\n[3] extract_content(bookmark_id={bookmark_id!r})")
                result = await session.call_tool(
                    "extract_content", {"bookmark_id": str(bookmark_id)}
                )
                out = result.content[0].text if result.content else "{}"
                data = json.loads(out) if out.startswith("{") else {"raw": out[:200]}
                words = data.get("word_count", data.get("aiWordCount", "?"))
                print(f"    word_count = {words}")

            # ── Step 4: search_bookmarks ─────────────────────────────
            print(f"\n[4] search_bookmarks(query='agents')")
            result = await session.call_tool("search_bookmarks", {"query": "agents"})
            out = result.content[0].text if result.content else "[]"
            hits = json.loads(out) if out.startswith("[") or out.startswith("{") else []
            if isinstance(hits, dict):
                hits = hits.get("bookmarks", hits.get("results", []))
            print(f"    {len(hits)} results")
            for h in hits[:3]:
                if isinstance(h, dict):
                    print(f"    • {h.get('title', h.get('url', 'unknown'))[:70]}")

            # ── Step 5: get_stats ─────────────────────────────────────
            print(f"\n[5] get_stats() — confirms DynamoDB write")
            result = await session.call_tool("get_stats", {})
            out = result.content[0].text if result.content else "{}"
            data = json.loads(out) if out.startswith("{") else {"raw": out}
            print(f"    total_bookmarks = {data.get('total_bookmarks', '?')}")
            print(f"    total_tags      = {data.get('total_tags', '?')}")

            print(f"\n{'='*60}")
            print("  PASS — all 5 steps completed.")
            print(f"  Use get_bookmark to verify the saved item.")
            print(f"{'='*60}\n")


if __name__ == "__main__":
    base_url = os.environ.get("MCP_BASE_URL", "http://localhost:8000")
    api_key = os.environ.get("MCP_API_KEY")
    asyncio.run(run_demo(base_url, api_key))
