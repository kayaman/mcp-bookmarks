"""Conformance: both concrete backends satisfy the BookmarkBackend protocol.

Phase 1 of WDN-393 (OSS-3).

``Protocol`` with ``@runtime_checkable`` only verifies attribute *presence*
at runtime, not signature compatibility. Real signature drift will surface
under mypy / pyright; this test is a guardrail against accidentally removing
a method or capability attribute from one backend.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_bookmarks.backend import (
    DYNAMODB_CAPABILITIES,
    SQLITE_CAPABILITIES,
    BackendCapabilities,
    BookmarkBackend,
)


def test_sqlite_capabilities_shape():
    assert SQLITE_CAPABILITIES.semantic_search is True
    assert SQLITE_CAPABILITIES.integer_bookmark_ids is True
    assert SQLITE_CAPABILITIES.usage_metering is True
    assert SQLITE_CAPABILITIES.subscription_storage is True
    assert SQLITE_CAPABILITIES.paged_search is False


def test_dynamodb_capabilities_shape():
    assert DYNAMODB_CAPABILITIES.semantic_search is False
    assert DYNAMODB_CAPABILITIES.integer_bookmark_ids is False
    assert DYNAMODB_CAPABILITIES.usage_metering is False
    assert DYNAMODB_CAPABILITIES.subscription_storage is False
    assert DYNAMODB_CAPABILITIES.paged_search is True


def test_database_conforms_to_protocol(tmp_path: Path):
    """SQLite Database satisfies the BookmarkBackend protocol (attribute check)."""
    from mcp_bookmarks.db import Database

    db = Database(tmp_path / "bookmarks.db")
    assert isinstance(db, BookmarkBackend)
    assert db.capabilities is SQLITE_CAPABILITIES


def test_dynamodb_conforms_to_protocol():
    """DynamoDBDatabase satisfies the BookmarkBackend protocol.

    Constructor calls boto3.resource at __init__ time, so this test mocks
    the boto3 layer rather than relying on real credentials.
    """
    with patch("mcp_bookmarks.dynamodb._dynamo") as fake_dynamo:
        fake_dynamo.return_value.Table.return_value = MagicMock()
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        ddb = DynamoDBDatabase()
    assert isinstance(ddb, BookmarkBackend)
    assert ddb.capabilities is DYNAMODB_CAPABILITIES


def test_capabilities_dataclass_is_frozen():
    """BackendCapabilities must be immutable so capability state can't drift at runtime."""
    import dataclasses

    caps = BackendCapabilities()
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        caps.semantic_search = True  # type: ignore[misc]
