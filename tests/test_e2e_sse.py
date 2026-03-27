"""
End-to-end SSE integration test.

Starts the MCP Bookmarks server on a random port,
connects via the MCP Python SDK client, and exercises
all tools through the actual protocol.

Run:
    python tests/test_e2e_sse.py
"""

import asyncio
import sys
import os
import tempfile
import signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def find_free_port() -> int:
    """Find a free TCP port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_for_server(port: int, timeout: float = 15.0):
    """Wait until the SSE server is accepting TCP connections."""
    import socket

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            sock.close()
            # Give uvicorn a moment to fully initialize after accepting TCP
            await asyncio.sleep(0.5)
            return
        except (ConnectionRefusedError, OSError):
            pass
        await asyncio.sleep(0.3)
    raise TimeoutError(f"Server on port {port} did not start within {timeout}s")


async def main():
    print("=" * 60)
    print("  MCP Bookmarks — E2E SSE Integration Test")
    print("=" * 60)

    port = await find_free_port()
    db_path = Path(tempfile.mktemp(suffix=".db"))

    # Start server as subprocess
    env = {
        **os.environ,
        "MCP_PORT": str(port),
        "MCP_HOST": "127.0.0.1",
        "BOOKMARKS_DB_PATH": str(db_path),
    }

    print(f"\n⏳ Starting server on port {port}...")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "mcp_bookmarks",
        env=env,
        cwd=str(Path(__file__).parent.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        await wait_for_server(port)
        print(f"✓ Server running on http://127.0.0.1:{port}/sse (pid={proc.pid})")

        # Now connect with the MCP client SDK
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        sse_url = f"http://127.0.0.1:{port}/sse"

        async with sse_client(sse_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                print(f"✓ MCP session initialized")

                # ── List tools ──
                tools_result = await session.list_tools()
                tool_names = [t.name for t in tools_result.tools]
                print(f"✓ Listed {len(tool_names)} tools: {tool_names}")
                assert "save_bookmark" in tool_names
                assert "extract_content" in tool_names
                assert "merge_tags" in tool_names
                assert "untag_bookmark" in tool_names
                assert "semantic_search_bookmarks" in tool_names
                assert "index_bookmark_embedding" in tool_names
                assert "ensemble_with_judge" in tool_names

                # ── get_stats (empty DB) ──
                result = await session.call_tool("get_stats", {})
                import json
                stats = json.loads(result.content[0].text)
                assert stats["total_bookmarks"] == 0
                assert stats["total_tags"] == 0
                print(f"✓ get_stats: {stats}")

                # ── create_tag ──
                result = await session.call_tool("create_tag", {
                    "slug": "python",
                    "name": "Python",
                    "description": "Python programming language",
                })
                data = json.loads(result.content[0].text)
                assert "created" in data
                print(f"✓ create_tag: {data['created']['slug']}")

                result = await session.call_tool("create_tag", {
                    "slug": "web",
                    "name": "Web",
                    "description": "Web technologies and development",
                })
                data = json.loads(result.content[0].text)
                assert "created" in data
                print(f"✓ create_tag: {data['created']['slug']}")

                # ── get_tags ──
                result = await session.call_tool("get_tags", {})
                data = json.loads(result.content[0].text)
                assert data["total"] == 2
                print(f"✓ get_tags: {data['total']} tags")

                # ── get_tags with search ──
                result = await session.call_tool("get_tags", {"query": "python"})
                data = json.loads(result.content[0].text)
                assert data["total"] == 1
                print(f"✓ get_tags(query='python'): {data['total']} result")

                # ── save_bookmark ──
                result = await session.call_tool("save_bookmark", {
                    "url": "https://github.com/modelcontextprotocol/python-sdk",
                })
                data = json.loads(result.content[0].text)
                bookmark_id = data["bookmark_id"]
                assert bookmark_id is not None
                print(f"✓ save_bookmark: id={bookmark_id}, title='{data.get('title', '')[:50]}'")

                # ── tag_bookmark ──
                result = await session.call_tool("tag_bookmark", {
                    "bookmark_id": bookmark_id,
                    "tag_slugs": ["python", "web"],
                })
                data = json.loads(result.content[0].text)
                assert "python" in data["tags"]
                assert "web" in data["tags"]
                print(f"✓ tag_bookmark: {data['tags']}")

                # ── set_summary ──
                result = await session.call_tool("set_summary", {
                    "bookmark_id": bookmark_id,
                    "summary": "Official MCP Python SDK for building servers and clients.",
                })
                data = json.loads(result.content[0].text)
                assert data["status"] == "ok"
                print(f"✓ set_summary: ok")

                # ── read_bookmark ──
                result = await session.call_tool("read_bookmark", {
                    "bookmark_id": bookmark_id,
                })
                data = json.loads(result.content[0].text)
                assert data["summary"] is not None
                assert data["tags"] == ["python", "web"]
                print(f"✓ read_bookmark: tags={data['tags']}, summary='{data['summary'][:50]}...'")

                # ── search_bookmarks ──
                result = await session.call_tool("search_bookmarks", {
                    "tag": "python",
                })
                data = json.loads(result.content[0].text)
                assert data["total"] == 1
                print(f"✓ search_bookmarks(tag='python'): {data['total']} result")

                # ── untag_bookmark ──
                result = await session.call_tool("untag_bookmark", {
                    "bookmark_id": bookmark_id,
                    "tag_slugs": ["web"],
                })
                data = json.loads(result.content[0].text)
                assert "web" not in data["remaining_tags"]
                assert "python" in data["remaining_tags"]
                print(f"✓ untag_bookmark: removed 'web', remaining={data['remaining_tags']}")

                # ── update_tag ──
                result = await session.call_tool("update_tag", {
                    "slug": "python",
                    "new_description": "Python language, frameworks, and ecosystem tools",
                })
                data = json.loads(result.content[0].text)
                assert "updated" in data
                print(f"✓ update_tag: desc='{data['updated']['description'][:40]}...'")

                # ── create + merge_tags ──
                await session.call_tool("create_tag", {
                    "slug": "py",
                    "name": "py",
                    "description": "Shorthand for Python (should be merged)",
                })
                # Tag the bookmark with the duplicate
                await session.call_tool("tag_bookmark", {
                    "bookmark_id": bookmark_id,
                    "tag_slugs": ["py"],
                })
                result = await session.call_tool("merge_tags", {
                    "source_slug": "py",
                    "target_slug": "python",
                })
                data = json.loads(result.content[0].text)
                assert data["source_deleted"] == "py"
                assert data["bookmarks_reassigned"] == 1
                print(f"✓ merge_tags: merged 'py' → 'python', {data['bookmarks_reassigned']} reassigned")

                # Verify py is gone
                result = await session.call_tool("get_tags", {"query": "py"})
                data = json.loads(result.content[0].text)
                slugs = [t["slug"] for t in data["tags"]]
                assert "py" not in slugs
                assert "python" in slugs
                print(f"✓ Verified 'py' tag no longer exists")

                # ── delete_bookmark ──
                result = await session.call_tool("delete_bookmark", {
                    "bookmark_id": bookmark_id,
                })
                data = json.loads(result.content[0].text)
                assert data["status"] == "deleted"
                print(f"✓ delete_bookmark: id={bookmark_id}")

                # Verify empty
                result = await session.call_tool("get_stats", {})
                stats = json.loads(result.content[0].text)
                assert stats["total_bookmarks"] == 0
                print(f"✓ Final stats: {stats}")

                # ── List prompts ──
                prompts_result = await session.list_prompts()
                prompt_names = [p.name for p in prompts_result.prompts]
                print(f"✓ Listed {len(prompt_names)} prompts: {prompt_names}")
                assert "save_and_tag" in prompt_names
                assert "bulk_save" in prompt_names
                assert "curate_tags" in prompt_names
                assert "knowledge_query" in prompt_names

                # ── List resources ──
                resources_result = await session.list_resource_templates()
                template_uris = [r.uriTemplate for r in resources_result.resourceTemplates]
                print(f"✓ Listed {len(template_uris)} resource templates: {template_uris}")

        print(f"\n{'=' * 60}")
        print(f"  🎉 ALL E2E TESTS PASSED")
        print(f"{'=' * 60}")

    finally:
        # Clean shutdown
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
        db_path.unlink(missing_ok=True)
        print(f"\n✓ Server shut down, temp DB cleaned up")


if __name__ == "__main__":
    asyncio.run(main())
