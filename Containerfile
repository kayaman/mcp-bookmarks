# Containerfile — Podman/Docker compatible
# Build:  podman build -t mcp-bookmarks .
# Run:    podman run -p 8000:8000 -v mcp-bookmarks-data:/data mcp-bookmarks

FROM python:3.12-slim AS base

# Avoid bytecode + enable unbuffered output for logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Dependencies layer (cached unless pyproject.toml changes) ────
FROM base AS deps

COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[cli]" 2>/dev/null || \
    pip install --no-cache-dir \
        "mcp[cli]>=1.2.0" \
        "httpx>=0.27.0" \
        "beautifulsoup4>=4.12.0" \
        "aiosqlite>=0.20.0" \
        "pydantic>=2.0.0" \
        "uvicorn>=0.30.0" \
        "trafilatura>=1.12.0"

# ── Application layer ────────────────────────────────────────────
FROM deps AS app

COPY src/ ./src/
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e .

# ── Runtime config ───────────────────────────────────────────────

# Store DB in a volume-mountable path
ENV MCP_PORT=8000 \
    MCP_HOST=0.0.0.0 \
    BOOKMARKS_DB_PATH=/data/bookmarks.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx,sys; r=httpx.get('http://localhost:8000/health', timeout=3); sys.exit(0 if r.status_code==200 else 1)" || exit 1

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p /data && chown appuser:appuser /data
USER appuser

ENTRYPOINT ["python", "-m", "mcp_bookmarks"]
