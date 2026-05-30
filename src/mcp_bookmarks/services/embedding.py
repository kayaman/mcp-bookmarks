"""EmbeddingService — capability-gated semantic-search orchestration.

`index_bookmark_embedding` and `semantic_search_bookmarks` MCP tools used
to compute embeddings, gate on the `semantic_search` capability, and
call `db.upsert_bookmark_embedding` / `db.get_all_embeddings` inline.
Both `db.*` methods live only on SQLite `Database`; the cast(Any, db)
acknowledgement of the protocol gap now lives here.

When (or if) DynamoDB grows a vector pipeline (ADR-0005), this file is
where the new branch lands.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from ..backend import BookmarkBackend, UnsupportedCapability, require_capability

log = logging.getLogger(__name__)


async def index_bookmark(*, db: BookmarkBackend, bookmark_id: int, text: str) -> tuple[str, int]:
    """Embed ``text`` and persist the vector for ``bookmark_id``.

    Returns ``(model_name, chars_used)``. Raises
    :class:`UnsupportedCapability` when the backend lacks
    ``semantic_search``; callers catch and serialize.
    """
    require_capability(db, "semantic_search", method="index_bookmark_embedding")
    from ..rag import embed_model, embed_texts

    vec = (await embed_texts([text]))[0]
    model = embed_model()
    await cast(Any, db).upsert_bookmark_embedding(bookmark_id, model, vec)
    log.info(
        "bookmark_embedded",
        extra={"bookmark_id": bookmark_id, "model": model, "chars": len(text)},
    )
    return model, len(text)


async def semantic_search(
    *, db: BookmarkBackend, query: str, limit: int = 8
) -> tuple[list[tuple[int, float]], str]:
    """Cosine-similarity search; returns ``(ranked, model_used)``.

    ``ranked`` is a list of ``(bookmark_id, score)`` sorted descending.
    Raises :class:`UnsupportedCapability` on the wrong backend; caller
    serializes.
    """
    require_capability(db, "semantic_search", method="semantic_search_bookmarks")
    from ..rag import cosine_similarity, embed_model, embed_texts

    qv = (await embed_texts([query]))[0]
    model = embed_model()
    rows = await cast(Any, db).get_all_embeddings(model)
    if not rows:
        return [], model
    scored = sorted(
        ((bid, cosine_similarity(qv, vec)) for bid, vec in rows),
        key=lambda item: item[1],
        reverse=True,
    )
    return scored[:limit], model


__all__ = ["UnsupportedCapability", "index_bookmark", "semantic_search"]
