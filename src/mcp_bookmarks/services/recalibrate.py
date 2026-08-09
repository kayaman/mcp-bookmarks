"""Recalibrate service (Phase 2): taxonomy merge/rename ops from human corrections.

``propose()`` reads the live taxonomy + the most recent human tag edits,
asks the Bedrock Converse text model for merge/rename ops, validates them,
and returns them with affected-bookmark counts. **Nothing is written** and
proposals are not persisted — the admin approves ops in the UI, then the
endpoint calls ``apply()`` with the approved subset.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from functools import partial

from ..backend import BookmarkBackend
from ..bedrock_text import BedrockTextError, converse_text

log = logging.getLogger(__name__)

# Targets are restricted to the canonical two-word #word-word shape by
# design (flat-taxonomy convention) — merging INTO legacy single-word tags
# is unsupported in v1.
_TARGET_RE = re.compile(r"^[a-z]+-[a-z]+$")
_EDITS_WINDOW = 200
_MAX_CORRECTION_LINES = 30

# Owner taxonomy policy (binding): compact catalog — just enough tags,
# always evolving; prefer existing tags; actively consolidate.
_SYSTEM_PROMPT = (
    "You are a taxonomy curator for a personal bookmark knowledge base. "
    "The taxonomy GOAL is a compact catalog: just enough tags, always "
    "evolving — never a sprawling one. PREFER merging into existing live "
    "tags (especially higher-usage ones) over inventing new slugs; propose "
    "a brand-new target slug only when no suitable existing tag fits. "
    "Actively consolidate rarely-used or overlapping tags — net catalog "
    "shrinkage is desirable when the corpus and the human corrections "
    "support it. You consolidate near-duplicate tags and rename "
    "off-convention tags based on the human corrections you are shown. "
    "Every tag is a lowercase slug; the canonical target shape is exactly "
    "two hyphenated words (e.g. 'machine-learning'). Respond with STRICT "
    "JSON only — no prose, no markdown fences — of the shape "
    '{"ops": [{"source": "a-b", "target": "c-d", "reason": "..."}]}. '
    "Sources must be existing live tags. Never chain ops: no op's target "
    "may be another op's source. "
    'Return {"ops": []} when no change is clearly supported.'
)


class ProposeFailed(Exception):
    """Bedrock call failed or the model output was unusable. Nothing was written."""


def _build_prompt(tags: list, edits: list[dict]) -> str:
    tag_lines = "\n".join(f"- {t.slug} (used by {t.usage_count} bookmarks)" for t in tags)
    removed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    for e in edits:
        if e.get("actor") != "human":
            continue
        removed.update(e.get("removed", []))
        added.update(e.get("added", []))
    correction_lines = [
        f"- removed by the human {n}x: {slug}"
        for slug, n in removed.most_common(_MAX_CORRECTION_LINES)
    ] + [
        f"- added by the human {n}x: {slug}" for slug, n in added.most_common(_MAX_CORRECTION_LINES)
    ]
    corrections = "\n".join(correction_lines) or "- (no human corrections yet)"
    return (
        "Live tags:\n"
        f"{tag_lines or '- (none)'}\n\n"
        "Human corrections (aggregated from the most recent tag edits):\n"
        f"{corrections}\n\n"
        "Propose merge/rename operations as strict JSON."
    )


def _parse_ops(raw: str) -> list[dict]:
    """Parse the model's JSON, tolerating markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposeFailed(f"Model output is not valid JSON: {exc}") from exc
    ops = data.get("ops") if isinstance(data, dict) else None
    if not isinstance(ops, list):
        raise ProposeFailed('Model output has no "ops" list')
    return [op for op in ops if isinstance(op, dict)]


def _drop_conflicting(ops: list[dict]) -> list[dict]:
    """Enforce disjointness, dropping LATER conflicting ops.

    Disjoint = sources pairwise distinct AND no op's target appears as
    another op's source (in either direction — chained proposals go in
    separate apply rounds).
    """
    kept: list[dict] = []
    sources: set[str] = set()
    targets: set[str] = set()
    for op in ops:
        s, t = op["source"], op["target"]
        if s in sources or s in targets or t in sources:
            continue
        kept.append(op)
        sources.add(s)
        targets.add(t)
    return kept


