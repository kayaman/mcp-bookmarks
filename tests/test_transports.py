"""
Transport smoke tests: verifies both SSE and Streamable HTTP transports
respond correctly to a JSON-RPC initialize request.

Run:
    python tests/test_transports.py
or:
    uv run python tests/test_transports.py
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_tcp(port: int, timeout: float = 15.0):
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


def start_server(port: int, db_path: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["MCP_PORT"] = str(port)
    env["MCP_HOST"] = "127.0.0.1"
    env["BOOKMARKS_DB_PATH"] = db_path
    env["DYNAMODB_MODE"] = "false"
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_bookmarks.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def check_sse_transport(port: int) -> bool:
    """Hit /sse and confirm the server returns an SSE event stream."""
    import http.client

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/sse", headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        content_type = resp.getheader("content-type", "")
        conn.close()
        if resp.status != 200:
            print(f"  SSE: unexpected status {resp.status}")
            return False
        if "text/event-stream" not in content_type:
            print(f"  SSE: unexpected content-type: {content_type}")
            return False
        return True
    except Exception as exc:
        print(f"  SSE check failed: {exc}")
        return False


def check_streamable_http_transport(port: int) -> bool:
    """POST initialize to /mcp and confirm a valid JSON-RPC result."""
    import http.client

    body = json.dumps(INITIALIZE_REQUEST).encode()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
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
        conn.close()
        if resp.status not in (200, 202):
            print(f"  Streamable HTTP: unexpected status {resp.status}")
            print(f"  Body: {raw[:200]}")
            return False
        # Response may be JSON or SSE; either way it must contain "result"
        if b'"result"' not in raw and b"result" not in raw:
            print(f"  Streamable HTTP: 'result' not found in response")
            print(f"  Body: {raw[:300]}")
            return False
        return True
    except Exception as exc:
        print(f"  Streamable HTTP check failed: {exc}")
        return False


def check_stats_endpoint(port: int) -> bool:
    """GET /api/stats must return 200 with JSON (no auth required in default mode)."""
    import http.client

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/stats")
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        if resp.status != 200:
            print(f"  Stats: unexpected status {resp.status}")
            return False
        data = json.loads(raw)
        if "total_bookmarks" not in data:
            print(f"  Stats: missing 'total_bookmarks' key: {data}")
            return False
        return True
    except Exception as exc:
        print(f"  Stats check failed: {exc}")
        return False


def run_smoke() -> bool:
    port = find_free_port()
    print(f"\nStarting server on port {port}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        proc = start_server(port, db_path)
        try:
            wait_for_tcp(port)
            print("Server ready.")

            results = {}
            results["SSE transport (/sse)"] = check_sse_transport(port)
            results["Streamable HTTP transport (/mcp)"] = check_streamable_http_transport(port)
            results["REST stats (/api/stats)"] = check_stats_endpoint(port)

            print("\nResults:")
            all_pass = True
            for name, ok in results.items():
                status = "PASS" if ok else "FAIL"
                print(f"  [{status}] {name}")
                if not ok:
                    all_pass = False

            return all_pass
        finally:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    ok = run_smoke()
    sys.exit(0 if ok else 1)
