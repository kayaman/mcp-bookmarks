#!/usr/bin/env python3
"""
CLI client for the MCP Bookmarks server.

Quick bookmark operations directly from your terminal without
needing a full MCP client. Talks to the SQLite DB directly.

Usage:
    # Save a URL (extract OG + content)
    python -m mcp_bookmarks.cli save https://example.com/article

    # List recent bookmarks
    python -m mcp_bookmarks.cli list

    # Search bookmarks
    python -m mcp_bookmarks.cli search "machine learning"

    # List all tags
    python -m mcp_bookmarks.cli tags

    # Show stats
    python -m mcp_bookmarks.cli stats

    # Show full bookmark details
    python -m mcp_bookmarks.cli show 42
"""

import asyncio
import argparse
import os
import sys
from pathlib import Path

from .db import Database, DEFAULT_DB_PATH
from .scraper import extract_og_metadata, extract_article_content


def get_db() -> Database:
    db_path = Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))
    return Database(db_path)


async def cmd_save(args):
    """Save a URL: extract OG metadata and optionally article content."""
    db = get_db()
    await db.connect()

    print(f"⏳ Fetching metadata for {args.url}...")
    try:
        og = await extract_og_metadata(args.url)
        print(f"   Title: {og.title or '(none)'}")
        print(f"   Description: {(og.description or '(none)')[:100]}")
        print(f"   Site: {og.site_name or '(none)'}")
    except Exception as e:
        print(f"   ⚠ OG extraction failed: {e}")
        og = None

    bookmark = await db.upsert_bookmark(
        url=args.url,
        title=og.title if og else None,
        description=og.description if og else None,
        image_url=og.image if og else None,
        site_name=og.site_name if og else None,
    )
    print(f"   ✓ Saved bookmark id={bookmark.id}")

    if not args.no_content:
        print(f"⏳ Extracting article content...")
        try:
            article = await extract_article_content(args.url)
            await db.set_bookmark_content(bookmark.id, article.text, article.word_count)
            print(f"   ✓ Extracted {article.word_count} words via {article.extraction_method}")
        except Exception as e:
            print(f"   ⚠ Content extraction failed: {e}")

    await db.close()
    print(f"\n✅ Bookmark #{bookmark.id} saved. Connect via MCP to tag and summarize.")


async def cmd_list(args):
    """List recent bookmarks."""
    db = get_db()
    await db.connect()

    bookmarks = await db.search_bookmarks(limit=args.limit)
    if not bookmarks:
        print("No bookmarks yet.")
        await db.close()
        return

    for b in bookmarks:
        tags_str = ", ".join(b.tags) if b.tags else "untagged"
        content_marker = "📄" if b.content else "  "
        summary_marker = "📝" if b.summary else "  "
        print(f"  {content_marker}{summary_marker} [{b.id:>4}] {b.title or b.url}")
        print(f"          {b.url}")
        print(f"          tags: [{tags_str}]")
        if b.summary:
            print(f"          {b.summary[:120]}")
        print()

    print(f"📄 = has content  📝 = has summary  ({len(bookmarks)} shown)")
    await db.close()


async def cmd_search(args):
    """Search bookmarks by text or tag."""
    db = get_db()
    await db.connect()

    if args.tag:
        bookmarks = await db.search_bookmarks(tag=args.tag, limit=args.limit)
        print(f"Bookmarks tagged [{args.tag}]:\n")
    else:
        query = " ".join(args.query)
        bookmarks = await db.search_bookmarks(query=query, limit=args.limit)
        print(f"Search results for '{query}':\n")

    if not bookmarks:
        print("  No results found.")
    else:
        for b in bookmarks:
            tags_str = ", ".join(b.tags) if b.tags else "untagged"
            print(f"  [{b.id:>4}] {b.title or b.url}")
            print(f"         [{tags_str}]")
            if b.summary:
                print(f"         {b.summary[:120]}")
            print()

    await db.close()