async def propose(db: BookmarkBackend) -> dict:
    """Read-only proposal. Pinned response contract:

    {"ops": [{"kind": "merge"|"rename", "source", "target",
              "bookmarksAffected", "reason"}],
     "editsConsidered": M, "tagsConsidered": K}
    """
    tags = await db.get_all_tags()  # live only — tombstoned rows filtered by the backend
    edits = await db.get_tag_edits(limit=_EDITS_WINDOW)
    prompt = _build_prompt(tags, edits)
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(
            None, partial(converse_text, prompt, system=_SYSTEM_PROMPT)
        )
    except BedrockTextError as exc:
        raise ProposeFailed(str(exc)) from exc

    live = {t.slug for t in tags}
    candidates: list[dict] = []
    for op in _parse_ops(raw):
        source = op.get("source")
        target = op.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        if source not in live:  # unknown or tombstoned source — drop
            continue
        if source == target or not _TARGET_RE.fullmatch(target):
            continue
        candidates.append(
            {"source": source, "target": target, "reason": str(op.get("reason") or "")}
        )

    ops_out: list[dict] = []
    for op in _drop_conflicting(candidates):
        ops_out.append(
            {
                "kind": "merge" if op["target"] in live else "rename",
                "source": op["source"],
                "target": op["target"],
                "bookmarksAffected": await db.count_bookmarks_with_tag(op["source"]),
                "reason": op["reason"],
            }
        )
    log.info(
        "recalibrate_proposed",
        extra={"ops": len(ops_out), "edits": len(edits), "tags": len(tags)},
    )
    return {"ops": ops_out, "editsConsidered": len(edits), "tagsConsidered": len(tags)}


async def apply(db: BookmarkBackend, ops: list[dict]) -> tuple[dict | None, str | None]:
    """Execute admin-approved ops. Returns ``(result, None)`` or ``(None, reason)``.

    Validation runs to completion over ALL ops before the first write —
    any failure returns ``(None, reason)`` (endpoint → 400 invalid_request)
    with nothing written. Execution then coalesces per bookmark: one
    ``replace_bookmark_tags(..., actor="recalibrate")`` per touched
    bookmark (Phase 1 snapshot + edit-log semantics ride along), and each
    source tag is tombstoned only AFTER every rewrite succeeded — so a
    retry after a partial failure re-sweeps live sources and reports
    fully-applied ones as ``alreadyApplied``.
    """
    if not ops:
        return None, "No ops submitted"

    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    to_run: list[dict] = []  # live sources, submitted order
    already: list[dict] = []  # sources already tombstoned → skip + report
    for op in ops:
        source = op.get("source")
        target = op.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            return None, "Each op needs string 'source' and 'target'"
        if not _TARGET_RE.fullmatch(target):
            return None, f"Invalid target slug (must match ^[a-z]+-[a-z]+$): {target!r}"
        if source == target:
            return None, f"Op source equals target: {source!r}"
        if source in seen_sources or source in seen_targets or target in seen_sources:
            return None, f"Ops are not disjoint around {source!r} -> {target!r}"
        seen_sources.add(source)
        seen_targets.add(target)
        tag = await db.get_tag_by_slug(source)
        if tag is None:
            return None, f"Source tag never existed: {source!r}"
        if tag.deprecated_as:
            already.append({"source": source, "target": target})
        else:
            to_run.append({"source": source, "target": target})

    mapping = {op["source"]: op["target"] for op in to_run}
    rewritten: dict[str, int] = dict.fromkeys(mapping, 0)
    if mapping:
        rows = await db.get_bookmarks_with_any_tag(list(mapping))
        for row in rows:
            tags = list(row["tags"])
            hit_sources = [s for s in mapping if s in tags]
            if not hit_sources:
                continue
            new_tags = list(dict.fromkeys(mapping.get(t, t) for t in tags))
            await db.replace_bookmark_tags(row["id"], new_tags, actor="recalibrate")
            for s in hit_sources:
                rewritten[s] += 1
        # Tombstone each source only after ALL bookmark rewrites succeeded.
        for op in to_run:
            await db.tombstone_tag(op["source"], op["target"])
            log.info(
                "recalibrate_applied",
                extra={
                    "source": op["source"],
                    "target": op["target"],
                    "rewritten": rewritten[op["source"]],
                },
            )

    by_source: dict[str, dict] = {}
    for op in to_run:
        by_source[op["source"]] = {
            "source": op["source"],
            "target": op["target"],
            "status": "applied",
            "bookmarksRewritten": rewritten[op["source"]],
        }
    for op in already:
        by_source[op["source"]] = {
            "source": op["source"],
            "target": op["target"],
            "status": "alreadyApplied",
            "bookmarksRewritten": 0,
        }
    results = [by_source[str(op["source"])] for op in ops]
    return {"results": results}, None
