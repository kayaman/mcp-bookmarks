"""DynamoDB backend for mcp-bookmarks.

Activate with DYNAMODB_MODE=true and set:

  DYNAMODB_LINKS_TABLE   (default: mcp-bookmarks-links)
  DYNAMODB_TAGS_TABLE    (default: mcp-bookmarks-tags)
  DYNAMODB_USER_ID       (default: mcp-agent)  — userId tagged on saved bookmarks
  AWS_DEFAULT_REGION     (default: us-east-1)
"""

import asyncio
import contextlib
import os
import uuid
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Attr

from .backend import DYNAMODB_CAPABILITIES
from .models import Bookmark, Tag

if TYPE_CHECKING:
    from .models import Tenant

_LINKS_TABLE = os.environ.get("DYNAMODB_LINKS_TABLE", "mcp-bookmarks-links")
_TAGS_TABLE = os.environ.get("DYNAMODB_TAGS_TABLE", "mcp-bookmarks-tags")
_ORG_LEGACY = os.environ.get("DYNAMODB_ORG_INCLUDE_LEGACY", "").lower() in ("1", "true", "yes")
# Optional GSI keyed (userId, bookmarkType) for O(1) per-user type queries. Most
# tables don't have it (blogmarks-links only has userId-savedAt-index), so it's
# opt-in via env and unset by default.
_TYPE_INDEX = os.environ.get("DYNAMODB_TYPE_INDEX", "").strip()
# GSI keyed on userId (SK savedAt) — present on blogmarks-links. When the type
# index is absent, query this by userId and filter bookmarkType server-side
# (scans only the owner's rows, not the whole table). Unset → full-table scan.
_USER_INDEX = os.environ.get("DYNAMODB_USER_INDEX", "userId-savedAt-index").strip()


def _dynamo():
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))


def _tag_edits_table_name() -> str:
    """Tag-edit event log table. Read at call time (usage_meter env pattern,
    but call-time so tests can monkeypatch.setenv without module re-import)."""
    return os.environ.get("TAG_EDITS_TABLE", "").strip() or "blogmarks-tag-edits"


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
        deprecated_as=item.get("deprecated_as") or None,
    )


def _to_bookmark(item: dict) -> Bookmark:
    """Map a DDB item to a ``Bookmark``.

    Reads camelCase (``ogTitle``, ``ogDescription``, ``ogImage``, ``ogSiteName``)
    preferentially with a snake_case fallback (``description``, ``image_url``,
    ``site_name``) so both shapes surface correctly.
    """
    og_title = item.get("ogTitle")
    og_description = item.get("ogDescription")
    og_image = item.get("ogImage")
    og_site_name = item.get("ogSiteName")
    legacy_description = item.get("description")
    legacy_image_url = item.get("image_url")
    legacy_site_name = item.get("site_name")
    ai_summary = item.get("aiSummary")
    ai_content = item.get("aiContent")
    ai_word_count = int(item.get("aiWordCount", 0))
    ai_tags_list = list(item.get("aiTags", []))
    return Bookmark(
        id=None,  # no integer id in DynamoDB
        dynamo_id=item.get("id"),
        url=item["url"],
        title=og_title or item.get("title") or item.get("url"),
        # snake_case (back-compat for tools that still consume them):
        description=og_description or legacy_description or ai_summary,
        image_url=og_image or legacy_image_url,
        site_name=og_site_name or legacy_site_name,
        summary=ai_summary,
        content=ai_content,
        word_count=ai_word_count or None,
        tags=ai_tags_list,
        created_at=item.get("savedAt"),
        updated_at=item.get("aiProcessedAt") or item.get("savedAt"),
        # camelCase aliases (for camelCase consumers):
        og_title=og_title,
        og_description=og_description,
        og_image=og_image,
        og_site_name=og_site_name,
        ai_summary=ai_summary,
        ai_tags=ai_tags_list,
        ai_image=item.get("aiImage"),
        ai_content=ai_content,
        ai_word_count=ai_word_count,
        ai_status=item.get("aiStatus"),
        ai_error=item.get("aiError"),
        bookmark_type=item.get("bookmarkType"),
        bookmark_type_confidence=item.get("bookmarkTypeConfidence"),
        # YouTube deep-extraction (blogmarks async-workers): fuller transcript +
        # structured chapters/claims/entities live inline on the links row.
        deep_text=item.get("deepText"),
        ai_chapters=item.get("aiChapters"),
        ai_claims=item.get("aiClaims"),
        entities=item.get("entities"),
        original_url=item.get("originalUrl"),
        saved_at=item.get("savedAt"),
        source=item.get("source"),
        # ownership + share metadata
        notes=item.get("notes"),
        mcp_exposed=item.get("mcpExposed"),
        visibility=item.get("visibility"),
        share_token=item.get("shareToken"),
        scraping_status=item.get("scrapingStatus"),
        scraped_at=item.get("scrapedAt"),
        ai_processed_at=item.get("aiProcessedAt"),
        ai_enrichment_attempts=item.get("aiEnrichmentAttempts"),
    )


