"""Tests for blogmarks_crew batch ingest — run: uv run python -m unittest tests.test_blogmarks_crew"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestBlogmarksCrewIngest(unittest.TestCase):
    def test_load_urls_real_file(self) -> None:
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

    def test_ingest_urls_counts_success_and_failure(self) -> None:
        from blogmarks_crew.ingest import ingest_urls

        good = MagicMock(is_success=True, status_code=200, text="ok")
        bad = MagicMock(is_success=False, status_code=500, text="err")
        mock_post = MagicMock(side_effect=[good, bad])
        mock_client_instance = MagicMock()
        mock_client_instance.post = mock_post
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_client_instance
        mock_cm.__exit__.return_value = None

        with patch("blogmarks_crew.ingest.httpx.Client", return_value=mock_cm):
            ok, fail = ingest_urls(
                ["https://a.com", "https://b.com"],
                "http://127.0.0.1:8000",
            )

        self.assertEqual(ok, 1)
        self.assertEqual(fail, 1)
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
