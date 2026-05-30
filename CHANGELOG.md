# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## v1.0.1 — 2026-05-30

Patch release: fixes a `KeyError` in `services.taxonomy.create` when any
structured-log handler is configured, plus a large test-coverage push
(measured coverage 57% → 79%) and a 60% CI floor.

### Added

- **Test coverage push round 2.** Lifts measured coverage from 57% → 79%
  via 109 new tests across 8 new test files (`test_services_taxonomy`,
  `test_services_billing`, `test_services_quota`, `test_services_usage`,
  `test_auth`, `test_scraper`, `test_usage_meter`, plus
  `test_api_rest` integration suite covering 6 REST routes and
  `test_dynamodb_moto` integration suite covering DynamoDB CRUD + paged
  search via moto). Also extends `test_stripe_util` and
  `test_subscription_store` with their previously uncovered branches.
- **CI coverage floor raised from 50% → 60%** (`--cov-fail-under=60`).
- **`moto[dynamodb]>=5.0` dev dependency** for in-process DynamoDB testing.

### Fixed

- `services.taxonomy.create` no longer raises `KeyError` when logging
  `tag_created` (renamed the `name` key in `extra={}` to `tag_name`;
  `name` is a reserved `LogRecord` attribute).

## v1.0.0 — 2026-05-30

This release closes the OSS-numbered backlog (WDN-391..401): standalone
open-source narrative, dual-mode storage with capability flags, REST
envelope, structured logging, seven ADRs, hardened Terraform, Lighthouse
100/100/100/100, services extraction, release engineering, and a
portfolio tour doc.

### Added

- **OSS roadmap PRs (WDN-391 → WDN-401).** Standalone open-source narrative,
  README rewrite, deterministic test layout (unit/integration/live split),
  BookmarkBackend protocol + capability flags, REST envelope + Pydantic
  request validation, Terraform IAM scoping + alarms + infra docs,
  capability enforcement + `/api/capabilities`, structured logging +
  correlation IDs + `/ready`, ADR-0001..0007, single-page go-live
  walkthrough, ruff + mypy + Makefile + pre-commit + CONTRIBUTING +
  RELEASING.
- **`docs/architecture.md`** layered diagram, capability matrix, layer
  boundaries.
- **`docs/api.md`** REST contract documentation: error envelope, error-code
  taxonomy, per-endpoint reference, rate-limiting details, auth + tenant
  resolution.
- **`docs/runbook.md`** operational runbook: `/health` vs `/ready`,
  canonical event names, startup / quota / Stripe verification recipes,
  backend failure handling, deploy + rollback.
- **`docs/infra.md`** cloud topology: trust boundaries, failure-domain
  matrix, RPO/RTO targets, monitoring + alerting thresholds, scaling
  path, environment separation, IAM scoping.
- **`docs/go-live.md`** single-page operator walkthrough for the first
  AWS deployment (tfvars, ECR image, ALB + ACM, ECS, smoke test).
- **`docs/demo/portfolio-tour.md`** 5-minute reviewer walkthrough:
  reading order, seven engineering-depth highlights with file:line
  evidence, end-to-end MCP transcript (save → extract → tag → semantic
  search), and measured benchmarks (cold start 614ms; `/health` p50
  1.45ms; `/ready` p50 3.22ms).
- **`docs/adr/`** seven Architecture Decision Records (0001..0007)
  covering dual-mode storage, single-app transports, quota design,
  capability flags, vector roadmap, deploy boundary, multi-tenancy.
- **`/api/capabilities`** REST endpoint reporting the active backend's
  capability flags (`semantic_search`, `paged_search`,
  `integer_bookmark_ids`, `usage_metering`, `subscription_storage`).
- **`/ready`** readiness endpoint suitable for ALB target health checks
  (returns 503 with structured `reason` on backend failure).
- **Self-hosted JetBrains Mono** font under `/static/jetbrains-mono.woff2`
  with long-cache `immutable` headers; drops the render-blocking Google
  Fonts dependency on `/ai-gateway`.
- **`SecurityHeadersMiddleware`** — CSP (with SHA-256 hash for the
  `/ai-gateway` inline script), `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy`.
- **`CorrelationMiddleware`** — propagates `X-Correlation-ID` through a
  contextvar so every log record under a request carries the same id.
- **`GZipMiddleware`** outermost in the stack; `text/event-stream`
  auto-skipped so SSE keeps streaming.
- **Ruff + mypy + CI gates** — `make ci` runs every gate locally;
  `.pre-commit-config.yaml` fires on changed files.
