"""Standardized REST error envelope + request validation (WDN-396 / OSS-6).

Pure unit tests; no HTTP server, no I/O.
"""

from __future__ import annotations

import json

import pytest

from mcp_bookmarks.api_envelope import (
    ApiError,
    ApiErrorEnvelope,
    ErrorCode,
    error_response,
)
from mcp_bookmarks.api_requests import (
    BookmarkSummaryRequest,
    BookmarkTagsRequest,
    CreateTagRequest,
    EnsembleRequest,
    SaveBookmarkRequest,
    parse_request_body,
)


# ── Envelope shape ─────────────────────────────────────────────────


def test_envelope_minimal_serialization():
    env = ApiErrorEnvelope(
        error=ApiError(code=ErrorCode.NOT_FOUND, message="missing")
    )
    payload = env.model_dump(exclude_none=True)
    assert payload == {"error": {"code": "not_found", "message": "missing"}}


def test_envelope_with_details():
    env = ApiErrorEnvelope(
        error=ApiError(
            code=ErrorCode.RATE_LIMITED,
            message="over quota",
            details={"used": 100, "limit": 50},
        )
    )
    payload = env.model_dump(exclude_none=True)
    assert payload["error"]["details"] == {"used": 100, "limit": 50}


def test_error_codes_are_stable_strings():
    """If you rename one of these strings you have broken the public contract."""
    assert ErrorCode.UNAUTHORIZED.value == "unauthorized"
    assert ErrorCode.FORBIDDEN.value == "forbidden"
    assert ErrorCode.NOT_FOUND.value == "not_found"
    assert ErrorCode.INVALID_REQUEST.value == "invalid_request"
    assert ErrorCode.INVALID_JSON.value == "invalid_json"
    assert ErrorCode.VALIDATION_ERROR.value == "validation_error"
    assert ErrorCode.CONFLICT.value == "conflict"
    assert ErrorCode.RATE_LIMITED.value == "rate_limited"
    assert ErrorCode.INVALID_SIGNATURE.value == "invalid_signature"
    assert ErrorCode.INTERNAL_ERROR.value == "internal_error"
    assert ErrorCode.SERVICE_UNAVAILABLE.value == "service_unavailable"


def test_error_response_builds_json_response():
    resp = error_response(
        ErrorCode.NOT_FOUND,
        "Bookmark 99 not found",
        status=404,
    )
    assert resp.status_code == 404
    body = json.loads(resp.body)
    assert body == {"error": {"code": "not_found", "message": "Bookmark 99 not found"}}


def test_error_response_accepts_string_code():
    resp = error_response("conflict", "duplicate", status=409)
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["error"]["code"] == "conflict"


def test_error_response_with_details():
    resp = error_response(
        ErrorCode.VALIDATION_ERROR,
        "schema",
        status=422,
        details={"fields": [{"loc": "url", "type": "missing", "message": "required"}]},
    )
    body = json.loads(resp.body)
    assert body["error"]["details"]["fields"][0]["loc"] == "url"


# ── Request models ─────────────────────────────────────────────────


def test_save_bookmark_request_requires_url():
    with pytest.raises(Exception):
        SaveBookmarkRequest(title="x")


def test_save_bookmark_request_accepts_url_only():
    body = SaveBookmarkRequest(url="https://example.com")
    assert str(body.url) == "https://example.com"
    assert body.title is None


def test_create_tag_request_rejects_empty_slug():
    with pytest.raises(Exception):
        CreateTagRequest(slug="", name="X")


def test_bookmark_tags_request_rejects_empty_list():
    with pytest.raises(Exception):
        BookmarkTagsRequest(tag_slugs=[])


def test_bookmark_summary_request_rejects_empty_summary():
    with pytest.raises(Exception):
        BookmarkSummaryRequest(summary="")


def test_ensemble_request_minimum():
    body = EnsembleRequest(task="ping")
    assert body.task == "ping"
    assert body.models is None
    assert body.judge_model is None


# ── parse_request_body helper ──────────────────────────────────────


class _FakeRequest:
    """Minimal Request-shaped fake that just exposes .body()."""

    def __init__(self, raw: bytes):
        self._raw = raw

    async def body(self) -> bytes:
        return self._raw


@pytest.mark.asyncio
async def test_parse_request_body_happy_path():
    req = _FakeRequest(b'{"url": "https://example.com"}')
    parsed, err = await parse_request_body(req, SaveBookmarkRequest)
    assert err is None
    assert parsed is not None
    assert str(parsed.url) == "https://example.com"


@pytest.mark.asyncio
async def test_parse_request_body_empty_body_returns_invalid_json():
    req = _FakeRequest(b"")
    parsed, err = await parse_request_body(req, SaveBookmarkRequest)
    assert parsed is None
    assert err is not None
    body = json.loads(err.body)
    assert body["error"]["code"] == "invalid_json"


@pytest.mark.asyncio
async def test_parse_request_body_bad_json_returns_invalid_json():
    req = _FakeRequest(b"{not json")
    parsed, err = await parse_request_body(req, SaveBookmarkRequest)
    assert parsed is None
    assert err is not None
    body = json.loads(err.body)
    assert body["error"]["code"] == "invalid_json"


@pytest.mark.asyncio
async def test_parse_request_body_missing_field_returns_validation_error():
    req = _FakeRequest(b'{"title": "no url"}')
    parsed, err = await parse_request_body(req, SaveBookmarkRequest)
    assert parsed is None
    assert err is not None
    body = json.loads(err.body)
    assert body["error"]["code"] == "validation_error"
    fields = body["error"]["details"]["fields"]
    assert any(f["loc"] == "url" for f in fields)


@pytest.mark.asyncio
async def test_parse_request_body_validation_error_surfaces_field_paths():
    req = _FakeRequest(b'{"slug": "", "name": "X"}')
    parsed, err = await parse_request_body(req, CreateTagRequest)
    assert parsed is None
    fields = json.loads(err.body)["error"]["details"]["fields"]
    assert any("slug" in f["loc"] for f in fields)
