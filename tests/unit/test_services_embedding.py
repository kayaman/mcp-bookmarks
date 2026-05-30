"""EmbeddingService — capability-gated semantic-search orchestration."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_bookmarks.backend import (
    DYNAMODB_CAPABILITIES,
    SQLITE_CAPABILITIES,
    UnsupportedCapability,
)
from mcp_bookmarks.services import embedding as embedding_service


class _FakeSQLiteBackend:
    """Stand-in for the SQLite Database with the capability-gated methods stubbed."""

    capabilities = SQLITE_CAPABILITIES

    def __init__(self) -> None:
        # Rename the class on the instance so _backend_name resolves to "sqlite"
        # (matches the test_capability_enforcement.py pattern).
        self.__class__ = type("Database", (_FakeSQLiteBackend,), {})
        self.upserts: list[tuple[int, str, list[float]]] = []
        self._rows: list[tuple[int, list[float]]] = []

    async def upsert_bookmark_embedding(
        self, bookmark_id: int, model: str, vec: list[float]
    ) -> None:
        self.upserts.append((bookmark_id, model, vec))
        self._rows.append((bookmark_id, vec))

    async def get_all_embeddings(self, model: str) -> list[tuple[int, list[float]]]:
        return list(self._rows)


class _FakeDynamoBackend:
    capabilities = DYNAMODB_CAPABILITIES

    def __init__(self) -> None:
        self.__class__ = type("DynamoDBDatabase", (_FakeDynamoBackend,), {})


async def _stub_embed(*, monkeypatch: pytest.MonkeyPatch, vectors: dict[str, list[float]]):
    """Patch rag.embed_texts to return deterministic vectors keyed by input."""

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [vectors[t] for t in texts]

    monkeypatch.setattr("mcp_bookmarks.rag.embed_texts", fake_embed_texts)
    monkeypatch.setattr("mcp_bookmarks.rag.embed_model", lambda: "test-embed-model")


# ── index_bookmark ─────────────────────────────────────────────────


async def test_index_bookmark_persists_vector(monkeypatch: pytest.MonkeyPatch):
    await _stub_embed(monkeypatch=monkeypatch, vectors={"hello world": [0.1, 0.2, 0.3]})
    db = _FakeSQLiteBackend()
    model, chars = await embedding_service.index_bookmark(db=db, bookmark_id=42, text="hello world")
    assert model == "test-embed-model"
    assert chars == len("hello world")
    assert db.upserts == [(42, "test-embed-model", [0.1, 0.2, 0.3])]


async def test_index_bookmark_raises_on_unsupported_backend(monkeypatch: pytest.MonkeyPatch):
    db = _FakeDynamoBackend()
    with pytest.raises(UnsupportedCapability) as excinfo:
        await embedding_service.index_bookmark(db=db, bookmark_id=1, text="x")  # type: ignore[arg-type]
    assert excinfo.value.capability == "semantic_search"
    assert excinfo.value.method == "index_bookmark_embedding"


# ── semantic_search ────────────────────────────────────────────────


async def test_semantic_search_returns_empty_when_no_embeddings_indexed(
    monkeypatch: pytest.MonkeyPatch,
):
    await _stub_embed(monkeypatch=monkeypatch, vectors={"query": [1.0, 0.0]})
    db = _FakeSQLiteBackend()  # no upserts → get_all_embeddings returns []
    results, model = await embedding_service.semantic_search(db=db, query="query", limit=5)
    assert results == []
    assert model == "test-embed-model"


async def test_semantic_search_ranks_by_cosine_similarity(monkeypatch: pytest.MonkeyPatch):
    await _stub_embed(
        monkeypatch=monkeypatch,
        vectors={"query": [1.0, 0.0]},
    )
    db = _FakeSQLiteBackend()
    # Seed embeddings directly (bypass upsert path).
    db._rows = [
        (1, [1.0, 0.0]),  # cos = 1.0 (perfect match)
        (2, [0.0, 1.0]),  # cos = 0.0 (orthogonal)
        (3, [0.7071, 0.7071]),  # cos ≈ 0.707
    ]
    results, model = await embedding_service.semantic_search(db=db, query="query", limit=5)
    assert model == "test-embed-model"
    assert [bid for bid, _ in results] == [1, 3, 2]
    # Top-ranked is exact match
    assert results[0][1] == pytest.approx(1.0)


async def test_semantic_search_respects_limit(monkeypatch: pytest.MonkeyPatch):
    await _stub_embed(monkeypatch=monkeypatch, vectors={"q": [1.0, 0.0]})
    db = _FakeSQLiteBackend()
    db._rows = [(i, [1.0, 0.0]) for i in range(10)]
    results, _ = await embedding_service.semantic_search(db=db, query="q", limit=3)
    assert len(results) == 3


async def test_semantic_search_raises_on_unsupported_backend():
    db = _FakeDynamoBackend()
    with pytest.raises(UnsupportedCapability) as excinfo:
        await embedding_service.semantic_search(db=db, query="x")  # type: ignore[arg-type]
    assert excinfo.value.capability == "semantic_search"
    assert excinfo.value.method == "semantic_search_bookmarks"


# ── re-export sanity ───────────────────────────────────────────────


def test_unsupported_capability_is_re_exported():
    """services.embedding re-exports UnsupportedCapability so handlers can
    import it from the same module they call (per the docstring contract)."""
    assert embedding_service.UnsupportedCapability is UnsupportedCapability
    # __all__ shape preserved
    assert "UnsupportedCapability" in embedding_service.__all__
    assert "index_bookmark" in embedding_service.__all__
    assert "semantic_search" in embedding_service.__all__


# Suppress: the fake backends quack like BookmarkBackend but mypy doesn't know.
_: Any = None
