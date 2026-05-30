"""rag.py — OpenAI embeddings + cosine similarity.

Pure unit; httpx call is mocked so the test does not hit the network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_bookmarks import rag


def test_cosine_similarity_orthogonal_is_zero():
    assert rag.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_identical_is_one():
    assert rag.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_opposite_is_minus_one():
    assert rag.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero():
    """Avoid division-by-zero blowup."""
    assert rag.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert rag.cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0


def test_embed_model_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_EMBED_MODEL", raising=False)
    assert rag.embed_model() == rag.DEFAULT_EMBED_MODEL


def test_embed_model_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_EMBED_MODEL", "text-embedding-3-large")
    assert rag.embed_model() == "text-embedding-3-large"


async def test_embed_texts_raises_without_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        await rag.embed_texts(["hello"])


async def test_embed_texts_preserves_input_order(monkeypatch: pytest.MonkeyPatch):
    """OpenAI's response includes an ``index`` field; we must sort by it before
    returning so callers can zip results with inputs without surprise.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    # Return embeddings out-of-order from the API to prove sorting works.
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["input"] == ["a", "b", "c"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "embedding": [0.3]},
                    {"index": 0, "embedding": [0.1]},
                    {"index": 1, "embedding": [0.2]},
                ]
            },
        )

    transport = httpx.MockTransport(_handler)
    original = httpx.AsyncClient

    class _PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedClient)

    result = await rag.embed_texts(["a", "b", "c"])
    assert result == [[0.1], [0.2], [0.3]]
