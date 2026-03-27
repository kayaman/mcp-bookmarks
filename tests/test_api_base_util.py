"""blogmarks_crew.api_base_util"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from blogmarks_crew.api_base_util import rest_api_prefix


def test_rest_api_prefix_appends_api() -> None:
    assert rest_api_prefix("http://127.0.0.1:8000") == "http://127.0.0.1:8000/api"
    assert rest_api_prefix("http://127.0.0.1:8000/") == "http://127.0.0.1:8000/api"


def test_rest_api_prefix_idempotent() -> None:
    assert rest_api_prefix("http://x/api") == "http://x/api"
    assert rest_api_prefix("http://x/api/") == "http://x/api"
