# Live tests

These tests hit real external services and are **opt-in**.

The default `pytest` run skips this directory (see `[tool.pytest.ini_options]`
in `pyproject.toml`: `addopts = "-m 'not live'"`).

To run live tests:

```bash
uv run pytest -m live                # all live tests
uv run pytest tests/live/test_api_live.py -m live    # one file
```

CI runs them only on a manual workflow_dispatch trigger (see
`.github/workflows/ci.yml`, job `live`).

## What lives here

| File | Why it's live |
|---|---|
| `test_scraper_live.py` | Real HTTP fetch against a stable public URL |
| `test_api_live.py`     | Subprocess server + `POST /api/save` against real URLs |
| `test_e2e_sse_live.py` | Subprocess server + MCP SSE flow + real URL fetch |

## Adding a new live test

1. Put the file under `tests/live/`.
2. Mark with `pytestmark = [pytest.mark.live, pytest.mark.asyncio]` (or just
   `pytest.mark.live` if synchronous).
3. Document the external service it needs and how to bypass / mock for offline
   work.
