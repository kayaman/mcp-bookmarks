"""Shared pytest fixtures.

Layout (per WDN-395 / OSS-5):

  tests/unit/         pure logic, no I/O, no subprocess; runs in milliseconds
  tests/integration/  deterministic local: tmp SQLite, mocked boto3, offline
                      scraper. May spawn a uvicorn subprocess for transport
                      tests but never touches the public internet or AWS.
  tests/live/         opt-in. Real network / real AWS / real LLM. Excluded
                      from the default `pytest` run via the `live` marker;
                      CI runs them on workflow_dispatch only.

Run defaults to "not live": see `pyproject.toml` `[tool.pytest.ini_options]`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio

# Put src/ on sys.path so tests can import `mcp_bookmarks.*` without an editable install.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ── Filesystem fixtures ────────────────────────────────────────────


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Path to a fresh SQLite file under pytest's tmp_path."""
    return tmp_path / "bookmarks.db"


# ── SQLite Database fixture ────────────────────────────────────────


@pytest_asyncio.fixture
async def db(tmp_db_path: Path) -> "AsyncIterator":
    """Connected Database instance backed by a fresh SQLite file. Closes on teardown."""
    from mcp_bookmarks.db import Database

    instance = Database(tmp_db_path)
    await instance.connect()
    try:
        yield instance
    finally:
        await instance.close()


# ── Offline scraper fixture ────────────────────────────────────────
#
# scraper.extract_og_metadata() and extract_article_content() both go through
# scraper.fetch_html(). Patching fetch_html in-process gives every test that
# touches the scraper a deterministic HTML body without live HTTP.


CANNED_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Canned Article - Title Tag</title>
  <meta property="og:title" content="Canned OG Title">
  <meta property="og:description" content="A test article for the offline scraper fixture.">
  <meta property="og:image" content="https://example.com/cover.jpg">
  <meta property="og:site_name" content="Example">
  <meta property="og:locale" content="en_US">
</head>
<body>
  <article>
    <h1>Canned OG Title</h1>
    <p>Paragraph one with enough words to satisfy trafilatura's minimum body length.
    The canned fixture stays stable across runs so the scraper tests are deterministic.</p>
    <p>Paragraph two adds a few more sentences. We want a non-empty extraction result
    so the article path can assert word_count &gt; 0 without doing real HTTP.</p>
  </article>
</body>
</html>
"""


@pytest.fixture
def offline_scraper(monkeypatch: pytest.MonkeyPatch) -> str:
    """Monkeypatch scraper.fetch_html to return a stable canned HTML body.

    Returns the canned HTML string for tests that want to inspect it.
    """
    from mcp_bookmarks import scraper

    async def _fake_fetch_html(url: str) -> str:
        return CANNED_HTML

    monkeypatch.setattr(scraper, "fetch_html", _fake_fetch_html)
    return CANNED_HTML


# ── Free port helper ────────────────────────────────────────────────


@pytest.fixture
def free_port() -> int:
    """Allocate a free TCP port on 127.0.0.1."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
