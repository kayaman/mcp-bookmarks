# PWA Share Target (Android) — Blogmarks app

Implementation lives in the **[blogmarks](https://github.com/kayaman/blogmarks)** PWA repository, not in `mcp-bookmarks`.

## Requirements

- **HTTPS** and a stable origin (`blogmarks.dev`).
- **Web App Manifest** `share_target` with `action` (e.g. `/share`), `method` `POST`, `enctype` `multipart/form-data` or `application/x-www-form-urlencoded`, `params` for `url`, `title`, `text`.
- **Route handler** `/share` (or chosen path): read shared fields, require authenticated session, call existing bookmark ingest API (same pipeline as in-app save).
- **Service worker** (optional): queue shares when offline, sync when online.

## UX

- Confirmation screen: “Save to Blogmarks?”
- Show processing state; link to the new bookmark when done.

## API

Reuse authenticated `POST` used by the main app for URL ingest; do not expose unauthenticated public save.

## Testing

- Chrome Android: Share from browser → Blogmarks PWA.
- Verify DynamoDB / API logs for the new item.
