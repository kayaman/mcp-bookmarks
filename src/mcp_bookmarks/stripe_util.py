"""Stripe webhook signature verification (no stripe SDK required)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


def _signing_key_bytes(secret: str) -> bytes:
    s = secret.strip()
    if s.startswith("whsec_"):
        return base64.b64decode(s[6:])
    return s.encode("utf-8")


def verify_stripe_signature(payload: bytes, sig_header: str | None, secret: str, *, tolerance_sec: int = 300) -> bool:
    """Verify ``Stripe-Signature`` header (``t=...,v1=...``)."""
    if not secret or not sig_header:
        return False
    parts: dict[str, list[str]] = {}
    for chunk in sig_header.split(","):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        parts.setdefault(k.strip(), []).append(v.strip())
    ts_list = parts.get("t")
    v1_list = parts.get("v1")
    if not ts_list or not v1_list:
        return False
    try:
        ts = int(ts_list[0])
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > tolerance_sec:
        return False
    signed_payload = f"{ts_list[0]}.{payload.decode('utf-8')}"
    key = _signing_key_bytes(secret)
    expected = hmac.new(key, signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in v1_list)
