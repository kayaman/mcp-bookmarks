"""Structured logging + per-request correlation IDs (WDN-397 / OSS-7).

Single entry point: :func:`configure_logging` — call once at app startup.

Format selection:
  - ``LOG_FORMAT=json``: production-grade JSON, one record per line, suitable
    for CloudWatch Logs / Datadog / Loki ingest.
  - ``LOG_FORMAT=pretty`` (default for local dev): human-readable text.
  - Unset: auto — JSON in production (when ``ENV != "dev"``), pretty otherwise.

Every record gets a ``correlation_id`` field. The value is read from the
:data:`correlation_id_var` contextvar (set by
:class:`mcp_bookmarks.correlation.CorrelationMiddleware`) and falls back to
``"-"`` when there's no in-flight request.

Use the standard ``logging`` API to emit::

    log.info("rest_bookmark_saved", extra={"bookmark_id": bid, "tenant_id": tid})

The ``extra`` dict's keys land at the top level of the JSON record.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from contextvars import ContextVar

# ── Correlation ID contextvar ──────────────────────────────────────


correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


# ── Record fields we keep out of the structured payload ────────────


_RESERVED_RECORD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


def _record_extras(record: logging.LogRecord) -> dict:
    """Return only the keys that the caller passed via ``extra=``."""
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED_RECORD_KEYS}


# ── Formatters ─────────────────────────────────────────────────────


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record. CloudWatch / Datadog friendly."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get() or "-",
        }
        # Caller-supplied extras override defaults (e.g. a custom correlation_id).
        payload.update(_record_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class PrettyFormatter(logging.Formatter):
    """Human-readable text + a compact key=value tail for structured extras."""

    _BASE_FMT = "%(asctime)s %(levelname)-7s %(name)s [cid=%(_cid)s] %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        # _cid is a dynamic attribute consumed by the format string above.
        record._cid = correlation_id_var.get() or "-"
        base = logging.Formatter(self._BASE_FMT, datefmt="%H:%M:%S").format(record)
        extras = _record_extras(record)
        # Strip the internal _cid we just added.
        extras.pop("_cid", None)
        if not extras:
            return base
        tail = " ".join(f"{k}={v!r}" for k, v in extras.items())
        return f"{base}  ({tail})"


# ── Public entry point ─────────────────────────────────────────────


def _resolve_format() -> str:
    explicit = os.environ.get("LOG_FORMAT", "").strip().lower()
    if explicit in ("json", "pretty"):
        return explicit
    env = os.environ.get("ENV", "").strip().lower()
    return "pretty" if env in ("", "dev", "development", "local") else "json"


def configure_logging(level: int | str | None = None) -> None:
    """Install the structured handler on the root logger.

    Idempotent — calling twice replaces the handler without duplicating.
    Honors ``LOG_LEVEL`` env var when ``level`` is None (default ``INFO``).
    Honors ``LOG_FORMAT`` env var to pick the formatter (auto by default).
    """
    if level is None:
        level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)

    fmt = _resolve_format()
    formatter: logging.Formatter = JsonFormatter() if fmt == "json" else PrettyFormatter()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace any existing handlers we installed on a prior call so a hot
    # reload (e.g. uvicorn --reload) doesn't double-emit every record.
    for existing in list(root.handlers):
        if getattr(existing, "_mcp_bookmarks_managed", False):
            root.removeHandler(existing)
    # Tag the handler so repeated calls find it via getattr above.
    handler._mcp_bookmarks_managed = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


__all__ = [
    "JsonFormatter",
    "PrettyFormatter",
    "configure_logging",
    "correlation_id_var",
]