class DynamoDBDatabase:
    """Drop-in async replacement for Database that reads/writes DynamoDB.

    Pass a ``Tenant`` for per-request isolation; omit (or pass ``None``) to
    fall back to the module-level ``DYNAMODB_ORG_ID`` / ``DYNAMODB_USER_ID``
    env vars for backward compatibility with single-tenant deployments.

    Conforms to :class:`mcp_bookmarks.backend.BookmarkBackend` (see that
    module for the documented contract).
    """

    # Capability matrix for the DynamoDB backend.
    # See src/mcp_bookmarks/backend.py for the documented flags.
    capabilities = DYNAMODB_CAPABILITIES

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
        # Per-request identity (set by BearerAuthMiddleware on authenticated
        # JWT or scoped-token traffic) takes precedence over the lifespan-level
        # default. Falls back to the static env var so single-tenant stdio
        # deployments behave exactly as before.
        from .request_context import current_user_id

        req_uid = current_user_id()
        if req_uid:
            return req_uid
        return self._tenant.user_id or os.environ.get("DYNAMODB_USER_ID", "mcp-agent")

    def _request_user_filter(self):
        """If a request user is in scope, return ``Attr("userId").eq(uid)``.

        Returns ``None`` when there's no per-request identity — single-tenant
        deployments keep their existing behaviour (org_id only).
        """
        from .request_context import current_user_id

        req_uid = current_user_id()
        if not req_uid:
            return None
        return Attr("userId").eq(req_uid)

    def _tenant_filter_expr(self):
        """Scope scans to this tenant's org (optional legacy items without attribute)."""
        org_id = self._org_id()
        if not org_id:
            return None
        if _ORG_LEGACY:
            return Attr("organization_id").eq(org_id) | Attr("organization_id").not_exists()
        return Attr("organization_id").eq(org_id)

    def _scope_condition(self):
        """Scope filter for scoped-token (agent) reads — parity with read-mcp.

        Mirrors ``buildScopeFilter`` (blogmarks/lambda/read-mcp/index.mjs:461):
          - **exposure gate** — exclude per-bookmark ``mcpExposed == false``;
          - **tags allowlist** — for a ``{"type": "tags", "tags": [...]}`` scope,
            require at least one allow-listed tag in ``aiTags``.

        Returns ``None`` when there is no per-connection scope (Cognito/owner or
        stdio traffic) so first-party reads still see every bookmark — the
        exposure gate is an *agent-access* control, never applied to the owner.
        """
        from .request_context import current_scope

        scope = current_scope()
        if scope is None:
            return None
        # Exposure gate: absent attribute counts as exposed (matches read-mcp).
        cond = Attr("mcpExposed").not_exists() | Attr("mcpExposed").eq(True)
        if scope.get("type") == "tags":
            tags = [t for t in (scope.get("tags") or []) if t]
            if tags:  # empty list ⇒ treat as all_private (exposure only)
                tag_cond = None
                for t in tags:
                    c = Attr("aiTags").contains(t)
                    tag_cond = c if tag_cond is None else (tag_cond | c)
                cond = cond & tag_cond
        return cond

    def _item_scope_visible(self, item: dict) -> bool:
        """Python-side equivalent of :meth:`_scope_condition` for single items."""
        from .request_context import current_scope

        scope = current_scope()
        if scope is None:
            return True
        if item.get("mcpExposed") is False:
            return False
        if scope.get("type") == "tags":
            tags = {t for t in (scope.get("tags") or []) if t}
            if tags and not (tags & set(item.get("aiTags", []))):
                return False
        return True

    def _base_link_filter(self):
        fe = Attr("url").exists() & Attr("rateLimitKey").not_exists()
        tf = self._tenant_filter_expr()
        if tf is not None:
            fe = fe & tf
        uf = self._request_user_filter()
        if uf is not None:
            fe = fe & uf
        sc = self._scope_condition()
        if sc is not None:
            fe = fe & sc
        return fe

    def _item_org_visible(self, item: dict) -> bool:
        org_id = self._org_id()
        if org_id:
            oid = item.get("organization_id")
            if oid == org_id or (oid is None and _ORG_LEGACY):
                pass
            else:
                return False
        # Per-request user scope: if set, the item must belong to that user.
        from .request_context import current_user_id

        req_uid = current_user_id()
        return not (req_uid and item.get("userId") != req_uid)

    async def connect(self) -> None:
        pass  # DynamoDB is serverless

    async def close(self) -> None:
        pass

    # ── Tags ──────────────────────────────────────────────────────────

    async def get_all_tags(self) -> list[Tag]:
        def _scan():
            resp = self._tags.scan(
                ProjectionExpression="slug, #n, description, usage_count, deprecated_as",
                ExpressionAttributeNames={"#n": "name"},
            )
            return resp.get("Items", [])

        items = await _run(_scan)
        live = [i for i in items if not i.get("deprecated_as")]
        return sorted([_to_tag(i) for i in live], key=lambda t: t.usage_count, reverse=True)

    async def search_tags(self, query: str) -> list[Tag]:
        all_tags = await self.get_all_tags()
        q = query.lower()
        return [
            t for t in all_tags if q in t.slug or q in t.name.lower() or q in t.description.lower()
        ]

    async def create_tag(self, slug: str, name: str, description: str = "") -> Tag:
        def _put():
            self._tags.put_item(
                Item={
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "usage_count": 0,
                    "created_at": datetime.now(UTC).isoformat(),
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

    async def update_tag(
        self, slug: str, new_name: str | None = None, new_description: str | None = None
    ) -> Tag | None:
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
            kwargs: dict[str, Any] = {
                "Key": {"slug": slug},
                "UpdateExpression": expr,
                "ExpressionAttributeValues": values,
            }
            if names:
                # boto3 errors on ExpressionAttributeNames=None when other
                # transformations want to merge generated placeholders in,
                # so only include the kwarg when we actually have names.
                kwargs["ExpressionAttributeNames"] = names
            self._tags.update_item(**kwargs)

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

        # Phase 2: tombstone instead of delete (blogmarks convention — tag
        # rows are never hard-deleted; assignTags resolves deprecated_as
        # chains at assignment time). Skips delete_tag's redundant
        # per-bookmark sweep: the loop above already rewrote every row.
        await self.tombstone_tag(source_slug, target_slug)
        return {
            "source_deleted": source_slug,
            "target": target_slug,
            "bookmarks_reassigned": reassigned,
        }

    async def tombstone_tag(self, slug: str, target: str) -> None:
        """Tombstone a tag row: SET deprecated_as = target, zero usage_count.

        Never deletes. UpdateItem upserts, so callers must verify the row
        exists first (merge / recalibrate apply both do).
        """

        def _upd():
            self._tags.update_item(
                Key={"slug": slug},
                UpdateExpression="SET deprecated_as = :t, usage_count = :zero",
                ExpressionAttributeValues={":t": target, ":zero": 0},
            )

        await _run(_upd)

    # ── Bookmarks ─────────────────────────────────────────────────────

    async def upsert_bookmark(
        self,
        url: str,
        title: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        site_name: str | None = None,
        *,
        bookmark_type: str | None = None,
        flow_id: str | None = None,
        source: str | None = None,
    ) -> Bookmark:
        """Insert a new bookmark.

        Writes the canonical camelCase OG keys (``ogTitle``, ``ogDescription``,
        ``ogImage``, ``ogSiteName``). The ``description``/``image_url``/
        ``site_name`` arguments are kept for back-compat with existing callers
        (the bookmarklet, REST `/api/save`, Claude/Cursor sessions); they map
        to the OG.* fields rather than living under their snake_case names.
        """
        now = datetime.now(UTC).isoformat()
        bk_id = str(uuid.uuid4())

        org_id = self._org_id()
        user_id = self._user_id()

        def _put():
            self._links.put_item(
                Item={
                    k: v
                    for k, v in {
                        "id": bk_id,
                        "url": url,
                        "title": title,
                        # camelCase OG (canonical shape for camelCase consumers)
                        "ogTitle": title,
                        "ogDescription": description,
                        "ogImage": image_url,
                        "ogSiteName": site_name,
                        # bookmarkType + flowId pass through if the caller supplied them
                        "bookmarkType": bookmark_type,
                        "flowId": flow_id,
                        "savedAt": now,
                        "userId": user_id,
                        "source": source or "mcp",
                        "sourceIp": "127.0.0.1",
                        "organization_id": org_id,
                    }.items()
                    if v is not None
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
            # camelCase fields populated so callers get a complete
            # SavedResponse from save_bookmark without a follow-up read.
            og_title=title,
            og_description=description,
            og_image=image_url,
            og_site_name=site_name,
            bookmark_type=bookmark_type,
            saved_at=now,
            source=source or "mcp",
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
        if not self._item_scope_visible(item):
            return None
        return _to_bookmark(item)

    _MAX_AI_CONTENT_CHARS = 350_000

    async def set_bookmark_content(
        self, bookmark_id: int | str, content: str, word_count: int
    ) -> None:
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
        now = datetime.now(UTC).isoformat()

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
        now = datetime.now(UTC).isoformat()

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
                    ":now": datetime.now(UTC).isoformat(),
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
                    ":now": datetime.now(UTC).isoformat(),
                },
            )

        await _run(_upd)
        return await self.get_bookmark_by_id(key)

    # ── Admin tag editing (Phase 1) ───────────────────────────────────

    async def replace_bookmark_tags(
        self, bookmark_id: int | str, tags: list[str], *, actor: str = "human"
    ) -> dict | None:
        """Replace-set aiTags with snapshot semantics.

        One UpdateItem does the whole links-row mutation:
          - ``aiTagsOriginal = if_not_exists(aiTagsOriginal, :orig)`` — written
            by the FIRST mutation of any kind, never updated afterwards;
          - ``tagsReviewedAt = if_not_exists(tagsReviewedAt, :now)`` — human
            edits only (clause omitted for other actors).
        Then usage_count is reconciled ±1 (net-new code in DynamoDB mode) and
        one edit event is appended to TAG_EDITS_TABLE.
        """
        key = self._dynamo_key(bookmark_id)
        if not key:
            return None

        def _get():
            return self._links.get_item(Key={"id": key}).get("Item")

        item = await _run(_get)
        if not item or not item.get("url") or not self._item_org_visible(item):
            return None

        before = list(item.get("aiTags", []))
        after = list(dict.fromkeys(tags))
        added = [t for t in after if t not in before]
        removed = [t for t in before if t not in after]
        now = datetime.now(UTC).isoformat()

        expr = "SET aiTags = :tags, aiTagsOriginal = if_not_exists(aiTagsOriginal, :orig)"
        values: dict[str, Any] = {":tags": after, ":orig": before}
        if actor == "human":
            expr += ", tagsReviewedAt = if_not_exists(tagsReviewedAt, :now)"
            values[":now"] = now

        def _upd():
            self._links.update_item(
                Key={"id": key}, UpdateExpression=expr, ExpressionAttributeValues=values
            )

        await _run(_upd)
        await self._reconcile_tag_usage(added=added, removed=removed)
        await self._put_tag_edit_event(
            bookmark_id=key,
            before=before,
            after=after,
            added=added,
            removed=removed,
            actor=actor,
            ts=now,
        )
        return {
            "bookmark_id": key,
            "before": before,
            "after": after,
            "added": added,
            "removed": removed,
        }

    async def _reconcile_tag_usage(self, *, added: list[str], removed: list[str]) -> None:
        """±1 usage_count per added/removed tag; create missing rows at 1.

        Drift risk on failure between the links write and here is accepted —
        blogmarks' weekly recomputeUsageCounts() is the self-heal (currently
        disabled in prod; see spec Known risks).
        """
        from botocore.exceptions import ClientError

        for slug in added:
            if await self.get_tag_by_slug(slug) is None:

                def _put(s=slug):
                    self._tags.put_item(
                        Item={
                            "slug": s,
                            "name": s,
                            "description": "",
                            "usage_count": 1,
                            "created_at": datetime.now(UTC).isoformat(),
                        }
                    )

                await _run(_put)
            else:

                def _inc(s=slug):
                    self._tags.update_item(
                        Key={"slug": s},
                        UpdateExpression="ADD usage_count :one",
                        ExpressionAttributeValues={":one": 1},
                    )

                await _run(_inc)
        for slug in removed:

            def _dec(s=slug):
                try:
                    self._tags.update_item(
                        Key={"slug": s},
                        UpdateExpression="SET usage_count = usage_count - :one",
                        ConditionExpression="attribute_exists(slug) AND usage_count >= :one",
                        ExpressionAttributeValues={":one": 1},
                    )
                except ClientError as exc:  # missing row / already 0 → skip
                    if (
                        exc.response.get("Error", {}).get("Code")
                        != "ConditionalCheckFailedException"
                    ):
                        raise

            await _run(_dec)

    async def _put_tag_edit_event(
        self,
        *,
        bookmark_id: str,
        before: list[str],
        after: list[str],
        added: list[str],
        removed: list[str],
        actor: str,
        ts: str,
    ) -> None:
        user_id = self._user_id()

        def _put():
            _dynamo().Table(_tag_edits_table_name()).put_item(
                Item={
                    "userId": user_id,
                    "sk": f"{ts}#{bookmark_id}",
                    "bookmarkId": bookmark_id,
                    "before": before,
                    "after": after,
                    "added": added,
                    "removed": removed,
                    "actor": actor,
                    "ts": ts,
                }
            )

        await _run(_put)

    async def get_tag_edits(self, limit: int = 100) -> list[dict]:
        """Edit history, newest first — single PK Query on userId."""
        from boto3.dynamodb.conditions import Key

        user_id = self._user_id()
        lim = max(1, min(int(limit), 1000))

        def _query():
            resp = _dynamo().Table(_tag_edits_table_name()).query(
                KeyConditionExpression=Key("userId").eq(user_id),
                ScanIndexForward=False,
                Limit=lim,
            )
            return resp.get("Items", [])

        items = await _run(_query)
        return [
            {
                "bookmarkId": i.get("bookmarkId"),
                "before": list(i.get("before", [])),
                "after": list(i.get("after", [])),
                "added": list(i.get("added", [])),
                "removed": list(i.get("removed", [])),
                "actor": i.get("actor"),
                "ts": i.get("ts"),
            }
            for i in items
        ]

    async def get_recent_bookmarks(self, limit: int = 50) -> list[dict]:
        """Recent bookmarks for the request user, incl. the snapshot fields.

        Queries the userId GSI newest-savedAt-first when configured; falls
        back to a filtered scan. Org/scope visibility is enforced HERE in
        the async context (contextvars don't cross run_in_executor — same
        caveat as _search_links).
        """
        from boto3.dynamodb.conditions import Key

        user_id = self._user_id()
        lim = max(1, min(int(limit), 200))

        def _fetch():
            if _USER_INDEX:
                resp = self._links.query(
                    IndexName=_USER_INDEX,
                    KeyConditionExpression=Key("userId").eq(user_id),
                    ScanIndexForward=False,
                    Limit=lim,
                )
            else:
                resp = self._links.scan(FilterExpression=Attr("userId").eq(user_id))
            return resp.get("Items", [])

        items = await _run(_fetch)
        # Org + scope enforcement runs HERE (async context), mirroring
        # _search_links: contextvars don't propagate into run_in_executor
        # threads, so this post-filter is the actual guard — a tags-scoped
        # token must not see bookmarks outside its scope via this endpoint.
        items = [
            i
            for i in items
            if i.get("url") and self._item_org_visible(i) and self._item_scope_visible(i)
        ]
        items.sort(key=lambda i: str(i.get("savedAt") or ""), reverse=True)
        return [
            {
                "id": i.get("id"),
                "url": i.get("url"),
                "title": i.get("ogTitle") or i.get("title") or i.get("url"),
                "aiTags": list(i.get("aiTags", [])),
                "aiTagsOriginal": list(i["aiTagsOriginal"]) if "aiTagsOriginal" in i else None,
                "tagsReviewedAt": i.get("tagsReviewedAt"),
            }
            for i in items[:lim]
        ]

    async def search_bookmarks(
        self, query: str | None = None, tag: str | None = None, limit: int = 20
    ) -> list[Bookmark]:
        items, _ = await self._search_links(query=query, tag=tag, limit=limit, cursor=None)
        return sorted(
            [_to_bookmark(i) for i in items],
            key=lambda b: str(b.created_at or ""),
            reverse=True,
        )

    async def search_bookmarks_paged(
        self,
        query: str | None = None,
        tag: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[Bookmark], str | None]:
        """Cursor-aware search. Returns ``(items, next_cursor)``.

        The cursor is opaque to the caller — base64(JSON(LastEvaluatedKey)).
        Pass ``next_cursor`` from the previous call to continue. Returns
        ``next_cursor=None`` when the scan is complete.
        """
        items, next_cursor = await self._search_links(
            query=query, tag=tag, limit=limit, cursor=cursor
        )
        bookmarks = sorted(
            [_to_bookmark(i) for i in items],
            key=lambda b: str(b.created_at or ""),
            reverse=True,
        )
        return bookmarks, next_cursor

    async def _search_links(
        self,
        *,
        query: str | None,
        tag: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict], str | None]:
        import base64
        import json

        def _scan():
            kwargs: dict[str, Any] = {"Limit": max(1, min(int(limit), 100))}
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
            if cursor:
                # Invalid cursor → treat as fresh scan rather than 500ing the request.
                with contextlib.suppress(Exception):
                    kwargs["ExclusiveStartKey"] = json.loads(
                        base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
                    )
            resp = self._links.scan(**kwargs)
            return resp.get("Items", []), resp.get("LastEvaluatedKey")

        raw_items, last_key = await _run(_scan)
        # Scope + org enforcement runs HERE (async context) rather than in the
        # executor-thread FilterExpression: contextvars (current_user_id /
        # current_scope) don't propagate into run_in_executor threads, so the
        # in-thread _base_link_filter conditions are an optimization only — this
        # post-filter is the actual guard, mirroring _item_org_visible.
        visible = [
            i
            for i in raw_items
            if i.get("url")
            and not i.get("rateLimitKey")
            and self._item_org_visible(i)
            and self._item_scope_visible(i)
        ]
        next_cursor: str | None = None
        if last_key:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(last_key, default=str).encode("utf-8")
            ).decode("ascii")
        return visible, next_cursor

    async def query_raw_by_type(
        self, bookmark_type: str, *, user_id: str, limit: int | None = None
    ) -> list[dict]:
        """Return raw DDB items for one ``userId`` + ``bookmarkType``.

        Used by the semantic index builder (server startup / refresh), NOT by
        request-scoped tool reads — it deliberately returns the owner's full
        corpus of a type so the ANN index can be built once; per-request scope
        filtering happens at query time against the results.

        Index preference: the ``_TYPE_INDEX`` (userId, bookmarkType) GSI if
        configured; else the ``_USER_INDEX`` (userId) GSI queried by userId with
        a server-side bookmarkType filter; else a full-table filtered scan.
        """
        from boto3.dynamodb.conditions import Key

        def _query() -> list[dict]:
            items: list[dict] = []
            start_key: dict[str, Any] | None = None
            while True:
                kwargs: dict[str, Any] = {}
                if _TYPE_INDEX:
                    kwargs["IndexName"] = _TYPE_INDEX
                    kwargs["KeyConditionExpression"] = Key("userId").eq(user_id) & Key(
                        "bookmarkType"
                    ).eq(bookmark_type)
                    use_query = True
                elif _USER_INDEX:
                    kwargs["IndexName"] = _USER_INDEX
                    kwargs["KeyConditionExpression"] = Key("userId").eq(user_id)
                    kwargs["FilterExpression"] = (
                        Attr("bookmarkType").eq(bookmark_type) & Attr("url").exists()
                    )
                    use_query = True
                else:
                    kwargs["FilterExpression"] = (
                        Attr("userId").eq(user_id)
                        & Attr("bookmarkType").eq(bookmark_type)
                        & Attr("url").exists()
                    )
                    use_query = False
                if start_key is not None:
                    kwargs["ExclusiveStartKey"] = start_key
                fn = self._links.query if use_query else self._links.scan
                resp = fn(**kwargs)
                items.extend(resp.get("Items", []))
                start_key = resp.get("LastEvaluatedKey")
                if start_key is None or (limit and len(items) >= limit):
                    break
            return items[:limit] if limit else items

        return await _run(_query)

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
        return [
            _to_bookmark(i)
            for i in items
            if self._item_org_visible(i) and self._item_scope_visible(i)
        ]

    async def get_full_export(self) -> dict:
        bookmarks = await self.get_all_bookmarks()
        tags = await self.get_all_tags()
        stats = await self.get_stats()
        return {
            "version": "1.0",
            "exported_at": datetime.now(UTC).isoformat(),
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
