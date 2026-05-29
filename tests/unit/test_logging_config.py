"""Structured logging + correlation contextvar (WDN-397 / OSS-7).

Pure unit tests; no HTTP server, no I/O. Exercises the formatters directly
plus the contextvar lifecycle.
"""

from __future__ import annotations

import io
import json
import logging

from mcp_bookmarks.logging_config import (
    JsonFormatter,
    PrettyFormatter,
    configure_logging,
    correlation_id_var,
)

# ── JsonFormatter ──────────────────────────────────────────────────


def _make_record(
    *, name: str = "test", level: int = logging.INFO, msg: str = "hello", **extra
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_json_formatter_minimal_shape():
    record = _make_record()
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello"
    assert "ts" in payload
    assert payload["correlation_id"] == "-"


def test_json_formatter_surfaces_extras():
    record = _make_record(tenant_id="org-7", bookmark_id=42)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["tenant_id"] == "org-7"
    assert payload["bookmark_id"] == 42


def test_json_formatter_picks_up_correlation_id():
    token = correlation_id_var.set("cid-abc")
    try:
        record = _make_record()
        payload = json.loads(JsonFormatter().format(record))
        assert payload["correlation_id"] == "cid-abc"
    finally:
        correlation_id_var.reset(token)


def test_json_formatter_emits_exception_block():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record()
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


# ── PrettyFormatter ────────────────────────────────────────────────


def test_pretty_formatter_includes_correlation_marker():
    token = correlation_id_var.set("cid-xyz")
    try:
        out = PrettyFormatter().format(_make_record())
        assert "cid=cid-xyz" in out
    finally:
        correlation_id_var.reset(token)


def test_pretty_formatter_appends_extras_when_present():
    out = PrettyFormatter().format(_make_record(tenant_id="org-7"))
    assert "tenant_id='org-7'" in out


def test_pretty_formatter_no_tail_when_no_extras():
    out = PrettyFormatter().format(_make_record(msg="plain"))
    # No trailing "(...)" when there are no extras to surface.
    assert "(" not in out.split("plain", 1)[1]


# ── configure_logging idempotency ──────────────────────────────────


def test_configure_logging_does_not_duplicate_handlers(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging()
    configure_logging()  # second call must not double-up
    managed = [
        h for h in logging.getLogger().handlers if getattr(h, "_mcp_bookmarks_managed", False)
    ]
    assert len(managed) == 1


def test_configure_logging_honors_log_format_env(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging()
    managed = next(
        h for h in logging.getLogger().handlers if getattr(h, "_mcp_bookmarks_managed", False)
    )
    assert isinstance(managed.formatter, JsonFormatter)


def test_configure_logging_auto_picks_pretty_in_dev(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    monkeypatch.setenv("ENV", "dev")
    configure_logging()
    managed = next(
        h for h in logging.getLogger().handlers if getattr(h, "_mcp_bookmarks_managed", False)
    )
    assert isinstance(managed.formatter, PrettyFormatter)


def test_configure_logging_auto_picks_json_in_prod(monkeypatch):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    monkeypatch.setenv("ENV", "prod")
    configure_logging()
    managed = next(
        h for h in logging.getLogger().handlers if getattr(h, "_mcp_bookmarks_managed", False)
    )
    assert isinstance(managed.formatter, JsonFormatter)


# ── End-to-end via stream handler ──────────────────────────────────


def test_logger_emits_through_json_handler_with_correlation_id():
    """A real log call lands a single JSON line containing the contextvar."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("mcp_bookmarks.test_e2e")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    token = correlation_id_var.set("cid-e2e")
    try:
        log.info("rest_bookmark_saved", extra={"bookmark_id": 42, "tenant_id": "org-7"})
    finally:
        correlation_id_var.reset(token)
        log.removeHandler(handler)

    line = buf.getvalue().strip()
    payload = json.loads(line)
    assert payload["message"] == "rest_bookmark_saved"
    assert payload["correlation_id"] == "cid-e2e"
    assert payload["bookmark_id"] == 42
    assert payload["tenant_id"] == "org-7"
