# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-04-18

### Added

- **Streamable HTTP transport** (`/mcp`) mounted alongside the existing SSE transport (`/sse`), enabling ChatGPT custom connectors and any HTTP-native MCP client to connect to the same server.
- `tests/test_transports.py`: smoke tests for both SSE and Streamable HTTP transports and the REST `/api/stats` endpoint.
- **Terraform — HTTPS infra** (`terraform/acm.tf`, updates to `alb.tf`, `variables.tf`, `outputs.tf`):
  - ACM certificate for `mcp.blogmarks.dev` with Route 53 DNS validation.
  - ALB HTTPS :443 listener with TLS 1.3 policy; HTTP :80 redirects to HTTPS.
  - ALB `idle_timeout = 300s` to keep SSE long-polls alive.
  - `mcp_public_url` output (`https://mcp.blogmarks.dev`).
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