- **`Makefile`** with `install`, `test`, `lint`, `format`, `typecheck`,
  `ci`, `dev`, `smoke`, `clean`, `pre-commit-install` targets.
- **`CONTRIBUTING.md`** and **`RELEASING.md`** documenting the
  contributor flow and release checklist.
- **Sample enrichment Lambda** marked `enable_lambda_processor=false`
  by default with a `⚠️ SAMPLE / TEMPLATE` notice in
  `lambda/handler.py` (ADR-0006).

### Changed

- **README.md** rewritten around a single product line ("bookmark
  intelligence platform with MCP + REST + dual-backend cloud
  architecture"), with Mermaid architecture and deployment diagrams,
  Production-ready vs experimental table, and reference tables in
  collapsed `<details>`.
- **REST error responses** now use a single shape:
  `{"error": {"code": "...", "message": "...", "details": {...}}}` with
  a stable `StrEnum` of codes.
- **`/api/usage`** gates on the backend's `usage_metering` capability
  and returns a structured `forbidden` envelope when unavailable.
- **`BookmarkBackend`** protocol replaces implicit duck typing; both
  `Database` and `DynamoDBDatabase` declare a `capabilities` attribute.
- **CI** (`/.github/workflows/ci.yml`) gains lint + type-check jobs;
  test job now runs `tests/unit` and `tests/integration` explicitly.
- **`lambda.tf`** logs IAM policy scoped to the specific log group
  ARN (was `Resource = "arn:aws:logs:*:*:*"`).
- **GitHub repo description + topics** aligned with the README's first
  screen.

### Fixed

- **Lighthouse scores on `/bookmarklet` and `/ai-gateway`** — both
  pages now hit 100/100/100/100 on Accessibility, Best Practices, SEO,
  and Agentic Browsing; Performance metrics LCP < 100ms, CLS ≤ 0.03.
- **`GET /ready`** added so ALB target health checks drain a task when
  the backend is unreachable (per ADR-0002).

### Documentation

- **ADR-0001..0007** in `docs/adr/` with consistent template.
- **Cross-references** between docs/architecture.md, docs/api.md,
  docs/infra.md, docs/runbook.md, docs/go-live.md, and the ADRs.
- **Capability matrix** in docs/architecture.md.

## [0.8.0] - 2026-04-18

### Added

- **Streamable HTTP transport** (`/mcp`) mounted alongside the existing SSE transport (`/sse`), enabling ChatGPT custom connectors and any HTTP-native MCP client to connect to the same server.
- `tests/test_transports.py`: smoke tests for both SSE and Streamable HTTP transports and the REST `/api/stats` endpoint.
- **Terraform — HTTPS infra** (`terraform/acm.tf`, updates to `alb.tf`, `variables.tf`, `outputs.tf`):
  - ACM certificate for `var.mcp_hostname` with Route 53 DNS validation.
  - ALB HTTPS :443 listener with TLS 1.3 policy; HTTP :80 redirects to HTTPS.
  - ALB `idle_timeout = 300s` to keep SSE long-polls alive.
  - `mcp_public_url` output (`https://<var.mcp_hostname>`).
- **Terraform — auth** (`terraform/secrets.tf`): Secrets Manager entry for `MCP_API_KEYS`; injected into ECS task via the `secrets` block.
- `docs/demo/` — copy-pasteable connection guides for Claude Code CLI, Cursor IDE, and ChatGPT custom connector; shared 5-step demo flow; operator infra-apply runbook.
- `scripts/capture_demo.py` — automated 5-step demo flow script (local or production).
- Presentation (`presentation/slides.md`): "Live on production", "Demo flow", and three per-client slides; updated "Try it" slide with production endpoint.
- `docs/production-smoke.md`: HTTPS auth gate, SSE/streamable checks, and end-to-end DynamoDB validation commands.

### Changed

- `README.md` "Connecting Clients" section: documents both transports with a comparison table; adds Cursor and ChatGPT connector snippets.
- `terraform/terraform.tfvars.example`: documents new `enable_alb`, `mcp_hostname`, `route53_zone_id`, `mcp_api_keys` variables.
- Terraform formatting pass (`terraform fmt`).

## [0.7.0] - 2026-04-02

### Added

- Slidev presentation under `presentation/` (product story, architecture, positioning).
- Documentation: `docs/product-positioning.md`, `docs/production-readiness.md`, `docs/dynamodb-rag-design.md`.

### Changed

- README: product direction section, project structure updates, semantic search note for DynamoDB.

### Removed

- Untracked scratch file `dev.py`.

[0.7.0]: https://github.com/kayaman/mcp-bookmarks/compare/v0.6.0...v0.7.0
