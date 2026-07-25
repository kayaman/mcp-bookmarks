"""In-process ANN index over "Knowledge" bookmarks for semantic search.

This is the cloud/EC2 replacement for the SQLite + brute-force cosine path in
``rag.py``/``services/embedding.py``. It holds an `hnswlib` index in memory,
built from the owner's ``bookmarkType == "knowledge"`` rows in DynamoDB and
embedded via Bedrock (``embeddings_bedrock``). The index persists to a local
path (EBS on the instance) and can snapshot to S3 so an instance replacement
warm-starts instead of re-embedding the whole corpus.

Design choices (see docs/dynamodb-rag-design.md):
  - One vector per bookmark (Knowledge ``aiContent`` is capped ~10k chars, so a
    single embedding window is enough — no chunking in v1).
  - ``content_hash`` over the composed text drives incremental refresh: only
    new/changed bookmarks are re-embedded; removed ones are ``mark_deleted``.
  - Query-time filtering (user + scope) is applied to over-fetched candidates,
    NOT baked into the index, so one index can serve multiple users safely.

Env:
  KNOWLEDGE_INDEX_PATH            (default: <cwd>/.knowledge_index/index.bin)
  KNOWLEDGE_INDEX_USER_IDS        (comma-sep; default: OWNER_USER_ID or DYNAMODB_USER_ID)
  KNOWLEDGE_INDEX_S3_BUCKET / _KEY (optional snapshot target)
  KNOWLEDGE_INDEX_MAX_ELEMENTS    (default: 10000)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("mcp_bookmarks.knowledge_index")

KNOWLEDGE_TYPE = "knowledge"


# ── The tunable seam: what text represents a bookmark ──────────────────
#
# This function decides what an agent's semantic query is matched against.
# It's the single biggest lever on retrieval quality. The default below leads
# with the human-authored signal (title + AI summary) and appends the extracted
# body so specific phrases still match. Refine freely — e.g. weight the summary
# by repeating it, fold in tags/entities, or drop the raw body if summaries
# prove sufficient.
def compose_embedding_text(item: dict) -> str:
    """Compose the text embedded for one Knowledge bookmark (raw DDB item)."""
    title = (item.get("ogTitle") or item.get("title") or "").strip()
    summary = (item.get("aiSummary") or "").strip()
    # Prefer the fuller deep-extract transcript when present (YouTube), else the
    # capped article/transcript body.
    body = (item.get("deepText") or item.get("aiContent") or "").strip()
    tags = " ".join(f"#{t}" for t in item.get("aiTags", []) if t)
    parts = [p for p in (title, summary, tags, body) if p]
    return "\n\n".join(parts)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_from_item(item: dict, text: str) -> dict:
    """Minimal manifest record kept per indexed bookmark (for filtering + display)."""
    return {
        "id": item.get("id"),
        "userId": item.get("userId"),
        "url": item.get("url"),
        "title": item.get("ogTitle") or item.get("title") or item.get("url"),
        "summary": (item.get("aiSummary") or "")[:400],
        "tags": list(item.get("aiTags", [])),
        "mcpExposed": item.get("mcpExposed"),
        "hash": content_hash(text),
    }


@dataclass
class KnowledgeIndex:
    """hnswlib index + id/label bookkeeping for Knowledge semantic search."""

    dim: int
    path: Path
    max_elements: int = 10_000
    s3_bucket: str | None = None
    s3_key: str | None = None

    _index: Any = None  # hnswlib.Index
    _by_label: dict[int, dict] = field(default_factory=dict)  # label -> record
    _label_by_id: dict[str, int] = field(default_factory=dict)  # bookmark id -> label
    _next_label: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _ready: bool = False

    # ── index lifecycle ────────────────────────────────────────────
    def _new_index(self) -> Any:
        import hnswlib

        idx = hnswlib.Index(space="cosine", dim=self.dim)
        idx.init_index(
            max_elements=self.max_elements,
            ef_construction=200,
            M=16,
            allow_replace_deleted=True,
        )
        idx.set_ef(max(64, self.dim // 8))
        return idx

    def _ensure_capacity(self, additional: int) -> None:
        need = self._next_label + additional
        cur = self._index.get_max_elements()
        if need > cur:
            self._index.resize_index(max(need, int(cur * 1.5) + 1))

    @property
    def manifest_path(self) -> Path:
        return self.path.with_suffix(".manifest.json")

    @property
    def size(self) -> int:
        return len(self._label_by_id)

    @property
    def ready(self) -> bool:
        return self._ready

    # ── build / refresh ────────────────────────────────────────────
    async def build(
        self,
        db: Any,
        user_ids: list[str],
        embed: Callable[[list[str]], Awaitable[list[list[float]]]],
    ) -> int:
        """Full rebuild from scratch over ``user_ids``' Knowledge bookmarks."""
        async with self._lock:
            import numpy as np

            self._index = self._new_index()
            self._by_label.clear()
            self._label_by_id.clear()
            self._next_label = 0

            items: list[dict] = []
            for uid in user_ids:
                items.extend(await db.query_raw_by_type(KNOWLEDGE_TYPE, user_id=uid))

            texts, records = [], []
            for it in items:
                if not it.get("id"):
                    continue
                text = compose_embedding_text(it)
                if not text.strip():
                    continue
                texts.append(text)
                records.append(_record_from_item(it, text))

            if texts:
                vectors = await embed(texts)
                self._ensure_capacity(len(vectors))
                labels = list(range(len(vectors)))
                self._index.add_items(np.asarray(vectors, dtype="float32"), labels)
                for label, rec in zip(labels, records, strict=True):
                    self._by_label[label] = rec
                    self._label_by_id[rec["id"]] = label
                self._next_label = len(vectors)

            self._ready = True
            log.info("knowledge_index_built", extra={"count": self.size})
            self._save_unlocked()
            return self.size

    async def refresh(
        self,
        db: Any,
        user_ids: list[str],
        embed: Callable[[list[str]], Awaitable[list[list[float]]]],
    ) -> dict:
        """Incremental update: (re)embed new/changed, drop removed. Returns stats."""
        if self._index is None:
            built = await self.build(db, user_ids, embed)
            return {"rebuilt": True, "count": built}
        async with self._lock:
            import numpy as np

            items: list[dict] = []
            for uid in user_ids:
                items.extend(await db.query_raw_by_type(KNOWLEDGE_TYPE, user_id=uid))

            current_ids: set[str] = set()
            to_embed_texts, to_embed_records = [], []
            for it in items:
                bid = it.get("id")
                if not bid:
                    continue
                current_ids.add(bid)
                text = compose_embedding_text(it)
                if not text.strip():
                    continue
                rec = _record_from_item(it, text)
                existing_label = self._label_by_id.get(bid)
                if (
                    existing_label is not None
                    and self._by_label[existing_label]["hash"] == rec["hash"]
                ):
                    continue  # unchanged
                to_embed_texts.append(text)
                to_embed_records.append(rec)

            removed = [bid for bid in self._label_by_id if bid not in current_ids]
            for bid in removed:
                label = self._label_by_id.pop(bid)
                self._by_label.pop(label, None)
                with_suppress_delete(self._index, label)

            added = 0
            if to_embed_texts:
                vectors = await embed(to_embed_texts)
                self._ensure_capacity(len(vectors))
                for vec, rec in zip(vectors, to_embed_records, strict=True):
                    bid = rec["id"]
                    old = self._label_by_id.get(bid)
                    if old is not None:  # changed content: retire the old vector
                        self._by_label.pop(old, None)
                        with_suppress_delete(self._index, old)
                    label = self._next_label
                    self._next_label += 1
                    self._index.add_items(
                        np.asarray([vec], dtype="float32"), [label], replace_deleted=True
                    )
                    self._by_label[label] = rec
                    self._label_by_id[bid] = label
                    added += 1

            self._ready = True
            self._save_unlocked()
            stats = {"added": added, "removed": len(removed), "count": self.size}
            log.info("knowledge_index_refreshed", extra=stats)
            return stats

    # ── query ──────────────────────────────────────────────────────
    def search(
        self, query_vec: list[float], k: int, over_fetch: int = 5
    ) -> list[tuple[dict, float]]:
        """Return ``[(record, score), ...]`` ranked by cosine similarity.

        Over-fetches so the caller can drop records failing the user/scope
        filter and still return ``k`` results. Scores are ``1 - cosine_distance``
        (1.0 = identical direction).
        """
        if not self._ready or self.size == 0:
            return []
        import numpy as np

        fetch = min(max(k * over_fetch, k), self.size)
        labels, distances = self._index.knn_query(np.asarray([query_vec], dtype="float32"), k=fetch)
        out: list[tuple[dict, float]] = []
        for label, dist in zip(labels[0], distances[0], strict=True):
            rec = self._by_label.get(int(label))
            if rec is not None:
                out.append((rec, 1.0 - float(dist)))
        return out

    # ── persistence ────────────────────────────────────────────────
    def _save_unlocked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._index.save_index(str(self.path))
            self.manifest_path.write_text(
                json.dumps(
                    {
                        "dim": self.dim,
                        "next_label": self._next_label,
                        "by_label": {str(k): v for k, v in self._by_label.items()},
                        "label_by_id": self._label_by_id,
                    }
                )
            )
            self._snapshot_to_s3()
        except Exception:  # persistence is best-effort; never fail a build on it
            log.exception("knowledge_index_save_failed")

    def load(self) -> bool:
        """Load a persisted index (local, or hydrate from S3 first). Returns success."""
        try:
            if not self.path.exists() and self.s3_bucket and self.s3_key:
                self._hydrate_from_s3()
            if not self.path.exists() or not self.manifest_path.exists():
                return False
            meta = json.loads(self.manifest_path.read_text())
            if int(meta.get("dim", -1)) != self.dim:
                log.warning("knowledge_index_dim_mismatch; rebuilding")
                return False
            self._index = self._new_index()
            self._index.load_index(str(self.path), max_elements=self.max_elements)
            self._by_label = {int(k): v for k, v in meta["by_label"].items()}
            self._label_by_id = dict(meta["label_by_id"])
            self._next_label = int(meta.get("next_label", len(self._by_label)))
            self._ready = True
            log.info("knowledge_index_loaded", extra={"count": self.size})
            return True
        except Exception:
            log.exception("knowledge_index_load_failed")
            return False

    def _s3(self) -> Any:
        import boto3

        return boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

    def _snapshot_to_s3(self) -> None:
        if not (self.s3_bucket and self.s3_key):
            return
        s3 = self._s3()
        s3.upload_file(str(self.path), self.s3_bucket, self.s3_key)
        s3.upload_file(str(self.manifest_path), self.s3_bucket, self.s3_key + ".manifest.json")

    def _hydrate_from_s3(self) -> None:
        # Mirrors the guard in _snapshot_to_s3. The sole caller already checks both
        # fields, but narrowing str|None → str doesn't cross the method boundary, and
        # a guard here keeps the method safe if it ever gains another caller.
        if not (self.s3_bucket and self.s3_key):
            return
        s3 = self._s3()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(self.s3_bucket, self.s3_key, str(self.path))
        s3.download_file(self.s3_bucket, self.s3_key + ".manifest.json", str(self.manifest_path))


