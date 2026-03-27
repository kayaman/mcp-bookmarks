"""POST bookmark URLs to the mcp-bookmarks REST API (no LLM required)."""

from __future__ import annotations

from pathlib import Path

import httpx


def load_urls(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def ingest_urls(
    urls: list[str],
    api_base: str,
    *,
    timeout: float = 120.0,
    api_key: str | None = None,
) -> tuple[int, int]:
    """Return (ok_count, fail_count)."""
    base = api_base.rstrip("/")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    ok = 0
    fail = 0
    with httpx.Client(timeout=timeout) as client:
        for url in urls:
            try:
                r = client.post(f"{base}/api/save", json={"url": url}, headers=headers)
                if r.is_success:
                    ok += 1
                else:
                    fail += 1
                    print(f"[fail] {url} -> HTTP {r.status_code}: {r.text[:200]}", flush=True)
            except httpx.HTTPError as e:
                fail += 1
                print(f"[fail] {url} -> {e}", flush=True)
    return ok, fail
