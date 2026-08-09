"""AWS Bedrock Converse text generation for the tag-recalibrate proposer.

The existing Bedrock code (``embeddings_bedrock.py``) is embeddings-only
(``invoke_model`` against Titan). This module is the text-generation
counterpart, built on the Converse API so the model id is swappable
without body-format changes. ``bedrock:InvokeModel`` covers Converse —
the IAM grant lives in terraform/knowledge (BedrockRecalibrate).

Env:
  RECALIBRATE_MODEL_ID   (default: us.amazon.nova-2-lite-v1:0; read at CALL time)
  AWS_DEFAULT_REGION     (default: us-east-1)
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

_DEFAULT_MODEL = "us.amazon.nova-2-lite-v1:0"

_client: Any | None = None


class BedrockTextError(Exception):
    """Bedrock Converse call failed or returned no usable text."""


def recalibrate_model_id() -> str:
    """Model id for the recalibrate proposer. Read at call time so tests
    (and a future env flip on the mcp2 box) need no module re-import."""
    return os.environ.get("RECALIBRATE_MODEL_ID", "").strip() or _DEFAULT_MODEL


def _bedrock() -> Any:
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _client


def converse_text(prompt: str, *, system: str | None = None, max_tokens: int = 2000) -> str:
    """One-shot Converse call → concatenated output text.

    Synchronous (boto3); async callers wrap it in ``run_in_executor`` —
    the same convention as ``embeddings_bedrock._invoke_one``. Raises
    :class:`BedrockTextError` on any transport, auth, throttling, or
    response-shape failure so callers get exactly one exception type.
    """
    kwargs: dict[str, Any] = {
        "modelId": recalibrate_model_id(),
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    try:
        resp = _bedrock().converse(**kwargs)
        blocks = resp["output"]["message"]["content"]
        text = "".join(b.get("text", "") for b in blocks)
    except (BotoCoreError, ClientError, KeyError, TypeError) as exc:
        raise BedrockTextError(f"Bedrock Converse failed: {exc}") from exc
    if not text.strip():
        raise BedrockTextError("Model returned empty text")
    return text


__all__ = ["BedrockTextError", "converse_text", "recalibrate_model_id"]
