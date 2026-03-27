"""Judge JSON parsing for llm_ensemble."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_bookmarks.llm_ensemble import _parse_judge_json


def test_parse_judge_plain_json():
    d = _parse_judge_json(
        '{"chosen_index": 1, "rationale": "clearer", "answer": "Final text"}'
    )
    assert d["chosen_index"] == 1
    assert d["answer"] == "Final text"


def test_parse_judge_fenced():
    raw = """Here is the verdict:
```json
{"chosen_index": 0, "rationale": "x", "answer": "OK"}
```
"""
    d = _parse_judge_json(raw)
    assert d["chosen_index"] == 0
    assert d["answer"] == "OK"
