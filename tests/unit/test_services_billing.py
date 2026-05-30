"""BillingService — Stripe webhook event processing branches.

Mocks ``verify_stripe_signature`` and ``upsert_from_stripe_event`` so the
test only exercises the dispatch logic in :mod:`mcp_bookmarks.services.billing`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_bookmarks.services import billing

# ── Helpers ────────────────────────────────────────────────────────


def _patch_signature(monkeypatch: pytest.MonkeyPatch, *, ok: bool) -> None:
    monkeypatch.setattr(billing, "verify_stripe_signature", lambda *a, **kw: ok)


def _patch_upsert(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def _fake_upsert(cid, status, plan_sku, cpe, obj):
        calls.append((cid, status, plan_sku, cpe, obj))

    monkeypatch.setattr(billing, "upsert_from_stripe_event", _fake_upsert)
    return calls


# ── Tests ──────────────────────────────────────────────────────────


async def test_invalid_signature_returns_error(monkeypatch: pytest.MonkeyPatch):
    _patch_signature(monkeypatch, ok=False)
    outcome, err = await billing.process_signed_payload(
        body=b"{}", signature="bad", secret="whsec_x"
    )
    assert outcome is None
    assert err == "invalid_signature"


async def test_invalid_json_returns_error(monkeypatch: pytest.MonkeyPatch):
    _patch_signature(monkeypatch, ok=True)
    outcome, err = await billing.process_signed_payload(
        body=b"not-json", signature="t=1,v1=x", secret="whsec_x"
    )
    assert outcome is None
    assert err == "invalid_json"


async def test_missing_data_object_returns_ignored(monkeypatch: pytest.MonkeyPatch):
    _patch_signature(monkeypatch, ok=True)
    body = json.dumps({"type": "customer.subscription.created", "data": {"object": None}}).encode()
    outcome, err = await billing.process_signed_payload(
        body=body, signature="sig", secret="whsec_x"
    )
    assert err is None
    assert outcome is not None
    assert outcome.processed is False
    assert outcome.ignored_reason == "no_data_object"
    assert outcome.event_type == "customer.subscription.created"


async def test_unhandled_event_type_returns_ignored(monkeypatch: pytest.MonkeyPatch):
    _patch_signature(monkeypatch, ok=True)
    body = json.dumps({"type": "invoice.paid", "data": {"object": {"id": "in_1"}}}).encode()
    outcome, err = await billing.process_signed_payload(
        body=body, signature="sig", secret="whsec_x"
    )
    assert err is None
    assert outcome is not None
    assert outcome.processed is False
    assert outcome.ignored_reason == "unhandled_type"
    assert outcome.event_type == "invoice.paid"


async def test_handled_event_dispatches_and_processes(monkeypatch: pytest.MonkeyPatch):
    _patch_signature(monkeypatch, ok=True)
    calls = _patch_upsert(monkeypatch)

    # Pin extract_subscription_fields so we know what upsert gets called with.
    monkeypatch.setattr(
        billing,
        "extract_subscription_fields",
        lambda obj: ("cus_123", "active", "pro", 1234567890),
    )

    body = json.dumps(
        {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_1", "customer": "cus_123"}},
        }
    ).encode()
    outcome, err = await billing.process_signed_payload(
        body=body, signature="sig", secret="whsec_x"
    )
    assert err is None
    assert outcome is not None
    assert outcome.processed is True
    assert outcome.event_type == "customer.subscription.updated"
    assert outcome.ignored_reason is None
    assert len(calls) == 1
    assert calls[0][0] == "cus_123"
    assert calls[0][1] == "active"
    assert calls[0][2] == "pro"


async def test_handled_event_without_customer_id_skips_upsert(monkeypatch: pytest.MonkeyPatch):
    """When extract returns no customer id, we still mark processed but don't upsert."""
    _patch_signature(monkeypatch, ok=True)
    calls = _patch_upsert(monkeypatch)
    monkeypatch.setattr(
        billing, "extract_subscription_fields", lambda obj: (None, None, None, None)
    )

    body = json.dumps(
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_1"}},
        }
    ).encode()
    outcome, err = await billing.process_signed_payload(
        body=body, signature="sig", secret="whsec_x"
    )
    assert err is None
    assert outcome is not None and outcome.processed is True
    assert calls == []  # upsert skipped because cid was falsy
