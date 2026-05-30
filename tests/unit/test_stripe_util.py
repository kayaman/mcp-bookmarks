"""Unit tests for Stripe webhook signature helper."""

import base64
import hashlib
import hmac
import time

from mcp_bookmarks.stripe_util import verify_stripe_signature


def test_verify_stripe_signature_accepts_valid_payload():
    secret = "whsec_" + base64.b64encode(b"test_secret_key_bytes_!!").decode("ascii")
    payload = b'{"id":"evt_1","object":"event"}'
    ts = str(int(time.time()))
    signed = f"{ts}.{payload.decode('utf-8')}"
    key = base64.b64decode(secret[6:])
    expected = hmac.new(key, signed.encode("utf-8"), hashlib.sha256).hexdigest()
    sig_header = f"t={ts},v1={expected}"
    assert verify_stripe_signature(payload, sig_header, secret) is True


def test_verify_stripe_signature_rejects_bad_sig():
    secret = "whsec_" + base64.b64encode(b"test_secret_key_bytes_!!").decode("ascii")
    payload = b"{}"
    ts = str(int(time.time()))
    sig_header = f"t={ts},v1=deadbeef"
    assert verify_stripe_signature(payload, sig_header, secret) is False


# ── malformed input ──────────────────────────────────────────────


def test_empty_secret_returns_false():
    assert verify_stripe_signature(b"{}", "t=1,v1=abc", "") is False


def test_none_header_returns_false():
    assert verify_stripe_signature(b"{}", None, "whsec_x") is False


def test_header_missing_t_chunk_returns_false():
    """Header without a `t=` chunk can't be verified."""
    assert verify_stripe_signature(b"{}", "v1=deadbeef", "whsec_x") is False


def test_header_missing_v1_chunk_returns_false():
    """Header without a `v1=` chunk can't be verified."""
    ts = str(int(time.time()))
    assert verify_stripe_signature(b"{}", f"t={ts}", "whsec_x") is False


def test_header_with_non_numeric_timestamp_returns_false():
    """`ts = int(...)` raises ValueError → caught, returns False."""
    assert verify_stripe_signature(b"{}", "t=notanumber,v1=deadbeef", "whsec_x") is False


def test_header_chunks_without_equals_are_skipped():
    """Malformed chunks (no `=`) should be silently ignored, not raise."""
    # No `t=` present after the malformed chunk is dropped → returns False (missing t)
    assert verify_stripe_signature(b"{}", "garbage,v1=abc", "whsec_x") is False


def test_stale_timestamp_outside_tolerance_returns_false():
    """Timestamp older than tolerance (default 5min) is rejected."""
    secret = "whsec_" + base64.b64encode(b"test_secret_key_bytes_!!").decode("ascii")
    stale_ts = str(int(time.time()) - 3600)  # 1 hour ago
    sig_header = f"t={stale_ts},v1=deadbeef"
    assert verify_stripe_signature(b"{}", sig_header, secret) is False


def test_raw_secret_bytes_path():
    """Secret NOT prefixed with `whsec_` is used as raw UTF-8 bytes (not base64 decoded)."""
    raw_secret = "my-shared-secret-bytes"
    payload = b'{"hello":"world"}'
    ts = str(int(time.time()))
    signed = f"{ts}.{payload.decode('utf-8')}"
    expected = hmac.new(
        raw_secret.encode("utf-8"), signed.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    sig_header = f"t={ts},v1={expected}"
    assert verify_stripe_signature(payload, sig_header, raw_secret) is True
