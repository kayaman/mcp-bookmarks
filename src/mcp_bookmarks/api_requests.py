"""Pydantic request models for the REST write endpoints (WDN-396 / OSS-6).

Each write endpoint pairs a model below with the
:func:`parse_request_body` helper. Validation failures surface through the
standard :data:`ErrorCode.VALIDATION_ERROR` envelope with the offending
field paths in ``details["fields"]``.
"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .api_envelope import ErrorCode, error_response

# ── Request models ─────────────────────────────────────────────────


class SaveBookmarkRequest(BaseModel):
    """``POST /api/save`` body."""

    url: str = Field(..., min_length=1, max_length=2048)
    title: str | None = None


class CreateTagRequest(BaseModel):
    """``POST /api/tag`` body."""

    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)


class BookmarkTagsRequest(BaseModel):
    """``POST /api/bookmarks/{id}/tags`` body."""

    tag_slugs: list[str] = Field(..., min_length=1)


class BookmarkSummaryRequest(BaseModel):
    """``POST /api/bookmarks/{id}/summary`` body."""

    summary: str = Field(..., min_length=1)


class EnsembleRequest(BaseModel):
    """``POST /api/ensemble`` body."""

    task: str = Field(..., min_length=1)
    models: list[str] | None = None
    judge_model: str | None = None


# ── Body parsing helper ────────────────────────────────────────────


_T = TypeVar("_T", bound=BaseModel)


async def parse_request_body(
    request: Request,
    model: type[_T],
) -> tuple[_T | None, JSONResponse | None]:
    """Parse + validate a JSON body against ``model``.

    Returns ``(parsed, None)`` on success or ``(None, error_response)``
    on failure. Handlers should pattern-match::

        body, err = await parse_request_body(request, SaveBookmarkRequest)
        if err is not None:
            return err
        # use body.url, body.title, ...
    """
    try:
        raw = await request.body()
        if not raw:
            return None, error_response(
                ErrorCode.INVALID_JSON,
                "Request body is empty",
                status=400,
            )
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, error_response(
            ErrorCode.INVALID_JSON,
            "Request body is not valid JSON",
            status=400,
            details={"reason": str(exc)},
        )

    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        return None, error_response(
            ErrorCode.VALIDATION_ERROR,
            "Request body failed schema validation",
            status=422,
            details={"fields": _flatten_validation_errors(exc)},
        )

    return parsed, None


def _flatten_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Map Pydantic ValidationError to a stable wire shape.

    Each entry: ``{"loc": "field.path", "type": "missing", "message": "..."}``.
    """
    out: list[dict[str, str]] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        out.append(
            {
                "loc": loc,
                "type": str(err.get("type", "")),
                "message": str(err.get("msg", "")),
            }
        )
    return out


__all__ = [
    "BookmarkSummaryRequest",
    "BookmarkTagsRequest",
    "CreateTagRequest",
    "EnsembleRequest",
    "SaveBookmarkRequest",
    "parse_request_body",
]
