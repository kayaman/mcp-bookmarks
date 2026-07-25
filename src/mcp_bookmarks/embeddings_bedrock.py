"""AWS Bedrock embeddings (Titan Text Embeddings v2) for cloud semantic search.

Mirrors the ``rag.py`` OpenAI interface (``embed_model`` / ``embed_texts``) so
the ANN layer (``knowledge_index``) stays provider-agnostic. Chosen for the
DynamoDB/EC2 deployment because Bedrock is already in-account and IAM-scoped —
no external API key, and Knowledge content never leaves AWS.

Titan Text Embeddings v2 (``amazon.titan-embed-text-v2:0``) takes ONE input per
``invoke_model`` call and, with ``normalize=True``, returns unit vectors (so
cosine similarity == dot product). Supported dimensionalities: 256 | 512 | 1024.

Env:
  BEDROCK_EMBED_MODEL   (default: amazon.titan-embed-text-v2:0)
  BEDROCK_EMBED_DIMS    (default: 1024; Titan v2 supports 256|512|1024)
  AWS_DEFAULT_REGION    (default: us-east-1)
"""

from __future__ import annotations

import asyncio
import json
import os
from functools import partial
from typing import Any

import boto3

_DEFAULT_MODEL = "amazon.titan-embed-text-v2:0"
_DEFAULT_DIMS = 1024
# Titan is one-input-per-invoke; bound fan-out so a full index build doesn't
# trip Bedrock account throttling.
_MAX_CONCURRENCY = 8
# Titan v2 caps input at ~8192 tokens. Knowledge text is far smaller (aiContent
# ≤10k chars), but truncate defensively so an oversized item can't 400 a build.
_MAX_INPUT_CHARS = 40_000

_client: Any | None = None


def embed_model() -> str:
    return os.environ.get("BEDROCK_EMBED_MODEL", _DEFAULT_MODEL)


def embed_dims() -> int:
    try:
        return int(os.environ.get("BEDROCK_EMBED_DIMS", str(_DEFAULT_DIMS)))
    except ValueError:
        return _DEFAULT_DIMS


def _bedrock() -> Any:
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _client


def _invoke_one(text: str, model: str, dims: int) -> list[float]:
    # Titan rejects empty inputText; a single space is a harmless stand-in that
    # keeps result/label alignment for callers that didn't pre-filter.
    body = {
        "inputText": (text or " ")[:_MAX_INPUT_CHARS],
        "dimensions": dims,
        "normalize": True,
    }
    resp = _bedrock().invoke_model(modelId=model, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    return payload["embedding"]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` → unit vectors, order-preserving.

    One Bedrock call per input, fanned out up to ``_MAX_CONCURRENCY`` at a time.
    Raises whatever boto3 raises (throttling, access denied) — callers decide
    whether to degrade or fail.
    """
    if not texts:
        return []
    model, dims = embed_model(), embed_dims()
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _one(t: str) -> list[float]:
        async with sem:
            return await loop.run_in_executor(None, partial(_invoke_one, t, model, dims))

    return list(await asyncio.gather(*[_one(t) for t in texts]))


__all__ = ["embed_dims", "embed_model", "embed_texts"]
