"""OpenAI embeddings for semantic bookmark search (SQLite storage only)."""

from __future__ import annotations

import os

import httpx

DEFAULT_EMBED_MODEL = "text-embedding-3-small"


def embed_model() -> str:
    return os.environ.get("OPENAI_EMBED_MODEL", DEFAULT_EMBED_MODEL)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    model = embed_model()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": texts},
        )
        r.raise_for_status()
        data = r.json()
    ordered = sorted(data["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in ordered]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
