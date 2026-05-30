"""BillingService — Stripe webhook event processing.

The `stripe_webhook` REST handler previously inlined the verify ->
parse -> dispatch -> persist flow. Centralizing here gives us:

- one place to update when new event types start mattering;
- a single canonical structured log line per webhook
  (``stripe_webhook_processed`` / ``stripe_webhook_ignored``);
- a return-shape (``WebhookOutcome``) handlers can serialize without
  duplicating dict-construction.

Plan -> quota mapping is **not** in scope; see ADR-0003.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..stripe_util import verify_stripe_signature
from ..subscription_store import extract_subscription_fields, upsert_from_stripe_event

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookOutcome:
    """Result of processing one webhook delivery."""

    received: bool = True
    processed: bool = False
    event_type: str | None = None
    ignored_reason: str | None = None


_HANDLED_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


async def process_signed_payload(
    *, body: bytes, signature: str | None, secret: str
) -> tuple[WebhookOutcome | None, str | None]:
    """Verify, parse, and dispatch one webhook.

    Returns ``(outcome, error)``:
    - ``(None, "invalid_signature")`` if the signature didn't verify.
    - ``(None, "invalid_json")`` if the body wasn't valid JSON.
    - ``(WebhookOutcome, None)`` on a verified event (processed or
      ignored — caller serializes the outcome).
    """
    if not verify_stripe_signature(body, signature, secret):
        return None, "invalid_signature"
    try:
        event = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None, "invalid_json"

    etype = event.get("type")
    obj = event.get("data", {}).get("object")
    if not isinstance(obj, dict):
        log.info("stripe_webhook_ignored", extra={"reason": "no_data_object", "type": etype})
        return WebhookOutcome(ignored_reason="no_data_object", event_type=etype), None

    if etype not in _HANDLED_EVENTS:
        log.info("stripe_webhook_ignored", extra={"reason": "unhandled_type", "type": etype})
        return WebhookOutcome(ignored_reason="unhandled_type", event_type=etype), None

    cid, status, plan_sku, cpe = extract_subscription_fields(obj)
    if cid:
        await upsert_from_stripe_event(cid, status, plan_sku, cpe, obj)
    log.info(
        "stripe_webhook_processed",
        extra={"type": etype, "customer_id": cid, "status": status, "plan": plan_sku},
    )
    return WebhookOutcome(processed=True, event_type=etype), None
