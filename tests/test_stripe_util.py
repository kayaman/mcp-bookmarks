"""Unit tests for Stripe webhook signature helper."""

import base64
import hashlib
import hmac
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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
