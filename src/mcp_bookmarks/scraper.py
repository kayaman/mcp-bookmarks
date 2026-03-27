"""Extract Open Graph metadata and article content from URLs."""

import httpx
from bs4 import BeautifulSoup

from .models import OGMetadata, ArticleContent

# Common browser User-Agent to avoid blocks
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
TIMEOUT = 15.0


async def fetch_html(url: str) -> str:
    """Fetch raw HTML from a URL."""
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=TIMEOUT, headers=headers
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    return response.text


async def extract_og_metadata(url: str) -> OGMetadata:
    """Fetch a URL and extract Open Graph meta tags.

    Falls back to standard HTML <title> and <meta name="description">
    when og: tags are missing.
    """
    html = await fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    def og(prop: str) -> str | None:
        tag = soup.find("meta", property=f"og:{prop}")
        if tag and tag.get("content"):
            return tag["content"].strip()
        return None

    def meta(name: str) -> str | None:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
        return None

    title = og("title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    description = og("description") or meta("description")

    return OGMetadata(
        url=url,
        title=title,
        description=description,
        image=og("image"),
        site_name=og("site_name"),
        og_type=og("type"),
        locale=og("locale"),
        author=meta("author") or og("article:author"),
    )


async def extract_article_content(url: str) -> ArticleContent:
    """Extract the main article text from a URL using trafilatura.

    Trafilatura is excellent at stripping nav, ads, footers and
    extracting just the article body — much better than raw BS4.
    Falls back to BeautifulSoup paragraph extraction if trafilatura
    returns nothing.
    """
    import trafilatura

    html = await fetch_html(url)

    # Primary: trafilatura — the gold standard for article extraction
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    method = "trafilatura"

    # Fallback: BeautifulSoup paragraph extraction
    if not text or len(text.strip()) < 100:
        soup = BeautifulSoup(html, "html.parser")
        # Remove noise elements
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 30]
        text = "\n\n".join(paragraphs)
        method = "beautifulsoup-fallback"

    text = text or ""
    word_count = len(text.split())

    return ArticleContent(
        url=url,
        text=text,
        word_count=word_count,
        extraction_method=method,
    )