def with_suppress_delete(index: Any, label: int) -> None:
    """``mark_deleted`` that tolerates an already-deleted/unknown label."""
    with contextlib.suppress(Exception):
        index.mark_deleted(label)


# ── process-global singleton + config ──────────────────────────────────

_INDEX: KnowledgeIndex | None = None


def index_user_ids() -> list[str]:
    raw = os.environ.get("KNOWLEDGE_INDEX_USER_IDS", "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    owner = (
        os.environ.get("OWNER_USER_ID", "").strip()
        or os.environ.get("DYNAMODB_USER_ID", "").strip()
    )
    return [owner] if owner else []


def get_knowledge_index() -> KnowledgeIndex | None:
    return _INDEX


def init_knowledge_index() -> KnowledgeIndex:
    """Construct (once) the process-global index from env config."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    from .embeddings_bedrock import embed_dims

    path = Path(
        os.environ.get("KNOWLEDGE_INDEX_PATH", str(Path.cwd() / ".knowledge_index" / "index.bin"))
    )
    _INDEX = KnowledgeIndex(
        dim=embed_dims(),
        path=path,
        max_elements=int(os.environ.get("KNOWLEDGE_INDEX_MAX_ELEMENTS", "10000")),
        s3_bucket=os.environ.get("KNOWLEDGE_INDEX_S3_BUCKET") or None,
        s3_key=os.environ.get("KNOWLEDGE_INDEX_S3_KEY") or None,
    )
    return _INDEX


__all__ = [
    "KNOWLEDGE_TYPE",
    "KnowledgeIndex",
    "compose_embedding_text",
    "content_hash",
    "get_knowledge_index",
    "index_user_ids",
    "init_knowledge_index",
]
