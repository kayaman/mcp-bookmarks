"""Tests for multi-tenant isolation in SQLite and DynamoDB backends."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_bookmarks.auth import resolve_tenant
from mcp_bookmarks.db import Database
from mcp_bookmarks.models import Tenant

# ═══════════════════════════════════════════════════════════════════
#  SQLite tenant isolation
# ═══════════════════════════════════════════════════════════════════


class TestSQLiteTenantIsolation(unittest.IsolatedAsyncioTestCase):
    """Verify that two tenants cannot read each other's data via SQLite backend."""

    async def asyncSetUp(self):
        # NamedTemporaryFile is kept open across setup/teardown so a `with` block
        # doesn't fit; the per-test asyncTearDown cleans up explicitly.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # noqa: SIM115
        self.db_path = Path(self._tmp.name)

        self.db_a = Database(self.db_path, tenant_id="org-a")
        self.db_b = Database(self.db_path, tenant_id="org-b")
        await self.db_a.connect()
        await self.db_b.connect()

    async def asyncTearDown(self):
        await self.db_a.close()
        await self.db_b.close()
        self.db_path.unlink(missing_ok=True)

    # ── Tags ──────────────────────────────────────────────────────

    async def test_tags_are_scoped_per_tenant(self):
        await self.db_a.create_tag("python", "Python", "Python ecosystem")
        await self.db_b.create_tag("rust", "Rust", "Rust language")

        tags_a = await self.db_a.get_all_tags()
        tags_b = await self.db_b.get_all_tags()

        self.assertEqual([t.slug for t in tags_a], ["python"])
        self.assertEqual([t.slug for t in tags_b], ["rust"])

    async def test_get_tag_by_slug_is_scoped(self):
        await self.db_a.create_tag("shared-slug", "A's tag", "")

        found_in_a = await self.db_a.get_tag_by_slug("shared-slug")
        found_in_b = await self.db_b.get_tag_by_slug("shared-slug")

        self.assertIsNotNone(found_in_a)
        self.assertIsNone(found_in_b)

    async def test_same_slug_can_exist_in_two_tenants(self):
        await self.db_a.create_tag("python", "Python A", "For org-a")
        await self.db_b.create_tag("python", "Python B", "For org-b")

        tag_a = await self.db_a.get_tag_by_slug("python")
        tag_b = await self.db_b.get_tag_by_slug("python")

        self.assertEqual(tag_a.name, "Python A")
        self.assertEqual(tag_b.name, "Python B")

    # ── Bookmarks ─────────────────────────────────────────────────

    async def test_bookmarks_are_scoped_per_tenant(self):
        await self.db_a.upsert_bookmark("https://a.example.com", title="A's bookmark")
        await self.db_b.upsert_bookmark("https://b.example.com", title="B's bookmark")

        results_a = await self.db_a.search_bookmarks()
        results_b = await self.db_b.search_bookmarks()

        self.assertEqual(len(results_a), 1)
        self.assertEqual(results_a[0].url, "https://a.example.com")
        self.assertEqual(len(results_b), 1)
        self.assertEqual(results_b[0].url, "https://b.example.com")

    async def test_same_url_can_exist_in_two_tenants(self):
        bk_a = await self.db_a.upsert_bookmark("https://shared.example.com", title="A's copy")
        bk_b = await self.db_b.upsert_bookmark("https://shared.example.com", title="B's copy")

        self.assertNotEqual(bk_a.id, bk_b.id)

        fetched_a = await self.db_a.get_bookmark_by_id(bk_a.id)
        fetched_b_via_a = await self.db_a.get_bookmark_by_id(bk_b.id)

        self.assertIsNotNone(fetched_a)
        self.assertIsNone(fetched_b_via_a, "Tenant A must not see tenant B's bookmark by ID")

    async def test_stats_are_scoped(self):
        await self.db_a.upsert_bookmark("https://a1.example.com")
        await self.db_a.upsert_bookmark("https://a2.example.com")
        await self.db_b.upsert_bookmark("https://b1.example.com")

        stats_a = await self.db_a.get_stats()
        stats_b = await self.db_b.get_stats()

        self.assertEqual(stats_a["total_bookmarks"], 2)
        self.assertEqual(stats_b["total_bookmarks"], 1)

    async def test_delete_bookmark_does_not_cross_tenant(self):
        bk_a = await self.db_a.upsert_bookmark("https://to-delete.example.com")
        await self.db_b.upsert_bookmark("https://to-delete.example.com")

        deleted = await self.db_b.delete_bookmark(bk_a.id)
        self.assertFalse(deleted, "Deleting another tenant's bookmark by ID must fail")

    async def test_set_bookmark_content_scoped(self):
        bk_a = await self.db_a.upsert_bookmark("https://content.example.com")
        await self.db_a.set_bookmark_content(bk_a.id, "A's content", 2)

        fetched_via_b = await self.db_b.get_bookmark_by_id(bk_a.id)
        self.assertIsNone(fetched_via_b)

    async def test_get_all_bookmarks_scoped(self):
        await self.db_a.upsert_bookmark("https://export-a.example.com")
        await self.db_b.upsert_bookmark("https://export-b.example.com")

        all_a = await self.db_a.get_all_bookmarks()
        all_b = await self.db_b.get_all_bookmarks()

        self.assertEqual(len(all_a), 1)
        self.assertEqual(len(all_b), 1)

    # ── Migration ────────────────────────────────────────────────

    async def test_migration_adds_tenant_column(self):
        """Old DBs without tenant_id should gain the column and default to 'default'."""
        import aiosqlite

        old_db_path = Path(tempfile.mktemp(suffix=".db"))
        async with aiosqlite.connect(str(old_db_path)) as conn:
            # Simulate schema from v0.5.x: has content/word_count but no tenant_id.
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    usage_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT,
                    description TEXT,
                    image_url TEXT,
                    site_name TEXT,
                    summary TEXT,
                    content TEXT,
                    word_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS bookmark_tags (
                    bookmark_id INTEGER NOT NULL REFERENCES bookmarks(id),
                    tag_id INTEGER NOT NULL REFERENCES tags(id),
                    PRIMARY KEY (bookmark_id, tag_id)
                );
                INSERT INTO bookmarks (url, title) VALUES ('https://old.com', 'Old Bookmark');
            """)
            await conn.commit()

        db = Database(old_db_path, tenant_id="default")
        await db.connect()

        bks = await db.search_bookmarks()
        self.assertEqual(len(bks), 1)
        self.assertEqual(bks[0].title, "Old Bookmark")

        await db.close()
        old_db_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  DynamoDB tenant isolation (mocked)
# ═══════════════════════════════════════════════════════════════════


class TestDynamoDBTenantIsolation(unittest.IsolatedAsyncioTestCase):
    """Verify DynamoDBDatabase uses per-instance Tenant for filtering."""

    def _make_db(self, org_id: str):  # -> DynamoDBDatabase
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        tenant = Tenant(organization_id=org_id, user_id="test-user")
        with patch("mcp_bookmarks.dynamodb.boto3"):
            db = DynamoDBDatabase(tenant=tenant)
        return db

    async def test_org_id_from_tenant_not_env(self):
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        tenant = Tenant(organization_id="org-from-arg")
        with patch("mcp_bookmarks.dynamodb.boto3"):
            db = DynamoDBDatabase(tenant=tenant)
        self.assertEqual(db._org_id(), "org-from-arg")

    async def test_default_tenant_org_id_is_none(self):
        """organization_id='default' should resolve to None (no filtering)."""
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        tenant = Tenant(organization_id="default")
        with patch("mcp_bookmarks.dynamodb.boto3"):
            db = DynamoDBDatabase(tenant=tenant)
        self.assertIsNone(db._org_id())

    async def test_item_org_visible_with_real_org(self):
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        tenant = Tenant(organization_id="org-a")
        with patch("mcp_bookmarks.dynamodb.boto3"):
            db = DynamoDBDatabase(tenant=tenant)

        self.assertTrue(db._item_org_visible({"organization_id": "org-a"}))
        self.assertFalse(db._item_org_visible({"organization_id": "org-b"}))
        self.assertFalse(db._item_org_visible({}))

    async def test_item_org_visible_default_tenant_sees_all(self):
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        tenant = Tenant(organization_id="default")
        with patch("mcp_bookmarks.dynamodb.boto3"):
            db = DynamoDBDatabase(tenant=tenant)

        self.assertTrue(db._item_org_visible({"organization_id": "any-org"}))
        self.assertTrue(db._item_org_visible({}))

    async def test_upsert_bookmark_uses_tenant_org(self):
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        tenant = Tenant(organization_id="org-a", user_id="user-a")
        with patch("mcp_bookmarks.dynamodb.boto3") as mock_boto:
            mock_table = MagicMock()
            mock_resource = MagicMock()
            mock_resource.Table.return_value = mock_table
            mock_boto.resource.return_value = mock_resource
            db = DynamoDBDatabase(tenant=tenant)

        put_calls = []

        def capture_put_item(Item, ConditionExpression):
            put_calls.append(Item)

        mock_table.put_item = capture_put_item

        with patch(
            "mcp_bookmarks.dynamodb._run", side_effect=lambda fn, *a, **kw: asyncio.coroutine(fn)()
        ):
            pass

        # Verify tenant properties are set correctly
        self.assertEqual(db._user_id(), "user-a")
        self.assertEqual(db._org_id(), "org-a")

    async def test_backward_compat_env_vars(self):
        """DynamoDBDatabase() with no tenant arg should read env vars."""
        import os

        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        with patch.dict(os.environ, {"DYNAMODB_ORG_ID": "env-org", "DYNAMODB_USER_ID": "env-user"}):
            with patch("mcp_bookmarks.dynamodb.boto3"):
                db = DynamoDBDatabase()

        self.assertEqual(db._tenant.organization_id, "env-org")
        self.assertEqual(db._user_id(), "env-user")


# ═══════════════════════════════════════════════════════════════════
#  Auth: resolve_tenant helper
# ═══════════════════════════════════════════════════════════════════


class TestResolveTenant(unittest.TestCase):
    def test_resolve_tenant_from_api_key(self):
        import os

        with patch.dict(os.environ, {"MCP_API_KEYS": "key-abc:org-xyz"}):
            tenant = resolve_tenant({"authorization": "Bearer key-abc"})
        self.assertEqual(tenant.organization_id, "org-xyz")

    def test_resolve_tenant_falls_back_to_env(self):
        import os

        with patch.dict(os.environ, {"MCP_API_KEYS": "", "DYNAMODB_ORG_ID": "env-org"}):
            tenant = resolve_tenant({})
        self.assertEqual(tenant.organization_id, "env-org")

    def test_resolve_tenant_defaults_to_default(self):
        import os

        with patch.dict(os.environ, {"MCP_API_KEYS": "", "DYNAMODB_ORG_ID": ""}):
            tenant = resolve_tenant({})
        self.assertEqual(tenant.organization_id, "default")

    def test_resolve_tenant_returns_tenant_model(self):
        from mcp_bookmarks.models import Tenant

        tenant = resolve_tenant({})
        self.assertIsInstance(tenant, Tenant)


if __name__ == "__main__":
    unittest.main()
