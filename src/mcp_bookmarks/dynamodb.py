"""DynamoDB backend for mcp-bookmarks.

Connects mcp-bookmarks to the same blogmarks-links / blogmarks-tags tables
used by the blogmarks PWA. Activate with DYNAMODB_MODE=true and set:

  DYNAMODB_LINKS_TABLE   (default: blogmarks-links)
  DYNAMODB_TAGS_TABLE    (default: blogmarks-tags)
  DYNAMODB_USER_ID       (default: mcp-agent)  — userId tagged on saved bookmarks
  AWS_DEFAULT_REGION     (default: us-east-1)
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from .models import Bookmark, Tag

if TYPE_CHECKING:
    from .models import Tenant

_LINKS_TABLE = os.environ.get("DYNAMODB_LINKS_TABLE", "blogmarks-links")
_TAGS_TABLE = os.environ.get("DYNAMODB_TAGS_TABLE", "blogmarks-tags")
_ORG_LEGACY = os.environ.get("DYNAMODB_ORG_INCLUDE_LEGACY", "").lower() in ("1", "true", "yes")


def _dynamo():
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def _run(fn, *args, **kwargs):
    """Run a synchronous boto3 call in a thread executor."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(fn, *args, **kwargs))


def _to_tag(item: dict) -> Tag:
    return Tag(
        slug=item["slug"],
        name=item.get("name", item["slug"]),
        description=item.get("description", ""),
        usage_count=int(item.get("usage_count", 0)),
        created_at=item.get("created_at"),
    )


def _to_bookmark(item: dict) -> Bookmark:
    return Bookmark(
        id=None,  # no integer id in DynamoDB
        dynamo_id=item.get("id"),
        url=item["url"],
        title=item.get("title") or item.get("url"),
        description=item.get("description") or item.get("aiSummary"),
        image_url=item.get("image_url"),
        site_name=item.get("site_name"),
        summary=item.get("aiSummary"),
        content=item.get("aiContent"),
        word_count=int(item.get("aiWordCount", 0)) or None,
        tags=list(item.get("aiTags", [])),
        created_at=item.get("savedAt"),
        updated_at=item.get("aiProcessedAt") or item.get("savedAt"),
    )


