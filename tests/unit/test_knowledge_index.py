"""Unit tests for the in-process Knowledge ANN index (fake embedder, no AWS)."""

from __future__ import annotations

import math

from mcp_bookmarks.knowledge_index import (
    KnowledgeIndex,
    compose_embedding_text,
    content_hash,
)

# asyncio_mode = "auto" (pyproject) handles async tests; the sync composition
# tests below must stay unmarked, so no module-level pytestmark.

_DIM = 4


async def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic unit vectors derived from the text — no network."""
    out: list[list[float]] = []
    for t in texts:
        v = [
            float(len(t) % 7) + 1.0,
            float(t.count("a") + 1),
            float(t.count("e") + 1),
            1.0,
        ]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


def _item(bid: str, *, user="owner", title="", summary="", content="", tags=None, exposed=None):
    it = {
        "id": bid,
        "userId": user,
        "url": f"https://ex.com/{bid}",
        "ogTitle": title,
        "aiSummary": summary,
        "aiContent": content,
        "aiTags": tags or [],
        "bookmarkType": "knowledge",
    }
    if exposed is not None:
        it["mcpExposed"] = exposed
    return it


class FakeDB:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    async def query_raw_by_type(self, bookmark_type, *, user_id, limit=None):
        return [
            i
            for i in self.items
            if i.get("bookmarkType") == bookmark_type and i.get("userId") == user_id
        ]


def _index(tmp_path) -> KnowledgeIndex:
    return KnowledgeIndex(dim=_DIM, path=tmp_path / "idx" / "index.bin")


# ── build + search ────────────────────────────────────────────────────


async def test_build_indexes_only_knowledge_with_text(tmp_path) -> None:
    db = FakeDB(
        [
            _item("a", title="Rust ownership", content="borrow checker deep dive"),
            _item("b", title="Postgres indexes", content="btree vs hash"),
            _item("c"),  # no text → skipped
            {"id": "d", "userId": "owner", "bookmarkType": "read_later", "url": "u"},  # wrong type
        ]
    )
    ki = _index(tmp_path)
    n = await ki.build(db, ["owner"], fake_embed)
    assert n == 2
    assert ki.ready is True


async def test_search_returns_scored_records(tmp_path) -> None:
    db = FakeDB([_item("a", title="alpha", content="aaaa"), _item("b", title="eee", content="eeee")])
    ki = _index(tmp_path)
    await ki.build(db, ["owner"], fake_embed)
    qvec = (await fake_embed(["aaaa"]))[0]
    results = ki.search(qvec, k=2)
    assert [r["id"] for r, _ in results]  # non-empty
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)  # descending


async def test_search_empty_index_returns_empty(tmp_path) -> None:
    ki = _index(tmp_path)
    assert ki.search([0.1, 0.2, 0.3, 0.4], k=5) == []


# ── persistence ───────────────────────────────────────────────────────


async def test_save_then_load_round_trip(tmp_path) -> None:
    db = FakeDB([_item("a", title="x", content="aaa"), _item("b", title="y", content="eee")])
    ki = _index(tmp_path)
    await ki.build(db, ["owner"], fake_embed)  # build() persists

    reloaded = _index(tmp_path)
    assert reloaded.load() is True
    assert reloaded.size == 2
    qvec = (await fake_embed(["aaa"]))[0]
    assert reloaded.search(qvec, k=1)


async def test_load_missing_index_returns_false(tmp_path) -> None:
    assert _index(tmp_path).load() is False


# ── incremental refresh ───────────────────────────────────────────────


async def test_refresh_adds_removes_and_updates(tmp_path) -> None:
    items = [_item("a", title="a1", content="aaa"), _item("b", title="b1", content="eee")]
    db = FakeDB(items)
    ki = _index(tmp_path)
    await ki.build(db, ["owner"], fake_embed)
    assert ki.size == 2

    # Add a new knowledge bookmark, drop "b", change "a"'s content.
    items.append(_item("c", title="c1", content="aeae"))
    items[:] = [it for it in items if it["id"] != "b"]
    for it in items:
        if it["id"] == "a":
            it["aiContent"] = "totally different body"

    stats = await ki.refresh(db, ["owner"], fake_embed)
    assert stats["removed"] == 1
    assert stats["added"] == 2  # new "c" + changed "a"
    assert ki.size == 2  # a + c
    assert set(ki._label_by_id) == {"a", "c"}


async def test_refresh_skips_unchanged(tmp_path) -> None:
    db = FakeDB([_item("a", title="a", content="aaa")])
    ki = _index(tmp_path)
    await ki.build(db, ["owner"], fake_embed)
    stats = await ki.refresh(db, ["owner"], fake_embed)
    assert stats["added"] == 0 and stats["removed"] == 0


async def test_refresh_builds_when_empty(tmp_path) -> None:
    db = FakeDB([_item("a", title="a", content="aaa")])
    ki = _index(tmp_path)
    stats = await ki.refresh(db, ["owner"], fake_embed)  # no prior build
    assert stats.get("rebuilt") is True
    assert ki.size == 1


# ── composition helpers ───────────────────────────────────────────────


def test_compose_prefers_deep_text_and_includes_signals() -> None:
    text = compose_embedding_text(
        {
            "ogTitle": "Title",
            "aiSummary": "Summary",
            "aiTags": ["rust", "db"],
            "aiContent": "short body",
            "deepText": "full transcript",
        }
    )
    assert "Title" in text and "Summary" in text
    assert "#rust" in text and "#db" in text
    assert "full transcript" in text  # deepText wins over aiContent
    assert "short body" not in text


def test_content_hash_changes_with_text() -> None:
    assert content_hash("a") != content_hash("b")
    assert content_hash("a") == content_hash("a")
