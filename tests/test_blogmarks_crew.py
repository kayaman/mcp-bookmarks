"""Tests for blogmarks_crew — run: uv run python -m pytest tests/test_blogmarks_crew.py"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ── load_urls ────────────────────────────────────────────────────────


class TestLoadUrls(unittest.TestCase):
    def test_load_urls_skips_comments_and_blanks(self) -> None:
        from blogmarks_crew.ingest import load_urls

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# intro\n\nhttps://example.com/page\n  https://b.org  \n")
            p = Path(f.name)
        try:
            self.assertEqual(
                load_urls(p),
                ["https://example.com/page", "https://b.org"],
            )
        finally:
            p.unlink(missing_ok=True)

    def test_load_urls_empty_file(self) -> None:
        from blogmarks_crew.ingest import load_urls

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# only comments\n# nothing here\n\n")
            p = Path(f.name)
        try:
            self.assertEqual(load_urls(p), [])
        finally:
            p.unlink(missing_ok=True)


# ── ingest_urls ──────────────────────────────────────────────────────


def _mock_httpx_client(side_effects: list):
    """Build a mock httpx.Client context-manager with the given response side effects."""
    mock_post = MagicMock(side_effect=side_effects)
    mock_client_instance = MagicMock()
    mock_client_instance.post = mock_post
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client_instance
    mock_cm.__exit__.return_value = None
    return mock_cm, mock_post


class TestIngestUrls(unittest.TestCase):
    def test_counts_success_and_failure(self) -> None:
        from blogmarks_crew.ingest import ingest_urls

        good = MagicMock(is_success=True, status_code=200, text="ok")
        bad = MagicMock(is_success=False, status_code=500, text="err")
        mock_cm, mock_post = _mock_httpx_client([good, bad])

        with patch("blogmarks_crew.ingest.httpx.Client", return_value=mock_cm):
            ok, fail = ingest_urls(
                ["https://a.com", "https://b.com"],
                "http://127.0.0.1:8000",
            )

        self.assertEqual(ok, 1)
        self.assertEqual(fail, 1)
        self.assertEqual(mock_post.call_count, 2)

    def test_batch_size_creates_batches(self) -> None:
        from blogmarks_crew.ingest import ingest_urls

        responses = [MagicMock(is_success=True, status_code=200, text="ok") for _ in range(5)]
        mock_cm, mock_post = _mock_httpx_client(responses)

        with patch("blogmarks_crew.ingest.httpx.Client", return_value=mock_cm), \
             patch("blogmarks_crew.ingest.time.sleep") as mock_sleep:
            ok, fail = ingest_urls(
                [f"https://example.com/{i}" for i in range(5)],
                "http://127.0.0.1:8000",
                batch_size=2,
                delay=0.5,
            )

        self.assertEqual(ok, 5)
        self.assertEqual(fail, 0)
        self.assertEqual(mock_sleep.call_count, 2)  # 3 batches → 2 sleeps
        mock_sleep.assert_called_with(0.5)

    def test_failures_file_written(self) -> None:
        from blogmarks_crew.ingest import ingest_urls

        good = MagicMock(is_success=True, status_code=200, text="ok")
        bad = MagicMock(is_success=False, status_code=500, text="err")
        mock_cm, _ = _mock_httpx_client([good, bad, bad])

        with tempfile.TemporaryDirectory() as td:
            failures_path = Path(td) / "failures.txt"
            with patch("blogmarks_crew.ingest.httpx.Client", return_value=mock_cm):
                ok, fail = ingest_urls(
                    ["https://a.com", "https://b.com", "https://c.com"],
                    "http://127.0.0.1:8000",
                    failures_file=failures_path,
                )

            self.assertEqual(ok, 1)
            self.assertEqual(fail, 2)
            content = failures_path.read_text()
            self.assertIn("https://b.com", content)
            self.assertIn("https://c.com", content)


# ── idempotency (_is_already_enriched) ───────────────────────────────


class TestIdempotency(unittest.TestCase):
    def test_already_enriched_skips(self) -> None:
        from blogmarks_crew.crew_enrich import _is_already_enriched

        enriched_response = MagicMock()
        enriched_response.is_success = True
        enriched_response.json.return_value = {
            "summary": "A great article.",
            "tags": ["python", "ai"],
        }

        with patch("blogmarks_crew.crew_enrich.httpx.get", return_value=enriched_response):
            self.assertTrue(_is_already_enriched("http://localhost:8000", "42", None))

    def test_not_enriched_when_missing_summary(self) -> None:
        from blogmarks_crew.crew_enrich import _is_already_enriched

        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"summary": None, "tags": ["python"]}

        with patch("blogmarks_crew.crew_enrich.httpx.get", return_value=response):
            self.assertFalse(_is_already_enriched("http://localhost:8000", "42", None))

    def test_not_enriched_when_missing_tags(self) -> None:
        from blogmarks_crew.crew_enrich import _is_already_enriched

        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"summary": "Something.", "tags": []}

        with patch("blogmarks_crew.crew_enrich.httpx.get", return_value=response):
            self.assertFalse(_is_already_enriched("http://localhost:8000", "42", None))

    def test_not_enriched_on_http_error(self) -> None:
        from blogmarks_crew.crew_enrich import _is_already_enriched

        response = MagicMock()
        response.is_success = False

        with patch("blogmarks_crew.crew_enrich.httpx.get", return_value=response):
            self.assertFalse(_is_already_enriched("http://localhost:8000", "42", None))


# ── run_enrichment_crew (mocked) ─────────────────────────────────────


class TestRunEnrichmentCrewMocked(unittest.TestCase):
    @patch("blogmarks_crew.crew_enrich._is_already_enriched", return_value=True)
    @patch("blogmarks_crew.crew_enrich._save_url", return_value=("99", ""))
    def test_skips_enriched_bookmark(self, mock_save, mock_enriched) -> None:
        from blogmarks_crew.crew_enrich import run_enrichment_crew

        result = run_enrichment_crew("https://example.com", "http://localhost:8000")
        self.assertIn("already enriched", result)
        self.assertIn("99", result)

    @patch("blogmarks_crew.crew_enrich._is_already_enriched", return_value=True)
    @patch("blogmarks_crew.crew_enrich._save_url", return_value=("99", ""))
    @patch("blogmarks_crew.crew_enrich.make_bookmarks_rest_tools", return_value=[MagicMock() for _ in range(6)])
    def test_force_overrides_enriched_check(self, mock_tools, mock_save, mock_enriched) -> None:
        from blogmarks_crew.crew_enrich import run_enrichment_crew
        import crewai

        orig = {k: getattr(crewai, k) for k in ("Agent", "Crew", "Process", "Task")}
        mock_crew_instance = MagicMock()
        mock_crew_instance.kickoff.return_value = "tags: python, ai"
        try:
            crewai.Agent = MagicMock()
            crewai.Task = MagicMock()
            crewai.Process = MagicMock()
            crewai.Crew = MagicMock(return_value=mock_crew_instance)
            result = run_enrichment_crew("https://example.com", "http://localhost:8000", force=True)
        finally:
            for k, v in orig.items():
                setattr(crewai, k, v)

        self.assertIn("bookmark_id=99", result)
        mock_crew_instance.kickoff.assert_called_once()

    @patch("blogmarks_crew.crew_enrich._save_url", return_value=(None, "connection refused"))
    def test_save_failure_reported(self, mock_save) -> None:
        from blogmarks_crew.crew_enrich import run_enrichment_crew

        result = run_enrichment_crew("https://example.com", "http://localhost:8000")
        self.assertIn("save_bookmark failed", result)
        self.assertIn("connection refused", result)


# ── Rust fetcher tool ────────────────────────────────────────────────


class TestRustFetcher(unittest.TestCase):
    @patch("blogmarks_crew.rest_crew_tools.shutil.which", return_value=None)
    def test_find_rust_binary_not_found(self, mock_which) -> None:
        from blogmarks_crew.rest_crew_tools import _find_rust_binary

        result = _find_rust_binary()
        self.assertIsNone(result)

    @patch("blogmarks_crew.rest_crew_tools.shutil.which", return_value="/usr/local/bin/blogmarks-fetch")
    def test_find_rust_binary_on_path(self, mock_which) -> None:
        from blogmarks_crew.rest_crew_tools import _find_rust_binary

        result = _find_rust_binary()
        self.assertEqual(result, "/usr/local/bin/blogmarks-fetch")


# ── CLI argument parsing ─────────────────────────────────────────────


class TestCLI(unittest.TestCase):
    def test_ingest_requires_urls_file(self) -> None:
        from blogmarks_crew.cli import main

        with self.assertRaises(SystemExit) as ctx:
            main(["ingest"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_ingest_batch_args_parsed(self) -> None:
        from blogmarks_crew.cli import main

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# empty\n")
            p = Path(f.name)
        try:
            result = main([
                "ingest",
                "--urls-file", str(p),
                "--batch-size", "5",
                "--delay", "2.0",
                "--max-cost", "1.50",
            ])
            self.assertEqual(result, 1)  # No URLs → exits 1
        finally:
            p.unlink(missing_ok=True)


# ── DynamoDB mode in REST API ────────────────────────────────────────


class TestDynamoDBModeAPI(unittest.TestCase):
    @patch.dict("os.environ", {"DYNAMODB_MODE": "true"})
    def test_dynamodb_mode_flag_detected(self) -> None:
        from mcp_bookmarks.api import _dynamodb_mode

        self.assertTrue(_dynamodb_mode())

    @patch.dict("os.environ", {"DYNAMODB_MODE": "false"})
    def test_sqlite_mode_when_off(self) -> None:
        from mcp_bookmarks.api import _dynamodb_mode

        self.assertFalse(_dynamodb_mode())

    @patch.dict("os.environ", {}, clear=False)
    def test_sqlite_mode_when_unset(self) -> None:
        import os
        os.environ.pop("DYNAMODB_MODE", None)
        from mcp_bookmarks.api import _dynamodb_mode

        self.assertFalse(_dynamodb_mode())


if __name__ == "__main__":
    unittest.main()