class DynamoDBDatabase:
    """Drop-in async replacement for Database that reads/writes DynamoDB.

    Pass a ``Tenant`` for per-request isolation; omit (or pass ``None``) to
    fall back to the module-level ``DYNAMODB_ORG_ID`` / ``DYNAMODB_USER_ID``
    env vars for backward compatibility with single-tenant deployments.
    """

    def __init__(self, tenant: "Tenant | None" = None):
        from .models import Tenant as _Tenant  # avoid circular at module level

        if tenant is None:
            org_id = os.environ.get("DYNAMODB_ORG_ID", "").strip() or None
            user_id = os.environ.get("DYNAMODB_USER_ID", "mcp-agent")
            tenant = _Tenant(organization_id=org_id or "default", user_id=user_id)

        self._tenant = tenant
        db = _dynamo()
        self._links = db.Table(_LINKS_TABLE)
        self._tags = db.Table(_TAGS_TABLE)

    # ── Per-instance tenant helpers ────────────────────────────────

    def _org_id(self) -> str | None:
        """Return organization_id if it's a real org, else None (meaning 'all')."""
        org = self._tenant.organization_id
        return org if org and org != "default" else None

    def _user_id(self) -> str:
        return self._tenant.user_id or os.environ.get("DYNAMODB_USER_ID", "mcp-agent")

    def _tenant_filter_expr(self):
        """Scope scans to this tenant's org (optional legacy items without attribute)."""
        org_id = self._org_id()
        if not org_id:
            return None
        if _ORG_LEGACY:
            return Attr("organization_id").eq(org_id) | Attr("organization_id").not_exists()
        return Attr("organization_id").eq(org_id)

    def _base_link_filter(self):
        fe = Attr("url").exists() & Attr("rateLimitKey").not_exists()
        tf = self._tenant_filter_expr()
        return fe & tf if tf is not None else fe

    def _item_org_visible(self, item: dict) -> bool:
        org_id = self._org_id()
        if not org_id:
            return True
        oid = item.get("organization_id")
        if oid == org_id:
            return True
        if oid is None and _ORG_LEGACY:
            return True
        return False

    async def connect(self) -> None:
        pass  # DynamoDB is serverless

    async def close(self) -> None:
        pass

    # ── Tags ──────────────────────────────────────────────────────────

    async def get_all_tags(self) -> list[Tag]:
        def _scan():
            resp = self._tags.scan(
                ProjectionExpression="slug, #n, description, usage_count",
                ExpressionAttributeNames={"#n": "name"},
            )
            return resp.get("Items", [])

        items = await _run(_scan)
        return sorted([_to_tag(i) for i in items], key=lambda t: t.usage_count, reverse=True)

    async def search_tags(self, query: str) -> list[Tag]:
        all_tags = await self.get_all_tags()
        q = query.lower()
        return [t for t in all_tags if q in t.slug or q in t.name.lower() or q in t.description.lower()]

    async def create_tag(self, slug: str, name: str, description: str = "") -> Tag:
        def _put():
            self._tags.put_item(
                Item={
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "usage_count": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ConditionExpression=Attr("slug").not_exists(),
            )

        await _run(_put)
        return Tag(slug=slug, name=name, description=description, usage_count=0)

    async def get_tag_by_slug(self, slug: str) -> Tag | None:
        def _get():
            return self._tags.get_item(Key={"slug": slug}).get("Item")

        item = await _run(_get)
        return _to_tag(item) if item else None

    async def update_tag(self, slug: str, new_name: str | None = None, new_description: str | None = None) -> Tag | None:
        tag = await self.get_tag_by_slug(slug)
        if not tag:
            return None
        updates, names, values = [], {}, {}
        if new_name is not None:
            updates.append("#n = :name")
            names["#n"] = "name"
            values[":name"] = new_name
        if new_description is not None:
            updates.append("description = :desc")
            values[":desc"] = new_description
        if not updates:
            return tag

        expr = "SET " + ", ".join(updates)

        def _update():
            self._tags.update_item(
                Key={"slug": slug},
                UpdateExpression=expr,
                ExpressionAttributeNames=names or None,
                ExpressionAttributeValues=values,
            )

        await _run(_update)
        return await self.get_tag_by_slug(slug)

    async def delete_tag(self, slug: str) -> bool:
        tag = await self.get_tag_by_slug(slug)
        if not tag:
            return False

        # DynamoDB: update each bookmark to remove the tag from aiTags list
        # (Scan for bookmarks with this tag, then update each)
        def _scan_with_tag():
            return self._links.scan(
                FilterExpression=Attr("aiTags").contains(slug),
                ProjectionExpression="#id",
                ExpressionAttributeNames={"#id": "id"},
            ).get("Items", [])

        items = await _run(_scan_with_tag)
        for item in items:
            bk_id = item["id"]
            # Fetch full item to rebuild the tag list
            def _get_bk(bid=bk_id):
                return self._links.get_item(Key={"id": bid}).get("Item", {})
            full = await _run(_get_bk)
            new_tags = [t for t in full.get("aiTags", []) if t != slug]

            def _upd(bid=bk_id, tags=new_tags):
                self._links.update_item(
                    Key={"id": bid},
                    UpdateExpression="SET aiTags = :tags",
                    ExpressionAttributeValues={":tags": tags},
                )
            await _run(_upd)

        def _del():
            self._tags.delete_item(Key={"slug": slug})

        await _run(_del)
        return True

    async def merge_tags(self, source_slug: str, target_slug: str) -> dict:
        source = await self.get_tag_by_slug(source_slug)
        target = await self.get_tag_by_slug(target_slug)
        if not source:
            raise ValueError(f"Source tag '{source_slug}' not found.")
        if not target:
            raise ValueError(f"Target tag '{target_slug}' not found.")

        def _scan():
            return self._links.scan(
                FilterExpression=Attr("aiTags").contains(source_slug),
                ProjectionExpression="#id, aiTags",
                ExpressionAttributeNames={"#id": "id"},
            ).get("Items", [])

        items = await _run(_scan)
        reassigned = 0
        for item in items:
            bk_id = item["id"]
            tags = set(item.get("aiTags", []))
            tags.discard(source_slug)
            tags.add(target_slug)
            new_tags = list(tags)

            def _upd(bid=bk_id, t=new_tags):
                self._links.update_item(
                    Key={"id": bid},
                    UpdateExpression="SET aiTags = :tags",
                    ExpressionAttributeValues={":tags": t},
                )
            await _run(_upd)
            reassigned += 1

        await self.delete_tag(source_slug)
        return {"source_deleted": source_slug, "target": target_slug, "bookmarks_reassigned": reassigned}

    # ── Bookmarks ─────────────────────────────────────────────────────

    async def upsert_bookmark(
        self,
        url: str,
        title: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        site_name: str | None = None,
    ) -> Bookmark:
        now = datetime.now(timezone.utc).isoformat()
        bk_id = str(uuid.uuid4())

        org_id = self._org_id()
        user_id = self._user_id()

        def _put():
            self._links.put_item(
                Item={
                    k: v for k, v in {
                        "id": bk_id,
                        "url": url,
                        "title": title,
                        "description": description,
                        "image_url": image_url,
                        "site_name": site_name,
                        "savedAt": now,
                        "userId": user_id,
                        "source": "mcp",
                        "sourceIp": "127.0.0.1",
                        "organization_id": org_id,
                    }.items() if v is not None
                },
                ConditionExpression=Attr("id").not_exists(),
            )

        await _run(_put)
        return Bookmark(
            id=None,
            dynamo_id=bk_id,
            url=url,
            title=title,
            description=description,
            image_url=image_url,
            site_name=site_name,
            tags=[],
            created_at=now,
        )

    def _dynamo_key(self, bookmark_id: int | str) -> str | None:
        if isinstance(bookmark_id, str):
            s = bookmark_id.strip()
            return s if s else None
        return None

    async def get_bookmark_by_id(self, bookmark_id: int | str) -> Bookmark | None:
        key = self._dynamo_key(bookmark_id)
        if not key:
            return None

        def _get():
            return self._links.get_item(Key={"id": key}).get("Item")

        item = await _run(_get)
        if not item or not item.get("url"):
            return None
        if not self._item_org_visible(item):
            return None
        return _to_bookmark(item)

    _MAX_AI_CONTENT_CHARS = 350_000

    async def set_bookmark_content(self, bookmark_id: int | str, content: str, word_count: int) -> None:
        key = self._dynamo_key(bookmark_id)
        if not key:
            return

        def _peek():
            return self._links.get_item(Key={"id": key}).get("Item")

        existing = await _run(_peek)
        if not existing or not self._item_org_visible(existing):
            return
        text_body = (content or "")[: self._MAX_AI_CONTENT_CHARS]
        wc = word_count if word_count else len(text_body.split())
        now = datetime.now(timezone.utc).isoformat()

        def _upd():
            self._links.update_item(
                Key={"id": key},
                UpdateExpression="SET aiContent = :c, aiWordCount = :w, aiProcessedAt = :now",
                ExpressionAttributeValues={":c": text_body, ":w": wc, ":now": now},
            )

        await _run(_upd)

    async def set_bookmark_summary(self, bookmark_id: int | str, summary: str) -> None:
        key = self._dynamo_key(bookmark_id)
        if not key:
            return

        def _peek():
            return self._links.get_item(Key={"id": key}).get("Item")

        existing = await _run(_peek)
        if not existing or not self._item_org_visible(existing):
            return
        now = datetime.now(timezone.utc).isoformat()

        def _upd():
            self._links.update_item(
                Key={"id": key},
                UpdateExpression="SET aiSummary = :s, aiProcessedAt = :now",
                ExpressionAttributeValues={":s": summary, ":now": now},
            )

        await _run(_upd)

    async def tag_bookmark(self, bookmark_id: int | str, tag_slugs: list[str]) -> Bookmark | None:
        key = self._dynamo_key(bookmark_id)
        if not key:
            return None
        for slug in tag_slugs:
            t = await self.get_tag_by_slug(slug)
            if not t:
                raise ValueError(f"Tag '{slug}' does not exist. Call create_tag first.")

        def _get():
            return self._links.get_item(Key={"id": key}).get("Item")

        item = await _run(_get)
        if not item or not item.get("url") or not self._item_org_visible(item):
            return None
        existing = list(item.get("aiTags", []))
        merged = list(dict.fromkeys(existing + list(tag_slugs)))

        def _upd():
            self._links.update_item(
                Key={"id": key},
                UpdateExpression="SET aiTags = :tags, aiProcessedAt = :now",
                ExpressionAttributeValues={
                    ":tags": merged,
                    ":now": datetime.now(timezone.utc).isoformat(),
                },
            )

        await _run(_upd)
        return await self.get_bookmark_by_id(key)

    async def untag_bookmark(self, bookmark_id: int | str, tag_slugs: list[str]) -> Bookmark | None:
        key = self._dynamo_key(bookmark_id)
        if not key:
            return None

        def _get():
            return self._links.get_item(Key={"id": key}).get("Item")

        item = await _run(_get)
        if not item or not item.get("url") or not self._item_org_visible(item):
            return None
        remove = set(tag_slugs)
        new_tags = [t for t in item.get("aiTags", []) if t not in remove]

        def _upd():
            self._links.update_item(
                Key={"id": key},
                UpdateExpression="SET aiTags = :tags, aiProcessedAt = :now",
                ExpressionAttributeValues={
                    ":tags": new_tags,
                    ":now": datetime.now(timezone.utc).isoformat(),
                },
            )

        await _run(_upd)
        return await self.get_bookmark_by_id(key)

    async def search_bookmarks(
        self, query: str | None = None, tag: str | None = None, limit: int = 20
    ) -> list[Bookmark]:
        def _scan():
            kwargs: dict[str, Any] = {"Limit": limit}
            base = self._base_link_filter()
            if tag and query:
                kwargs["FilterExpression"] = base & (
                    Attr("aiTags").contains(tag)
                    & (
                        Attr("title").contains(query)
                        | Attr("aiSummary").contains(query)
                        | Attr("url").contains(query)
                    )
                )
            elif tag:
                kwargs["FilterExpression"] = base & Attr("aiTags").contains(tag)
            elif query:
                kwargs["FilterExpression"] = base & (
                    Attr("title").contains(query)
                    | Attr("aiSummary").contains(query)
                    | Attr("url").contains(query)
                )
            else:
                kwargs["FilterExpression"] = base
            return self._links.scan(**kwargs).get("Items", [])

        items = await _run(_scan)
        bookmarks = [
            _to_bookmark(i)
            for i in items
            if i.get("url") and not i.get("rateLimitKey") and self._item_org_visible(i)
        ]
        return sorted(bookmarks, key=lambda b: str(b.created_at or ""), reverse=True)

    async def get_stats(self) -> dict:
        def _counts():
            bk = self._links.scan(Select="COUNT").get("Count", 0)
            tg = self._tags.scan(Select="COUNT").get("Count", 0)
            return bk, tg

        bk, tg = await _run(_counts)
        return {"total_bookmarks": bk, "total_tags": tg}

    async def delete_bookmark(self, bookmark_id: int | str) -> bool:
        key = self._dynamo_key(bookmark_id)
        if not key:
            return False

        def _peek():
            return self._links.get_item(Key={"id": key}).get("Item")

        existing = await _run(_peek)
        if not existing or not self._item_org_visible(existing):
            return False

        def _del():
            self._links.delete_item(Key={"id": key})

        await _run(_del)
        return True

    async def get_all_bookmarks(self) -> list[Bookmark]:
        def _scan():
            return self._links.scan(FilterExpression=self._base_link_filter()).get("Items", [])

        items = await _run(_scan)
        return [_to_bookmark(i) for i in items if self._item_org_visible(i)]

    async def get_full_export(self) -> dict:
        bookmarks = await self.get_all_bookmarks()
        tags = await self.get_all_tags()
        stats = await self.get_stats()
        return {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "tags": [t.model_dump(mode="json") for t in tags],
            "bookmarks": [
                {
                    "url": b.url,
                    "title": b.title,
                    "description": b.description,
                    "summary": b.summary,
                    "word_count": b.word_count,
                    "tags": b.tags,
                    "created_at": str(b.created_at),
                }
                for b in bookmarks
            ],
        }
