"""Normalize user-supplied host into the Starlette ``/api`` mount prefix."""

from __future__ import annotations


def rest_api_prefix(base: str) -> str:
    """Return base URL ending with ``/api`` (e.g. ``http://127.0.0.1:8000`` → ``.../api``)."""
    b = base.rstrip("/")
    if b.endswith("/api"):
        return b
    return f"{b}/api"
