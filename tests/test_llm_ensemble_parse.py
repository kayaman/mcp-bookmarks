"""Judge JSON parsing for llm_ensemble."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_bookmarks.llm_ensemble import _parse_judge_json, gateway_status_public


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


def test_gateway_status_public_shape(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_ENABLED", raising=False)
    monkeypatch.delenv("ENSEMBLE_MODELS", raising=False)
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_BASE_URL", raising=False)
    d = gateway_status_public()
    assert set(d) == {
        "ensemble_enabled",
        "default_models",
        "default_judge",
        "has_api_key_configured",
        "gateway_display",
    }
    assert d["ensemble_enabled"] is False
    assert d["default_models"] == []
    assert d["has_api_key_configured"] is False
    assert "gateway.example" in d["gateway_display"]


def test_gateway_status_public_ensemble_flags(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_ENABLED", "true")
    monkeypatch.setenv("ENSEMBLE_MODELS", "a, b")
    monkeypatch.setenv("JUDGE_MODEL", "judge-x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    d = gateway_status_public()
    assert d["ensemble_enabled"] is True
    assert d["default_models"] == ["a", "b"]
    assert d["default_judge"] == "judge-x"
    assert d["has_api_key_configured"] is True
