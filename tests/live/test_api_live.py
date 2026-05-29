"""LIVE: REST API end-to-end against a subprocess server with real URL fetches.

This test spawns a real uvicorn server and saves real public URLs
(github.com, pypi.org) to exercise the full extract + persist path.

Opt-in only: `uv run pytest -m live tests/live/test_api_live.py`.
The default `pytest` invocation skips this file (see pyproject `live` marker).
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


pytestmark = [pytest.mark.live, pytest.mark.asyncio]


async def find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_for_server(port: int, timeout: float = 15.0):
    import socket
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            sock.close()
            await asyncio.sleep(0.5)
            return
        except (ConnectionRefusedError, OSError):
            pass
        await asyncio.sleep(0.3)
    raise TimeoutError(f"Server on port {port} did not start within {timeout}s")


async def test_rest_api_end_to_end():
    """Full REST surface against a real uvicorn subprocess + live URL fetches."""
    port = await find_free_port()
    db_path = Path(tempfile.mktemp(suffix=".db"))
    base = f"http://127.0.0.1:{port}"

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
        print(f"✓ Server running on {base}")

        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:

            # ── GET /bookmarklet ──
            resp = await client.get(f"{base}/bookmarklet")
            assert resp.status_code == 200
            assert "MCP Bookmarks" in resp.text
            assert "Save to MCP" in resp.text
            print(f"✓ GET /bookmarklet → 200 (HTML page with bookmarklet)")

            # ── GET /api/stats (empty) ──
            resp = await client.get(f"{base}/api/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_bookmarks"] == 0
            print(f"✓ GET /api/stats → {data}")

            # ── GET /api/tags (empty) ──
            resp = await client.get(f"{base}/api/tags")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0
            print(f"✓ GET /api/tags → {data['total']} tags")

            # ── POST /api/save (JSON body) ──
            resp = await client.post(
                f"{base}/api/save",
                json={"url": "https://github.com"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "saved"
            assert data["bookmark_id"] == 1
            assert data["title"] is not None
            print(f"✓ POST /api/save (JSON) → id={data['bookmark_id']}, title='{data['title'][:50]}'")
            print(f"  word_count={data['word_count']}")

            # ── POST /api/save (form body) ──
            resp = await client.post(
                f"{base}/api/save",
                data={"url": "https://pypi.org/project/mcp/"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "saved"
            assert data["bookmark_id"] == 2
            print(f"✓ POST /api/save (form) → id={data['bookmark_id']}, title='{(data.get('title') or 'N/A')[:50]}'")

            # ── POST /api/save (missing url) ──
            resp = await client.post(f"{base}/api/save", json={})
            assert resp.status_code == 400
            print(f"✓ POST /api/save (no url) → 400")

            # ── GET /api/bookmarks ──
            resp = await client.get(f"{base}/api/bookmarks")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2
            print(f"✓ GET /api/bookmarks → {data['total']} bookmarks")

            # ── GET /api/bookmarks?query=github ──
            resp = await client.get(f"{base}/api/bookmarks?query=github")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] >= 1
            print(f"✓ GET /api/bookmarks?query=github → {data['total']} result(s)")

            # ── GET /api/bookmarks/1 ──
            resp = await client.get(f"{base}/api/bookmarks/1")
            assert resp.status_code == 200
            b1 = resp.json()
            assert b1.get("url")
            print(f"✓ GET /api/bookmarks/1 → url present")

            # ── POST /api/tag ──
            resp = await client.post(
                f"{base}/api/tag",
                json={"slug": "open-source", "name": "Open Source", "description": "FOSS"},
            )
            assert resp.status_code == 201
            print(f"✓ POST /api/tag → 201")

            # ── POST /api/bookmarks/1/tags ──
            resp = await client.post(
                f"{base}/api/bookmarks/1/tags",
                json={"tag_slugs": ["open-source"]},
            )
            assert resp.status_code == 200
            print(f"✓ POST /api/bookmarks/1/tags → 200")

            # ── POST /api/bookmarks/1/summary ──
            resp = await client.post(
                f"{base}/api/bookmarks/1/summary",
                json={"summary": "GitHub landing page."},
            )
            assert resp.status_code == 200
            print(f"✓ POST /api/bookmarks/1/summary → 200")

            # ── GET /api/stats (after saves) ──
            resp = await client.get(f"{base}/api/stats")
            data = resp.json()
            assert data["total_bookmarks"] == 2
            print(f"✓ GET /api/stats → {data}")

            # ── Verify MCP SSE still works alongside REST ──
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(f"{base}/sse") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool_names = [t.name for t in tools.tools]
                    assert "save_bookmark" in tool_names
                    assert "export_bookmarks" in tool_names
                    print(f"✓ MCP SSE coexists with REST: {len(tool_names)} tools available")

        print(f"\n{'=' * 60}")
        print(f"  🎉 ALL REST API TESTS PASSED")
        print(f"{'=' * 60}")

    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
        db_path.unlink(missing_ok=True)
        print(f"\n✓ Cleaned up")