async def cmd_tags(args):
    """List all tags."""
    db = get_db()
    await db.connect()

    tags = await db.get_all_tags()
    if not tags:
        print("No tags yet. Save some bookmarks and tag them via MCP.")
        await db.close()
        return

    max_slug = max(len(t.slug) for t in tags)
    max_name = max(len(t.name) for t in tags)

    print(f"{'Slug':<{max_slug}}  {'Name':<{max_name}}  {'Uses':>5}  Description")
    print(f"{'─' * max_slug}  {'─' * max_name}  {'─' * 5}  {'─' * 40}")
    for t in tags:
        desc = t.description[:50] + "..." if len(t.description) > 50 else t.description
        print(f"{t.slug:<{max_slug}}  {t.name:<{max_name}}  {t.usage_count:>5}  {desc}")

    print(f"\n{len(tags)} tags total")
    await db.close()


async def cmd_show(args):
    """Show full details of a bookmark."""
    db = get_db()
    await db.connect()

    bookmark = await db.get_bookmark_by_id(args.id)
    if not bookmark:
        print(f"Bookmark #{args.id} not found.")
        await db.close()
        return

    print(f"═══ Bookmark #{bookmark.id} ═══")
    print(f"  URL:         {bookmark.url}")
    print(f"  Title:       {bookmark.title or '(none)'}")
    print(f"  Site:        {bookmark.site_name or '(none)'}")
    print(f"  Description: {(bookmark.description or '(none)')[:200]}")
    print(f"  Tags:        [{', '.join(bookmark.tags) if bookmark.tags else 'untagged'}]")
    print(f"  Word count:  {bookmark.word_count or 0}")
    print(f"  Created:     {bookmark.created_at}")
    print(f"  Updated:     {bookmark.updated_at}")

    if bookmark.summary:
        print(f"\n  📝 Summary:")
        print(f"  {bookmark.summary}")

    if bookmark.content and args.content:
        print(f"\n  📄 Content (first 2000 chars):")
        print(f"  {bookmark.content[:2000]}")

    await db.close()


async def cmd_stats(args):
    """Show knowledge base statistics."""
    db = get_db()
    await db.connect()
    stats = await db.get_stats()

    print(f"📊 Knowledge Base Stats")
    print(f"   Bookmarks: {stats['total_bookmarks']}")
    print(f"   Tags:      {stats['total_tags']}")

    # Extra: count bookmarks with content/summaries
    cursor = await db.db.execute(
        "SELECT COUNT(*) as c FROM bookmarks WHERE content IS NOT NULL"
    )
    r = await cursor.fetchone()
    print(f"   With content: {r['c']}")

    cursor = await db.db.execute(
        "SELECT COUNT(*) as c FROM bookmarks WHERE summary IS NOT NULL"
    )
    r = await cursor.fetchone()
    print(f"   With summary: {r['c']}")

    await db.close()


def main():
    parser = argparse.ArgumentParser(
        prog="mcp-bookmarks-cli",
        description="CLI for the MCP Bookmarks knowledge base",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # save
    p_save = sub.add_parser("save", help="Save a URL")
    p_save.add_argument("url", help="URL to bookmark")
    p_save.add_argument("--no-content", action="store_true", help="Skip article content extraction")

    # list
    p_list = sub.add_parser("list", help="List recent bookmarks")
    p_list.add_argument("-n", "--limit", type=int, default=10, help="Number of bookmarks")

    # search
    p_search = sub.add_parser("search", help="Search bookmarks")
    p_search.add_argument("query", nargs="*", help="Search query")
    p_search.add_argument("-t", "--tag", help="Filter by tag slug")
    p_search.add_argument("-n", "--limit", type=int, default=20, help="Max results")

    # tags
    sub.add_parser("tags", help="List all tags")

    # show
    p_show = sub.add_parser("show", help="Show bookmark details")
    p_show.add_argument("id", type=int, help="Bookmark ID")
    p_show.add_argument("-c", "--content", action="store_true", help="Show extracted content")

    # stats
    sub.add_parser("stats", help="Show knowledge base stats")

    args = parser.parse_args()

    cmd_map = {
        "save": cmd_save,
        "list": cmd_list,
        "search": cmd_search,
        "tags": cmd_tags,
        "show": cmd_show,
        "stats": cmd_stats,
    }

    asyncio.run(cmd_map[args.command](args))


if __name__ == "__main__":
    main()
