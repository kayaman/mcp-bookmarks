"""Unit tests for the Bedrock Converse text client (mocked boto3).

Same pattern as tests/unit/test_embeddings_bedrock.py: monkeypatch the
module-level ``_client`` so ``_bedrock()`` never constructs a real boto3
client.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from mcp_bookmarks import bedrock_text as bt


class _FakeConverse:
    """Records converse calls; returns a canned payload or raises."""

    def __init__(self, payload: dict | None = None, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._payload = payload or {"output": {"message": {"content": [{"text": "hello"}]}}}
        self._exc = exc

    def converse(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._payload


@pytest.fixture
def fake_bedrock(monkeypatch: pytest.MonkeyPatch) -> _FakeConverse:
    fake = _FakeConverse()
    monkeypatch.setattr(bt, "_client", fake)  # short-circuit _bedrock()
    return fake


def test_model_default_and_env_override_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch, fake_bedrock: _FakeConverse
) -> None:
    monkeypatch.delenv("RECALIBRATE_MODEL_ID", raising=False)
    assert bt.recalibrate_model_id() == "us.amazon.nova-2-lite-v1:0"
    bt.converse_text("hi")
    assert fake_bedrock.calls[0]["modelId"] == "us.amazon.nova-2-lite-v1:0"
    monkeypatch.setenv("RECALIBRATE_MODEL_ID", "us.amazon.nova-2-pro-v1:0")
    bt.converse_text("hi")  # env re-read on the second call, no re-import
    assert fake_bedrock.calls[1]["modelId"] == "us.amazon.nova-2-pro-v1:0"


def test_prompt_and_max_tokens_plumbed(fake_bedrock: _FakeConverse) -> None:
    out = bt.converse_text("the prompt", max_tokens=123)
    assert out == "hello"
    call = fake_bedrock.calls[0]
    assert call["messages"] == [{"role": "user", "content": [{"text": "the prompt"}]}]
    assert call["inferenceConfig"] == {"maxTokens": 123}


def test_system_block_included_only_when_given(fake_bedrock: _FakeConverse) -> None:
    bt.converse_text("p")
    assert "system" not in fake_bedrock.calls[0]
    bt.converse_text("p", system="you are a curator")
    assert fake_bedrock.calls[1]["system"] == [{"text": "you are a curator"}]


def test_multiple_content_blocks_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeConverse(
        payload={"output": {"message": {"content": [{"text": "a"}, {"text": "b"}]}}}
    )
    monkeypatch.setattr(bt, "_client", fake)
    assert bt.converse_text("p") == "ab"


def test_client_error_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Converse")
    monkeypatch.setattr(bt, "_client", _FakeConverse(exc=err))
    with pytest.raises(bt.BedrockTextError):
        bt.converse_text("p")


def test_empty_output_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeConverse(payload={"output": {"message": {"content": []}}})
    monkeypatch.setattr(bt, "_client", fake)
    with pytest.raises(bt.BedrockTextError):
        bt.converse_text("p")


def test_malformed_response_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bt, "_client", _FakeConverse(payload={"output": {}}))
    with pytest.raises(bt.BedrockTextError):
        bt.converse_text("p")
