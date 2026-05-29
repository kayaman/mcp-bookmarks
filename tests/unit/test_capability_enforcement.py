"""Capability enforcement (WDN-394 / OSS-4).

Pure unit tests; no HTTP server, no I/O. Mocks a fake backend to exercise
the ``require_capability`` guard + ``UnsupportedCapability`` envelope shape
+ ``backend_capabilities_payload`` builder.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_bookmarks.backend import (
    DYNAMODB_CAPABILITIES,
    SQLITE_CAPABILITIES,
    BackendCapabilities,
    UnsupportedCapability,
    backend_capabilities_payload,
    require_capability,
)


class _FakeBackend:
    """Minimal backend stand-in: just a ``capabilities`` attribute."""

    def __init__(self, capabilities: BackendCapabilities, *, classname: str = "_FakeBackend"):
        self.capabilities = capabilities
        # Rename the class on the instance so _backend_name picks it up.
        self.__class__ = type(classname, (_FakeBackend,), {})


# ── require_capability ─────────────────────────────────────────────


def test_require_capability_passes_when_flag_true():
    backend = _FakeBackend(SQLITE_CAPABILITIES)
    require_capability(backend, "semantic_search")  # does not raise


def test_require_capability_raises_when_flag_false():
    backend = _FakeBackend(DYNAMODB_CAPABILITIES)
    with pytest.raises(UnsupportedCapability) as excinfo:
        require_capability(backend, "semantic_search")
    assert excinfo.value.capability == "semantic_search"


def test_require_capability_carries_method_in_exception():
    backend = _FakeBackend(DYNAMODB_CAPABILITIES)
    with pytest.raises(UnsupportedCapability) as excinfo:
        require_capability(backend, "semantic_search", method="index_bookmark_embedding")
    assert excinfo.value.method == "index_bookmark_embedding"
    assert "index_bookmark_embedding" in str(excinfo.value)


def test_require_capability_raises_when_backend_missing_capabilities_attr():
    backend = object()  # no ``capabilities`` at all
    with pytest.raises(UnsupportedCapability):
        require_capability(backend, "semantic_search")  # type: ignore[arg-type]


# ── UnsupportedCapability.to_envelope ──────────────────────────────


def test_envelope_shape_minimal():
    exc = UnsupportedCapability(capability="semantic_search", backend="dynamodb")
    envelope = exc.to_envelope()
    assert envelope == {
        "code": "unsupported",
        "message": "Backend 'dynamodb' does not support 'semantic_search'",
        "details": {
            "backend": "dynamodb",
            "capability": "semantic_search",
        },
    }


def test_envelope_includes_method_when_provided():
    exc = UnsupportedCapability(
        capability="semantic_search",
        backend="dynamodb",
        method="semantic_search_bookmarks",
    )
    envelope = exc.to_envelope()
    assert envelope["details"]["method"] == "semantic_search_bookmarks"


# ── backend_capabilities_payload ───────────────────────────────────


def test_capabilities_payload_for_sqlite_database():
    """Real Database instance (no DB connection needed for the attr lookup)."""
    from pathlib import Path

    from mcp_bookmarks.db import Database

    db = Database(Path("/tmp/never-connected.db"))
    payload = backend_capabilities_payload(db)
    assert payload["backend"] == "sqlite"
    assert payload["capabilities"]["semantic_search"] is True
    assert payload["capabilities"]["paged_search"] is False
    assert payload["capabilities"]["integer_bookmark_ids"] is True
    assert payload["capabilities"]["usage_metering"] is True
    assert payload["capabilities"]["subscription_storage"] is True


def test_capabilities_payload_for_dynamodb_database():
    """Real DynamoDBDatabase instance (boto3 mocked at __init__)."""
    with patch("mcp_bookmarks.dynamodb._dynamo") as fake_dynamo:
        fake_dynamo.return_value.Table.return_value = MagicMock()
        from mcp_bookmarks.dynamodb import DynamoDBDatabase

        ddb = DynamoDBDatabase()
    payload = backend_capabilities_payload(ddb)
    assert payload["backend"] == "dynamodb"
    assert payload["capabilities"]["semantic_search"] is False
    assert payload["capabilities"]["paged_search"] is True
    assert payload["capabilities"]["integer_bookmark_ids"] is False
    assert payload["capabilities"]["usage_metering"] is False
    assert payload["capabilities"]["subscription_storage"] is False


def test_capabilities_payload_keys_match_dataclass_fields():
    """If you add a new flag to BackendCapabilities, the payload must surface it.

    This is the safety net against `BackendCapabilities` growing a flag that
    `backend_capabilities_payload` silently drops.
    """
    import dataclasses

    from pathlib import Path

    from mcp_bookmarks.db import Database

    db = Database(Path("/tmp/never-connected.db"))
    payload_keys = set(backend_capabilities_payload(db)["capabilities"].keys())
    expected_keys = {f.name for f in dataclasses.fields(BackendCapabilities)}
    assert payload_keys == expected_keys
