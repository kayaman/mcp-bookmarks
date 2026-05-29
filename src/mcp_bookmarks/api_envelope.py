"""Standardized REST error envelope (WDN-396 / OSS-6).

Every ``/api/*`` error response wraps its payload in::

    {"error": {"code": "<machine-readable>", "message": "<human-readable>", "details": {...}}}

The ``code`` field is stable and intended for client branching. The
``message`` is for human display. ``details`` is optional structured data
(e.g. validation field paths) that may be omitted.

Use :func:`error_response` to build a ``JSONResponse``; use the
:data:`ErrorCode` enum members so the code strings stay aligned across
handlers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from starlette.responses import JSONResponse


class ErrorCode(StrEnum):
    """Canonical machine-readable error codes used in ``/api/*`` responses.

    Add a new code here when an endpoint needs to distinguish a new failure
    mode. Never change an existing string (the code is part of the public
    REST contract).
    """

    UNAUTHORIZED = "unauthorized"            # 401: missing / bad credentials
    FORBIDDEN = "forbidden"                  # 403: authenticated but not allowed
    NOT_FOUND = "not_found"                  # 404: resource missing
    INVALID_REQUEST = "invalid_request"      # 400: shape OK but content invalid
    INVALID_JSON = "invalid_json"            # 400: body could not be parsed as JSON
    VALIDATION_ERROR = "validation_error"    # 422: Pydantic / schema rejected the body
    CONFLICT = "conflict"                    # 409: idempotency / duplicate-key style
    RATE_LIMITED = "rate_limited"            # 429: monthly quota exceeded
    INVALID_SIGNATURE = "invalid_signature"  # 400 for /webhooks/stripe
    INTERNAL_ERROR = "internal_error"        # 500: unexpected failure
    SERVICE_UNAVAILABLE = "service_unavailable"  # 503: dependency unwired / down


class ApiError(BaseModel):
    """The inner ``error`` object inside an :class:`ApiErrorEnvelope`."""

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context (validation field paths, retry hints, ...)",
    )


class ApiErrorEnvelope(BaseModel):
    """The full ``{"error": ApiError}`` wire shape."""

    error: ApiError


def error_response(
    code: ErrorCode | str,
    message: str,
    status: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a standardized error :class:`JSONResponse`.

    ``code`` accepts the :class:`ErrorCode` enum (preferred) or a string for
    edge cases (e.g. legacy webhook codes). The output always omits ``details``
    when ``None`` so clients see a minimal body in the common case.
    """
    envelope = ApiErrorEnvelope(
        error=ApiError(
            code=ErrorCode(code) if not isinstance(code, ErrorCode) else code,
            message=message,
            details=details,
        )
    )
    return JSONResponse(
        envelope.model_dump(exclude_none=True),
        status_code=status,
    )


__all__ = [
    "ApiError",
    "ApiErrorEnvelope",
    "ErrorCode",
    "error_response",
]
