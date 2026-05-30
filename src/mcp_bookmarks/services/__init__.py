"""Application services — handler orchestration extracted from server.py/api.py.

Each module owns a single responsibility (quota, bookmarks, taxonomy, search,
embeddings, billing). Handlers are thin parse-and-serialize layers; services
encapsulate the BookmarkBackend calls + capability gates + side effects.

Phase 2 of WDN-393 (OSS-3); see docs/architecture.md.
"""
