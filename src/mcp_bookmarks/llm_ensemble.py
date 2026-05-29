"""Multi-model completions via an OpenAI-compatible AI Gateway + LLM-as-judge."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_TIMEOUT = 120.0
MAX_CANDIDATE_CHARS = 12_000


def _gateway_base() -> str:
    return (
        os.environ.get("AI_GATEWAY_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )


def _api_key() -> str:
    return (os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()


def _chat_completions_url() -> str:
    b = _gateway_base().strip().rstrip("/")
    if not b.endswith("/v1"):
        b = f"{b}/v1"
    return f"{b}/chat/completions"


def _default_models() -> list[str]:
    raw = os.environ.get("ENSEMBLE_MODELS", "").strip()
    if not raw:
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


def _default_judge_model() -> str:
    return os.environ.get("JUDGE_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def ensemble_enabled() -> bool:
    return os.environ.get("ENSEMBLE_ENABLED", "").lower() in ("1", "true", "yes")


def gateway_status_public() -> dict[str, Any]:
    """Safe metadata for the AI Gateway dashboard (no secrets)."""
    raw = _gateway_base().strip().rstrip("/")
    display = ""
    if raw:
        try:
            url = raw if "://" in raw else f"https://{raw}"
            parsed = urlparse(url)
            display = (parsed.netloc or parsed.path or "").strip("/") or raw[:120]
        except Exception:
            display = raw[:120]
    return {
        "ensemble_enabled": ensemble_enabled(),
        "default_models": _default_models(),
        "default_judge": _default_judge_model(),
        "has_api_key_configured": bool(_api_key()),
        "gateway_display": display or "(default OpenAI host)",
    }


async def _chat_completion(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.4,
) -> tuple[str, str | None, str | None]:
    """Returns (model, content, error)."""
    key = _api_key()
    if not key:
        return model, None, "AI_GATEWAY_API_KEY or OPENAI_API_KEY is not set"
    url = _chat_completions_url()
    try:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
            },
        )
        r.raise_for_status()
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if content is None:
            return model, None, "empty completion"
        return model, str(content).strip(), None
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500] if e.response is not None else ""
        return model, None, f"HTTP {e.response.status_code if e.response else '?'}: {body}"
    except Exception as e:
        return model, None, str(e)


def _parse_judge_json(text: str) -> dict[str, Any]:
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if fence:
        s = fence.group(1).strip()
    return json.loads(s)


async def run_ensemble_with_judge(
    task: str,
    *,
    models: list[str] | None = None,
    judge_model: str | None = None,
    max_candidate_chars: int = MAX_CANDIDATE_CHARS,
) -> dict[str, Any]:
    """Run parallel chat completions, then a judge model. Returns structured dict."""
    mids = models if models else _default_models()
    if not mids:
        return {
            "ok": False,
            "error": "No models: pass `models` or set ENSEMBLE_MODELS (comma-separated).",
        }
    if not _api_key():
        return {"ok": False, "error": "Missing AI_GATEWAY_API_KEY or OPENAI_API_KEY"}

    judge = (judge_model or _default_judge_model()).strip()
    user_messages = [{"role": "user", "content": task.strip()}]

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        results = await asyncio.gather(
            *[_chat_completion(client, m, user_messages, temperature=0.5) for m in mids],
            return_exceptions=False,
        )

    candidates: list[dict[str, Any]] = []
    for model, content, err in results:
        entry: dict[str, Any] = {"model": model}
        if err:
            entry["error"] = err
            entry["content"] = None
        else:
            text = content or ""
            entry["content"] = text[:max_candidate_chars]
            entry["truncated"] = len(text) > max_candidate_chars
        candidates.append(entry)

    ok_contents = [c for c in candidates if c.get("content")]
    if not ok_contents:
        return {
            "ok": False,
            "candidates": candidates,
            "error": "All model calls failed; nothing to judge.",
        }

    judge_system = (
        "You are an impartial judge. You receive a user task and several candidate answers "
        "from different models. Pick the best answer or merge the strongest parts. "
        "Respond with a single JSON object only, no markdown, keys: "
        "chosen_index (integer 0 .. N-1 matching the Index lines below in order), "
        "rationale (short string), "
        "answer (final string to return to the user — may copy or synthesize)."
    )
    lines = [f"Task:\n{task.strip()}\n", "\nSuccessful candidates (index is chosen_index):\n"]
    j = 0
    for c in candidates:
        if not c.get("content"):
            lines.append(f"--- FAILED model={c['model']}: {c.get('error', 'unknown')} ---\n")
            continue
        lines.append(f"--- Index {j} | model={c['model']} ---\n{c['content']}\n")
        j += 1

    judge_user = "".join(lines)
    judge_messages = [
        {"role": "system", "content": judge_system},
        {"role": "user", "content": judge_user},
    ]

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        _, judge_raw, jerr = await _chat_completion(client, judge, judge_messages, temperature=0.2)

    if jerr or not judge_raw:
        return {
            "ok": True,
            "partial": True,
            "candidates": candidates,
            "error": jerr or "empty judge",
            "answer": ok_contents[0]["content"],
            "winner_model": ok_contents[0]["model"],
        }

    try:
        parsed = _parse_judge_json(judge_raw)
        chosen = int(parsed.get("chosen_index", 0))
        answer = str(parsed.get("answer", "")).strip()
        rationale = str(parsed.get("rationale", "")).strip()
        if not answer:
            raise ValueError("empty answer")
        # Map chosen_index to model label among successful only
        success_models = [c["model"] for c in candidates if c.get("content")]
        winner_model = (
            success_models[chosen] if 0 <= chosen < len(success_models) else success_models[0]
        )
        return {
            "ok": True,
            "partial": False,
            "candidates": candidates,
            "judge_model": judge,
            "chosen_index": chosen,
            "rationale": rationale,
            "answer": answer,
            "winner_model": winner_model,
        }
    except (json.JSONDecodeError, ValueError, TypeError, IndexError):
        return {
            "ok": True,
            "partial": True,
            "candidates": candidates,
            "judge_model": judge,
            "judge_raw": judge_raw[:8000],
            "answer": ok_contents[0]["content"],
            "winner_model": ok_contents[0]["model"],
            "note": "Judge did not return valid JSON; first successful candidate returned.",
        }
