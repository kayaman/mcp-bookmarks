"""Transport parity: both MCP transports + REST stats answer correctly.

Spawns a uvicorn subprocess on a free port (deterministic, all localhost,
no live HTTP) and checks:

1. SSE GET /sse → 200 + Content-Type: text/event-stream
2. Streamable HTTP POST /mcp initialize → 200/202 with a JSON-RPC result
3. REST GET /api/stats → 200 with total_bookmarks

Converted from the original tests/test_transports.py smoke script (WDN-395).
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "transport-smoke-test", "version": "0.1.0"},
    },
}


def _wait_for_tcp(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            sock.close()
            time.sleep(0.5)
            return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    raise TimeoutError(f"Server on port {port} did not start within {timeout}s")


@pytest.fixture(scope="module")
def server(free_port_module):
    """Start a uvicorn subprocess against a fresh tmp SQLite DB.

    Scope=module so the three transport checks share one server boot.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "transports.db")
        env = os.environ.copy()
        env["MCP_PORT"] = str(free_port_module)
        env["MCP_HOST"] = "127.0.0.1"
        env["BOOKMARKS_DB_PATH"] = db_path
        env["DYNAMODB_MODE"] = "false"

        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_bookmarks.server"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_tcp(free_port_module)
            yield free_port_module
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="module")
def free_port_module() -> int:
    """Module-scoped free port (the function-scoped `free_port` from conftest
    can't be used by a module-scoped fixture)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sse_transport_returns_event_stream(server):
    """GET /sse returns 200 with text/event-stream Content-Type."""
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    try:
        conn.request("GET", "/sse", headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        assert resp.status == 200, f"unexpected status {resp.status}"
        assert "text/event-stream" in resp.getheader("content-type", "")
    finally:
        conn.close()


def test_streamable_http_initialize_returns_result(server):
    """POST /mcp with a JSON-RPC initialize body returns a JSON-RPC result."""
    body = json.dumps(INITIALIZE_REQUEST).encode()
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    try:
        conn.request(
            "POST",
            "/mcp",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
        assert resp.status in (200, 202), f"unexpected status {resp.status}: {raw[:200]!r}"
        # Response may be plain JSON or SSE — either way it must carry the result field.
        assert b'"result"' in raw or b"result" in raw, f"no result in body: {raw[:300]!r}"
    finally:
        conn.close()


def test_rest_stats_endpoint(server):
    """GET /api/stats returns 200 with the total_bookmarks key (no auth in default mode)."""
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    try:
        conn.request("GET", "/api/stats")
        resp = conn.getresponse()
        raw = resp.read()
        assert resp.status == 200, f"unexpected status {resp.status}"
        data = json.loads(raw)
        assert "total_bookmarks" in data
        assert "total_tags" in data
    finally:
        conn.close()
