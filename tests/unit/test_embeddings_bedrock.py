"""Unit tests for the Bedrock Titan v2 embeddings client (mocked boto3)."""

from __future__ import annotations

import json

import pytest

from mcp_bookmarks import embeddings_bedrock as eb

# asyncio_mode = "auto" (pyproject) auto-detects async tests; sync tests here
# (embed_dims/embed_model) must stay unmarked, so no module-level pytestmark.


class _FakeBody:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _FakeBedrock:
    """Records invoke_model calls; returns a vector whose values encode the
    input length so tests can assert order + content."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke_model(self, *, modelId: str, body: str) -> dict:
        req = json.loads(body)
        self.calls.append(req)
        dims = int(req["dimensions"])
        val = float(len(req["inputText"]))
        return {"body": _FakeBody({"embedding": [val] * dims})}


@pytest.fixture
def fake_bedrock(monkeypatch: pytest.MonkeyPatch) -> _FakeBedrock:
    fake = _FakeBedrock()
    monkeypatch.setattr(eb, "_client", fake)  # short-circuit _bedrock()
    return fake


async def test_embed_empty_list_returns_empty(fake_bedrock: _FakeBedrock) -> None:
    assert await eb.embed_texts([]) == []
    assert fake_bedrock.calls == []


async def test_embed_preserves_order_and_dims(
    monkeypatch: pytest.MonkeyPatch, fake_bedrock: _FakeBedrock
) -> None:
    monkeypatch.setenv("BEDROCK_EMBED_DIMS", "4")
    vecs = await eb.embed_texts(["a", "bbb"])
    assert len(vecs) == 2
    assert vecs[0] == [1.0, 1.0, 1.0, 1.0]  # len("a") == 1
    assert vecs[1] == [3.0, 3.0, 3.0, 3.0]  # len("bbb") == 3


async def test_empty_string_is_padded_not_rejected(fake_bedrock: _FakeBedrock) -> None:
    vecs = await eb.embed_texts([""])
    # Titan rejects empty inputText; the client substitutes a single space.
    assert fake_bedrock.calls[0]["inputText"] == " "
    assert len(vecs) == 1


async def test_oversized_input_is_truncated(fake_bedrock: _FakeBedrock) -> None:
    await eb.embed_texts(["x" * (eb._MAX_INPUT_CHARS + 500)])
    assert len(fake_bedrock.calls[0]["inputText"]) == eb._MAX_INPUT_CHARS


async def test_normalize_flag_is_set(fake_bedrock: _FakeBedrock) -> None:
    await eb.embed_texts(["hello"])
    assert fake_bedrock.calls[0]["normalize"] is True


def test_embed_dims_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEDROCK_EMBED_DIMS", raising=False)
    assert eb.embed_dims() == 1024
    monkeypatch.setenv("BEDROCK_EMBED_DIMS", "512")
    assert eb.embed_dims() == 512
    monkeypatch.setenv("BEDROCK_EMBED_DIMS", "not-an-int")
    assert eb.embed_dims() == 1024  # falls back on bad value


def test_embed_model_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEDROCK_EMBED_MODEL", raising=False)
    assert eb.embed_model() == "amazon.titan-embed-text-v2:0"
    monkeypatch.setenv("BEDROCK_EMBED_MODEL", "cohere.embed-english-v3")
    assert eb.embed_model() == "cohere.embed-english-v3"
