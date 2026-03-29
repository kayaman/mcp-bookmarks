"""POST bookmark URLs to the mcp-bookmarks REST API (no LLM required)."""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from .api_base_util import rest_api_prefix


def load_urls(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def ingest_urls(
    urls: list[str],
    api_base: str,
    *,
    timeout: float = 120.0,
    api_key: str | None = None,
    batch_size: int = 0,
    delay: float = 0.0,
    failures_file: Path | None = None,
) -> tuple[int, int]:
    """Return (ok_count, fail_count).

    When *batch_size* > 0 the URL list is chunked and *delay* seconds
    are inserted between consecutive batches.  Failed URLs are
    optionally appended to *failures_file* for later retry.
    """
    prefix = rest_api_prefix(api_base)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    ok = 0
    fail = 0
    failed_urls: list[str] = []

    if batch_size > 0:
        batches = [urls[i : i + batch_size] for i in range(0, len(urls), batch_size)]
    else:
        batches = [urls]

    with httpx.Client(timeout=timeout) as client:
        for batch_idx, batch in enumerate(batches):
            if batch_idx > 0 and delay > 0:
                print(f"[batch] waiting {delay:.1f}s before batch {batch_idx + 1}/{len(batches)}", flush=True)
                time.sleep(delay)

            for url in batch:
                try:
                    r = client.post(f"{prefix}/save", json={"url": url}, headers=headers)
                    if r.is_success:
                        ok += 1
                    else:
                        fail += 1
                        failed_urls.append(url)
                        print(f"[fail] {url} -> HTTP {r.status_code}: {r.text[:200]}", flush=True)
                except httpx.HTTPError as e:
                    fail += 1
                    failed_urls.append(url)
                    print(f"[fail] {url} -> {e}", flush=True)

    if failures_file and failed_urls:
        failures_file.parent.mkdir(parents=True, exist_ok=True)
        with failures_file.open("a", encoding="utf-8") as fh:
            for u in failed_urls:
                fh.write(u + "\n")
        print(f"[info] {len(failed_urls)} failed URLs appended to {failures_file}", flush=True)

    return ok, fail
