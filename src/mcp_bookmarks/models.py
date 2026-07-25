"""Domain models for the bookmark knowledge base."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OGMetadata(BaseModel):
    """Open Graph metadata extracted from a URL."""

    url: str
    title: str | None = None
    description: str | None = None
    image: str | None = None
    site_name: str | None = None
    og_type: str | None = None
    locale: str | None = None
    author: str | None = None


class Tag(BaseModel):
    """A canonical tag in the knowledge base.

    Tags are meant to be reused across bookmarks — the LLM decides
    whether to pick existing tags or propose new ones.
    """

    id: int | None = None
    slug: str = Field(..., description="Normalized identifier, e.g. 'machine-learning'")
    name: str = Field(..., description="Human-readable label, e.g. 'Machine Learning'")
    description: str = Field(
        default="",
        description="Scope of this tag so the LLM can decide when to reuse it",
    )
    usage_count: int = Field(default=0, description="How many bookmarks use this tag")
    created_at: datetime | None = None


class ArticleContent(BaseModel):
    """Full extracted article text from a URL."""

    url: str
    text: str
    word_count: int
    extraction_method: str = "trafilatura"


class Bookmark(BaseModel):
    """A saved bookmark with its metadata and tags.

    The canonical wire shape is camelCase (``ogTitle``, ``ogDescription``,
    ``ogImage``, ``ogSiteName``, ``aiSummary``, ``aiTags``, ``aiContent``,
    ``aiWordCount``, ``bookmarkType``, ``savedAt``, ...). The legacy snake_case
    fields (``description``, ``image_url``, ``site_name``, ``summary``,
    ``content``, ``word_count``) stay in place for back-compat with the
    bookmarklet, Claude, Cursor, and existing REST clients.

    On serialization we emit BOTH names (snake_case from the field declarations
    + camelCase via ``Field(alias=...)``); ``model_dump(by_alias=True)`` yields
    camelCase, ``model_dump()`` yields snake_case. ``populate_by_name=True``
    lets ``_to_bookmark`` build the model from either DDB key.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None
    dynamo_id: str | None = Field(
        default=None,
        description="DynamoDB partition key (UUID) when using DYNAMODB_MODE",
    )
    url: str
    title: str | None = None

    # ── snake_case fields (back-compat with existing REST/MCP clients) ──
    description: str | None = None
    image_url: str | None = None
    site_name: str | None = None
    summary: str | None = None
    content: str | None = Field(default=None, description="Full extracted article text")
    word_count: int | None = None
    tags: list[str] = Field(default_factory=list, description="List of tag slugs")
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # ── camelCase aliases (canonical wire shape) ────────────────────────
    og_title: str | None = Field(default=None, alias="ogTitle")
    og_description: str | None = Field(default=None, alias="ogDescription")
    og_image: str | None = Field(default=None, alias="ogImage")
    og_site_name: str | None = Field(default=None, alias="ogSiteName")
    ai_summary: str | None = Field(default=None, alias="aiSummary")
    ai_tags: list[str] = Field(default_factory=list, alias="aiTags")
    ai_image: str | None = Field(default=None, alias="aiImage")
    ai_content: str | None = Field(default=None, alias="aiContent")
    ai_word_count: int = Field(default=0, alias="aiWordCount")
    ai_status: str | None = Field(default=None, alias="aiStatus")
    ai_error: str | None = Field(default=None, alias="aiError")
    bookmark_type: str | None = Field(default=None, alias="bookmarkType")
    bookmark_type_confidence: float | None = Field(default=None, alias="bookmarkTypeConfidence")
    original_url: str | None = Field(default=None, alias="originalUrl")
    saved_at: str | None = Field(default=None, alias="savedAt")
    source: str | None = None

    # ── YouTube deep-extraction (blogmarks async-workers) ───────────────
    deep_text: str | None = Field(
        default=None, alias="deepText", description="Fuller transcript text (deep extract)"
    )
    ai_chapters: list[Any] | None = Field(
        default=None, alias="aiChapters", description="Timestamped chapters"
    )
    ai_claims: Any | None = Field(
        default=None, alias="aiClaims", description="Claims / recommendations / resources"
    )
    entities: Any | None = Field(default=None, description="Extracted typed entities")

    # ── ownership / share metadata ──────────────────────────────────────
    notes: str | None = None
    mcp_exposed: bool | None = Field(default=None, alias="mcpExposed")
    visibility: str | None = None
    share_token: str | None = Field(default=None, alias="shareToken")
    scraping_status: str | None = Field(default=None, alias="scrapingStatus")
    scraped_at: str | None = Field(default=None, alias="scrapedAt")
    ai_processed_at: str | None = Field(default=None, alias="aiProcessedAt")
    ai_enrichment_attempts: int | None = Field(default=None, alias="aiEnrichmentAttempts")


class BookmarkCreateResult(BaseModel):
    """Result returned after saving a bookmark."""

    bookmark: Bookmark
    og_metadata: OGMetadata
    message: str = "Bookmark saved. Use get_tags to see existing tags before tagging."


class Tenant(BaseModel):
    """Per-request tenant context resolved from API key or JWT.

    In single-tenant / personal mode, organization_id defaults to ``"default"``.
    """

    organization_id: str
    user_id: str | None = None
    scopes: list[str] = []
