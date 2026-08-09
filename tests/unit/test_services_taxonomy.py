"""TaxonomyService — thin wrappers around backend tag CRUD + merge.

Each function forwards to the backend and (sometimes) emits a log. The
fake backend records every call so we can assert wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_bookmarks.models import Bookmark, Tag
from mcp_bookmarks.services import taxonomy

# ── Fake backend ───────────────────────────────────────────────────


@dataclass
class _FakeBackend:
    """Stand-in for BookmarkBackend with per-method call tracking."""

    tag_by_slug: Tag | None = None
    created_tag: Tag | None = None
    updated_tag: Tag | None = None
    deleted_ok: bool = True
    merge_result: dict[str, Any] = field(default_factory=lambda: {"bookmarks_reassigned": 3})
    tagged_bookmark: Bookmark | None = None
    untagged_bookmark: Bookmark | None = None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def get_all_tags(self) -> list[Tag]:
        self.calls.append(("get_all_tags", {}))
        return []

    async def search_tags(self, query: str) -> list[Tag]:
        self.calls.append(("search_tags", {"query": query}))
        return []

    async def get_tag_by_slug(self, slug: str) -> Tag | None:
        self.calls.append(("get_tag_by_slug", {"slug": slug}))
        return self.tag_by_slug

    async def create_tag(self, *, slug: str, name: str, description: str = "") -> Tag:
        self.calls.append(("create_tag", {"slug": slug, "name": name, "description": description}))
        return self.created_tag or Tag(slug=slug, name=name, description=description)

    async def update_tag(
        self,
        slug: str,
        *,
        new_name: str | None = None,
        new_description: str | None = None,
    ) -> Tag | None:
        self.calls.append(
            ("update_tag", {"slug": slug, "new_name": new_name, "new_description": new_description})
        )
        return self.updated_tag

    async def delete_tag(self, slug: str) -> bool:
        self.calls.append(("delete_tag", {"slug": slug}))
        return self.deleted_ok

    async def merge_tags(self, source_slug: str, target_slug: str) -> dict[str, Any]:
        self.calls.append(("merge_tags", {"source": source_slug, "target": target_slug}))
        return self.merge_result

    async def tag_bookmark(self, bookmark_id: int | str, tag_slugs: list[str]) -> Bookmark | None:
        self.calls.append(("tag_bookmark", {"bookmark_id": bookmark_id, "tag_slugs": tag_slugs}))
        return self.tagged_bookmark

    async def untag_bookmark(self, bookmark_id: int | str, tag_slugs: list[str]) -> Bookmark | None:
        self.calls.append(("untag_bookmark", {"bookmark_id": bookmark_id, "tag_slugs": tag_slugs}))
        return self.untagged_bookmark


# ── Tests ──────────────────────────────────────────────────────────


async def test_get_all_forwards():
    db = _FakeBackend()
    assert await taxonomy.get_all(db=db) == []
    assert db.calls == [("get_all_tags", {})]


async def test_search_forwards_query():
    db = _FakeBackend()
    await taxonomy.search(db=db, query="py")
    assert db.calls == [("search_tags", {"query": "py"})]


async def test_get_by_slug_forwards():
    existing = Tag(slug="py", name="Python")
    db = _FakeBackend(tag_by_slug=existing)
    assert await taxonomy.get_by_slug(db=db, slug="py") is existing


async def test_create_returns_existing_when_slug_taken():
    existing = Tag(slug="py", name="Python")
    db = _FakeBackend(tag_by_slug=existing)
    tag, created = await taxonomy.create(db=db, slug="py", name="Python")
    assert created is False
    assert tag is existing
    # Should not have called create_tag because slug existed
    assert not any(name == "create_tag" for name, _ in db.calls)


async def test_create_inserts_new_tag_when_absent():
    db = _FakeBackend(tag_by_slug=None)
    tag, created = await taxonomy.create(db=db, slug="py", name="Python", description="lang")
    assert created is True
    assert tag is not None and tag.slug == "py"
    assert ("create_tag", {"slug": "py", "name": "Python", "description": "lang"}) in db.calls


async def test_update_forwards_kwargs():
    db = _FakeBackend(updated_tag=Tag(slug="py", name="Python"))
    out = await taxonomy.update(db=db, slug="py", new_name="Py", new_description="d")
    assert out is db.updated_tag
    assert db.calls[-1] == (
        "update_tag",
        {"slug": "py", "new_name": "Py", "new_description": "d"},
    )


async def test_delete_returns_backend_flag():
    db = _FakeBackend(deleted_ok=True)
    assert await taxonomy.delete(db=db, slug="py") is True
    assert ("delete_tag", {"slug": "py"}) in db.calls


async def test_delete_false_does_not_log_delete():
    db = _FakeBackend(deleted_ok=False)
    assert await taxonomy.delete(db=db, slug="py") is False


async def test_merge_returns_backend_dict():
    db = _FakeBackend(merge_result={"bookmarks_reassigned": 5})
    result = await taxonomy.merge(db=db, source_slug="a", target_slug="b")
    assert result == {"bookmarks_reassigned": 5}
    assert ("merge_tags", {"source": "a", "target": "b"}) in db.calls


async def test_tag_bookmark_forwards():
    db = _FakeBackend()
    await taxonomy.tag_bookmark(db=db, bookmark_id=42, tag_slugs=["a", "b"])
    assert ("tag_bookmark", {"bookmark_id": 42, "tag_slugs": ["a", "b"]}) in db.calls


async def test_untag_bookmark_forwards():
    db = _FakeBackend()
    await taxonomy.untag_bookmark(db=db, bookmark_id="x", tag_slugs=["a"])
    assert ("untag_bookmark", {"bookmark_id": "x", "tag_slugs": ["a"]}) in db.calls


# ── normalize_and_validate_tags (admin tag editing, Phase 1) ─────────


def test_normalize_strips_hash_lowercases_trims_and_hyphenates():
    tags, err = taxonomy.normalize_and_validate_tags(["#Machine Learning", "  PyThOn  "])
    assert err is None
    assert tags == ["machine-learning", "python"]


def test_normalize_dedupes_preserving_order():
    tags, err = taxonomy.normalize_and_validate_tags(["python", "#python", "web"])
    assert err is None
    assert tags == ["python", "web"]


def test_normalize_allows_empty_list():
    """Replace-set with [] clears the bookmark's tags."""
    tags, err = taxonomy.normalize_and_validate_tags([])
    assert err is None
    assert tags == []


def test_normalize_rejects_invalid_slug_characters():
    tags, err = taxonomy.normalize_and_validate_tags(["bad_tag"])
    assert tags is None
    assert err is not None and "bad_tag" in err


def test_normalize_rejects_double_hyphen_from_multiple_spaces():
    tags, _err = taxonomy.normalize_and_validate_tags(["a  b"])
    assert tags is None  # "a--b" fails the slug regex


def test_normalize_rejects_over_30_chars():
    tags, err = taxonomy.normalize_and_validate_tags(["x" * 31])
    assert tags is None
    assert err is not None and "long" in err


def test_normalize_rejects_more_than_10_tags():
    tags, err = taxonomy.normalize_and_validate_tags([f"tag-{i}" for i in range(11)])
    assert tags is None
    assert err is not None and "Too many" in err


def test_normalize_rejects_empty_after_normalization():
    tags, _err = taxonomy.normalize_and_validate_tags(["#"])
    assert tags is None
