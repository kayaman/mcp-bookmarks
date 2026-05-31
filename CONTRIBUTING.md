# Contributing

Thanks for considering a contribution. Most of this file is operational:
how to get a working dev env, how to run checks, how to propose a
change. For deeper rationale on the codebase's shape, see
[`docs/architecture.md`](docs/architecture.md) and the ADRs in
[`docs/adr/`](docs/adr/).

## Five commands to a working dev env

```bash
# 1. Clone
git clone git@github.com:kayaman/mcp-bookmarks.git
cd mcp-bookmarks

# 2. Install (uv + ruff + mypy + pytest)
make install

# 3. Verify all CI gates locally
make ci

# 4. Start the server (Ctrl-C to stop)
make dev

# 5. Smoke-check it
make smoke
```

`make help` lists every target.

## CI gates

Every PR runs four gates; each has a local-equivalent target so you
shouldn't need a round trip to GitHub to know your change is clean:

| Gate | Local | What it checks |
|---|---|---|
| Lint | `make lint` | Ruff: `E`, `F`, `I`, `B`, `UP`, `SIM`, `RUF` codes |
| Format | `make format-check` | Ruff format (`make format` applies it) |
| Type-check | `make typecheck` | mypy on `src/mcp_bookmarks/` — pragmatic config (not strict) |
| Tests | `make test` | pytest unit + integration; live tests are opt-in; coverage gated at 85% via `pytest-cov` |

Pre-commit hooks (Ruff + mypy on changed files only) come from
`.pre-commit-config.yaml`. Install once with `make pre-commit-install`;
they fire on every `git commit` after that.

## Test coverage

Coverage is measured by `pytest-cov` on every `pytest` run; the gate
fails the build when total coverage drops below **85%**
(`--cov-fail-under=85` in `pyproject.toml`). Current baseline is ~90%.

```bash
make test       # gate-enforced, terminal report
make coverage   # full report + HTML at htmlcov/index.html
```

The floor is a ratchet: do not lower it. When you add tests that lift
the total, the next PR effectively raises the floor by virtue of not
regressing.

DynamoDB tests use [`moto`](https://docs.getmoto.org/)'s in-memory
mock (`from moto import mock_aws`). Don't reach for the real AWS SDK
in tests — instantiate `boto3` *inside* a `with mock_aws():` block so
the call lands on the mock. See `tests/integration/test_dynamodb_moto.py`
for the canonical pattern.

### What we chose not to test (and why)

These modules are deliberately under-covered — listed here so future
contributors don't re-investigate without context:

- **`server.py`** is excluded from coverage measurement entirely. It is
  exercised by `tests/integration/test_transports.py`, which spawns
  uvicorn as a **subprocess** — `coverage.py` can't track child
  processes without a `sitecustomize.py` + `COVERAGE_PROCESS_START`
  setup. That refactor would let us re-include server.py honestly; in
  the meantime, omitting keeps the measured % truthful.
- **`llm_ensemble.py`** (~32%) — retry/failover branches need contrived
  failure scenarios; cost > value until we see real ensemble bugs. The
  `run_ensemble_with_judge` entry point IS exercised end-to-end via the
  `/api/ensemble` route test (with a fake implementation).
- **`api.py` `ai_gateway_page` + `bookmarklet_page` + `static_font_jetbrains_mono`**
  — mostly HTML/binary rendering where E2E is more valuable than unit
  tests.
- **`bearer_auth.py` live Cognito JWKS HTTP roundtrip** (`PyJWKClient.get_signing_key_from_jwt`)
  + **`dynamodb.py` `_item_org_visible` per-request paths** + **boto3 retry/throttling
  paths** — all need real-AWS integration; deferred to a separate
  initiative.

If you're adding code in one of these areas, adding tests at the same
time is *encouraged* but not required by the gate.

## Proposing a change

1. **One PR per logical change.** "Refactor X" and "add feature Y in
   that refactor" are two PRs; the diff and the review thread both
   benefit.
2. **Add or update tests.** New behavior → new test. New backend
   capability flag → drift-guard test. Bug fix → regression test.
3. **Update `CHANGELOG.md`'s `## Unreleased` section.** One bullet
   summarizing the change. The next release will roll those bullets
   into a version section (see [`RELEASING.md`](RELEASING.md)).
4. **Cross-link decisions.** If the PR makes an architectural choice
   that wasn't already implicit, add an ADR (`docs/adr/`) using
   [`docs/adr/0000-template.md`](docs/adr/0000-template.md). If it
   touches an existing decision, link the relevant ADR from the PR
   description.
5. **Run `make ci` before opening the PR.** Saves a CI cycle.

## Branch + commit conventions

- Branch from `main`. Branch name: `m/<wdn-NNN>-<kebab-title>` for
  Linear-tracked work; `m/<kebab-title>` for repo-local work.
- Commit messages: imperative subject ("add ADR for tenancy", not
  "added"), keep the subject under 70 chars, wrap the body at 72.
  Reference the WDN ticket in the body when relevant.
- Squash on merge — every commit on `main` should correspond to a
  reviewed PR.

## Reporting issues

Bugs and feature requests: open a Linear ticket in the
**mcp-bookmarks OSS** project, or a GitHub issue if you don't have
Linear access. Include reproduction steps, expected vs actual
behavior, and the relevant correlation id from
`X-Correlation-ID` if the bug is server-side (see
[`docs/runbook.md` § Structured logs](docs/runbook.md#structured-logs)
for how to grep logs by id).

## Things that aren't conventions yet

- **Strict mypy.** Today we run pragmatic mypy (`check_untyped_defs`
  on, `strict` off). Tightening per-module is a follow-up; happy to
  take PRs that add typed annotations + enable strict overrides for
  individual modules without breaking the gate.
- **Coverage thresholds.** No minimum coverage gate. The integration
  suite covers the protocol boundaries; gut feel is the bar.

## Reference

- [`README.md`](README.md) — what the project is and how it's
  deployed
- [`docs/architecture.md`](docs/architecture.md) — layered shape
  and capability matrix
- [`docs/adr/`](docs/adr/) — every architecturally meaningful
  decision the codebase has made
- [`docs/runbook.md`](docs/runbook.md) — operational verification
  recipes
- [`RELEASING.md`](RELEASING.md) — what happens when a maintainer
  cuts a version
