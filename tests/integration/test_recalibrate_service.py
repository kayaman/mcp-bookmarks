"""Recalibrate service tests (SQLite backend, converse_text mocked at the seam).

Uses the shared ``db`` fixture from tests/conftest.py.
"""

from __future__ import annotations

import json

import pytest


def _mock_llm(monkeypatch: pytest.MonkeyPatch, payload: str) -> dict:
    """Replace converse_text with a recorder returning ``payload``."""
    calls: dict = {}

    def fake(prompt: str, *, system: str | None = None, max_tokens: int = 2000) -> str:
        calls["prompt"] = prompt
        calls["system"] = system
        return payload

    monkeypatch.setattr("mcp_bookmarks.services.recalibrate.converse_text", fake)
    return calls


async def _seed(db, url, tags=()):
    bm = await db.upsert_bookmark(url=url, title="T")
    for slug in tags:
        if await db.get_tag_by_slug(slug) is None:
            await db.create_tag(slug, slug)
    if tags:
        await db.tag_bookmark(bm.id, list(tags))
    return bm.id


async def test_propose_pinned_contract_merge_and_rename(db, monkeypatch):
    from mcp_bookmarks.services import recalibrate

    await _seed(db, "https://example.com/1", ("machine-learning", "ml-engineering"))
    await _seed(db, "https://example.com/2", ("machine-learning", "webdev"))
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "ops": [
                    {"source": "machine-learning", "target": "ml-engineering", "reason": "dup"},
                    {"source": "webdev", "target": "web-development", "reason": "convention"},
                ]
            }
        ),
    )
    out = await recalibrate.propose(db)
    assert out == {
        "ops": [
            {
                "kind": "merge",  # target ml-engineering is live
                "source": "machine-learning",
                "target": "ml-engineering",
                "bookmarksAffected": 2,
                "reason": "dup",
            },
            {
                "kind": "rename",  # target web-development does not exist yet
                "source": "webdev",
                "target": "web-development",
                "bookmarksAffected": 1,
                "reason": "convention",
            },
        ],
        "editsConsidered": 0,
        "tagsConsidered": 3,
    }


async def test_propose_drops_invalid_and_conflicting_ops(db, monkeypatch):
    from mcp_bookmarks.services import recalibrate

    await _seed(db, "https://example.com/1", ("aa-bb", "cc-dd", "ee-ff"))
    _mock_llm(
        monkeypatch,
        json.dumps(
            {
                "ops": [
                    {"source": "aa-bb", "target": "cc-dd", "reason": "ok"},
                    {"source": "ghost-tag", "target": "cc-dd", "reason": "source not live"},
                    {"source": "cc-dd", "target": "not_valid!", "reason": "bad target shape"},
                    {"source": "cc-dd", "target": "gg-hh", "reason": "chained: cc-dd is a target"},
                    {"source": "aa-bb", "target": "ii-jj", "reason": "duplicate source"},
                    {"source": "ee-ff", "target": "aa-bb", "reason": "target is earlier source"},
                    {"source": "ee-ff", "target": "ee-ff", "reason": "self-merge"},
                ]
            }
        ),
    )
    out = await recalibrate.propose(db)
    assert [(o["source"], o["target"]) for o in out["ops"]] == [("aa-bb", "cc-dd")]


async def test_propose_tolerates_markdown_fences(db, monkeypatch):
    from mcp_bookmarks.services import recalibrate

    await _seed(db, "https://example.com/1", ("aa-bb",))
    _mock_llm(
        monkeypatch,
        '```json\n{"ops": [{"source": "aa-bb", "target": "cc-dd", "reason": "r"}]}\n```',
    )
    out = await recalibrate.propose(db)
    assert out["ops"][0]["target"] == "cc-dd"


async def test_propose_unparseable_output_raises_propose_failed(db, monkeypatch):
    from mcp_bookmarks.services import recalibrate

    _mock_llm(monkeypatch, "sorry, I cannot do JSON today")
    with pytest.raises(recalibrate.ProposeFailed):
        await recalibrate.propose(db)


async def test_propose_bedrock_error_raises_propose_failed(db, monkeypatch):
    from mcp_bookmarks.bedrock_text import BedrockTextError
    from mcp_bookmarks.services import recalibrate

    def boom(prompt, *, system=None, max_tokens=2000):
        raise BedrockTextError("throttled")

    monkeypatch.setattr("mcp_bookmarks.services.recalibrate.converse_text", boom)
    with pytest.raises(recalibrate.ProposeFailed):
        await recalibrate.propose(db)


async def test_propose_writes_nothing_and_prompts_with_corrections(db, monkeypatch):
    from mcp_bookmarks.services import recalibrate

    bid = await _seed(db, "https://example.com/1", ("aa-bb",))
    await db.replace_bookmark_tags(bid, ["cc-dd"], actor="human")  # human correction
    calls = _mock_llm(monkeypatch, json.dumps({"ops": []}))
    before_tags = [t.slug for t in await db.get_all_tags()]
    out = await recalibrate.propose(db)
    assert out["ops"] == [] and out["editsConsidered"] == 1
    assert [t.slug for t in await db.get_all_tags()] == before_tags  # nothing written
    assert (await db.get_bookmark_by_id(bid)).tags == ["cc-dd"]
    # Prompt carries live tags with usage counts + the human corrections summary.
    assert "cc-dd" in calls["prompt"] and "aa-bb" in calls["prompt"]
    assert "removed by the human" in calls["prompt"]
    assert "added by the human" in calls["prompt"]
    assert "taxonomy curator" in calls["system"]
    # Owner taxonomy policy is embedded in the system prompt.
    assert "compact" in calls["system"] and "PREFER merging into existing" in calls["system"]
