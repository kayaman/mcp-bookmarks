"""Domain models for the bookmark knowledge base."""

from datetime import datetime
from pydantic import BaseModel, Field


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
    """A saved bookmark with its metadata and tags."""

    id: int | None = None
    dynamo_id: str | None = Field(
        default=None,
        description="DynamoDB partition key (UUID) when using DYNAMODB_MODE",
    )
    url: str
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    site_name: str | None = None
    summary: str | None = None
    content: str | None = Field(default=None, description="Full extracted article text")
    word_count: int | None = None
    tags: list[str] = Field(default_factory=list, description="List of tag slugs")
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
