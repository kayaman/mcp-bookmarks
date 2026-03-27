"""
Lambda handler — processes new links from DynamoDB Streams with a Claude AI agent.

Flow:
  1. A new item {id, url, status: "PENDING"} is inserted into the links table
  2. DynamoDB Streams triggers this Lambda on INSERT
  3. Claude (with tool use) orchestrates:
       fetch_url_metadata → extract_article_content → get_tags
       → (create_tag if needed) → save_bookmark
  4. The processed item is updated in-place with title, content, summary, tags, status=DONE

Required env vars:
  LINKS_TABLE   — DynamoDB table name for bookmark items
  TAGS_TABLE    — DynamoDB table name for tag taxonomy
  ANTHROPIC_API_KEY — Anthropic API key
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import anthropic
import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── AWS clients ───────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
LINKS_TABLE = dynamodb.Table(os.environ["LINKS_TABLE"])
TAGS_TABLE = dynamodb.Table(os.environ["TAGS_TABLE"])

# ── Anthropic ─────────────────────────────────────────────────────────────────
claude = anthropic.Anthropic()

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
_FETCH_TIMEOUT = 15.0
_CONTENT_CHAR_LIMIT = 10_000  # cap content stored in DynamoDB


# ═════════════════════════════════════════════════════════════════════════════
# Tool implementations
# ═════════════════════════════════════════════════════════════════════════════


async def _fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=_FETCH_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return resp.text


async def _tool_fetch_metadata(url: str) -> dict:
    """Fetch OG metadata from a URL."""
    try:
        html = await _fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        def og(prop: str) -> str | None:
            tag = soup.find("meta", property=f"og:{prop}")
            return tag["content"].strip() if tag and tag.get("content") else None

        def meta(name: str) -> str | None:
            tag = soup.find("meta", attrs={"name": name})
            return tag["content"].strip() if tag and tag.get("content") else None

        return {
            "title": og("title") or (soup.title.string.strip() if soup.title and soup.title.string else None),
            "description": og("description") or meta("description"),
            "image_url": og("image"),
            "site_name": og("site_name"),
        }
    except Exception as exc:
        logger.warning("fetch_metadata failed for %s: %s", url, exc)
        return {"title": None, "description": None, "image_url": None, "site_name": None, "error": str(exc)}


async def _tool_extract_content(url: str) -> dict:
    """Extract full article text via trafilatura with BS4 fallback."""
    try:
        import trafilatura

        html = await _fetch_html(url)
        text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True)

        if not text or len(text.strip()) < 100:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 30]
            text = "\n\n".join(paragraphs)

        text = (text or "")[:_CONTENT_CHAR_LIMIT]
        return {"text": text, "word_count": len(text.split())}
    except Exception as exc:
        logger.warning("extract_content failed for %s: %s", url, exc)
        return {"text": "", "word_count": 0, "error": str(exc)}


def _tool_get_tags() -> list[dict]:
    """Return all tags from the DynamoDB taxonomy table."""
    resp = TAGS_TABLE.scan(
        ProjectionExpression="slug, #n, description, usage_count",
        ExpressionAttributeNames={"#n": "name"},
    )
    items = resp.get("Items", [])
    return sorted(items, key=lambda t: int(t.get("usage_count", 0)), reverse=True)


def _tool_create_tag(slug: str, name: str, description: str) -> dict:
    """Create a new tag in the taxonomy table."""
    TAGS_TABLE.put_item(
        Item={
            "slug": slug,
            "name": name,
            "description": description,
            "usage_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        ConditionExpression=Attr("slug").not_exists(),  # idempotent
    )
    return {"slug": slug, "name": name, "created": True}


def _tool_save_bookmark(
    item_id: str,
    title: str | None,
    description: str | None,
    content: str | None,
    summary: str,
    tags: list[str],
    image_url: str | None,
    site_name: str | None,
    word_count: int,
) -> dict:
    """Write the processed bookmark back to the links table and bump tag counters."""
    now = datetime.now(timezone.utc).isoformat()

    LINKS_TABLE.update_item(
        Key={"id": item_id},
        UpdateExpression=(
            "SET #st = :done, title = :title, description = :desc, "
            "content = :content, summary = :summary, tags = :tags, "
            "image_url = :img, site_name = :site, word_count = :wc, updated_at = :now"
        ),
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":done": "DONE",
            ":title": title or "",
            ":desc": description or "",
            ":content": content or "",
            ":summary": summary,
            ":tags": tags,
            ":img": image_url or "",
            ":site": site_name or "",
            ":wc": word_count,
            ":now": now,
        },
    )

    for slug in tags:
        try:
            TAGS_TABLE.update_item(
                Key={"slug": slug},
                UpdateExpression="ADD usage_count :one",
                ExpressionAttributeValues={":one": 1},
            )
        except ClientError:
            pass  # tag may not exist yet if create_tag was skipped

    return {"status": "DONE", "item_id": item_id, "tags": tags}


# ═════════════════════════════════════════════════════════════════════════════
# Tool dispatch
# ═════════════════════════════════════════════════════════════════════════════


async def _dispatch(name: str, tool_input: dict) -> str:
    match name:
        case "fetch_url_metadata":
            result = await _tool_fetch_metadata(tool_input["url"])
        case "extract_article_content":
            result = await _tool_extract_content(tool_input["url"])
        case "get_tags":
            result = _tool_get_tags()
        case "create_tag":
            result = _tool_create_tag(tool_input["slug"], tool_input["name"], tool_input["description"])
        case "save_bookmark":
            result = _tool_save_bookmark(
                item_id=tool_input["item_id"],
                title=tool_input.get("title"),
                description=tool_input.get("description"),
                content=tool_input.get("content"),
                summary=tool_input["summary"],
                tags=tool_input.get("tags", []),
                image_url=tool_input.get("image_url"),
                site_name=tool_input.get("site_name"),
                word_count=tool_input.get("word_count", 0),
            )
        case _:
            result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result, default=str)


# ═════════════════════════════════════════════════════════════════════════════
# Claude tool definitions
# ═════════════════════════════════════════════════════════════════════════════


TOOLS: list[dict] = [
    {
        "name": "fetch_url_metadata",
        "description": (
            "Fetch a URL and extract Open Graph metadata "
            "(title, description, image_url, site_name)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "extract_article_content",
        "description": (
            "Extract the full article body from a URL using trafilatura. "
            "Returns text and word_count. Call after fetch_url_metadata."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "get_tags",
        "description": (
            "Return the full tag taxonomy (slug, name, description, usage_count). "
            "Always call this before deciding whether to create a new tag, "
            "to avoid creating near-duplicate tags."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_tag",
        "description": (
            "Create a new tag in the taxonomy. "
            "Only call this when no existing tag adequately covers the topic. "
            "Write a description that clearly scopes the tag to prevent future duplicates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "kebab-case, e.g. 'machine-learning'"},
                "name": {"type": "string", "description": "Human label, e.g. 'Machine Learning'"},
                "description": {"type": "string", "description": "Scope description for the LLM taxonomy"},
            },
            "required": ["slug", "name", "description"],
        },
    },
    {
        "name": "save_bookmark",
        "description": (
            "Save the fully processed bookmark. Call this last. "
            "Write a 2-3 sentence summary capturing what the page is about."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "DynamoDB item ID (provided in the task)"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "content": {"type": "string", "description": "Extracted article text"},
                "summary": {"type": "string", "description": "2-3 sentence summary you write"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tag slugs (1-5 tags)",
                },
                "image_url": {"type": "string"},
                "site_name": {"type": "string"},
                "word_count": {"type": "integer"},
            },
            "required": ["item_id", "summary", "tags"],
        },
    },
]


# ═════════════════════════════════════════════════════════════════════════════
# Agent loop
# ═════════════════════════════════════════════════════════════════════════════


async def process_link(item_id: str, url: str) -> None:
    """Run the Claude agent to fully process a single link."""
    logger.info("Starting agent for item=%s url=%s", item_id, url)

    # Claim the item — prevents duplicate processing if Lambda retries
    try:
        LINKS_TABLE.update_item(
            Key={"id": item_id},
            UpdateExpression="SET #st = :proc",
            ConditionExpression="#st = :pending",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":proc": "PROCESSING", ":pending": "PENDING"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("Item %s already claimed, skipping", item_id)
            return
        raise

    try:
        messages = [
            {
                "role": "user",
                "content": (
                    f"Process this link and save it to the knowledge base.\n\n"
                    f"URL: {url}\n"
                    f"Item ID: {item_id}\n\n"
                    "Steps:\n"
                    "1. fetch_url_metadata(url) — get title, description, image\n"
                    "2. extract_article_content(url) — get full text\n"
                    "3. get_tags() — read the existing taxonomy\n"
                    "4. Reuse existing tags where possible; call create_tag only for genuinely new topics\n"
                    "5. save_bookmark(...) — persist everything with a 2-3 sentence summary\n"
                ),
            }
        ]

        # Agentic loop — runs until Claude calls save_bookmark and returns end_turn
        for turn in range(10):  # hard cap
            response = claude.messages.create(
                model="claude-opus-4-6",
                max_tokens=4096,
                thinking={"type": "adaptive"},
                tools=TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                logger.info("Agent finished item=%s in %d turn(s)", item_id, turn + 1)
                break

            if response.stop_reason != "tool_use":
                logger.warning("Unexpected stop_reason=%s for item=%s", response.stop_reason, item_id)
                break

            # Execute all tool calls in parallel
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            tool_results = await asyncio.gather(*[_dispatch(b.name, b.input) for b in tool_blocks])

            messages.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "content": result}
                    for b, result in zip(tool_blocks, tool_results)
                ],
            })

    except Exception as exc:
        logger.error("Agent failed for item=%s: %s", item_id, exc, exc_info=True)
        LINKS_TABLE.update_item(
            Key={"id": item_id},
            UpdateExpression="SET #st = :err, error_message = :msg",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":err": "ERROR", ":msg": str(exc)},
        )
        raise


# ═════════════════════════════════════════════════════════════════════════════
# Lambda entry point
# ═════════════════════════════════════════════════════════════════════════════


async def _handle_records(records: list[dict]) -> int:
    tasks = []
    for record in records:
        if record.get("eventName") != "INSERT":
            continue
        new_image = record.get("dynamodb", {}).get("NewImage", {})
        item_id = new_image.get("id", {}).get("S")
        url = new_image.get("url", {}).get("S")
        status = new_image.get("status", {}).get("S", "")
        if not item_id or not url or status != "PENDING":
            continue
        tasks.append(process_link(item_id, url))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        for err in errors:
            logger.error("Link processing error: %s", err)
        if errors:
            raise RuntimeError(f"{len(errors)} link(s) failed — see logs")

    return len(tasks)


def handler(event: dict, context) -> dict:
    """Lambda entry point triggered by DynamoDB Streams."""
    records = event.get("Records", [])
    logger.info("Received %d stream record(s)", len(records))
    processed = asyncio.run(_handle_records(records))
    return {"statusCode": 200, "processed": processed}
