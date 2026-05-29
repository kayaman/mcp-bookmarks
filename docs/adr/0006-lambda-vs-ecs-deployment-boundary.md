# ADR-0006: Lambda enrichment is a sample template; ECS is production

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`terraform/ecs.tf`](../../terraform/ecs.tf), [`terraform/lambda.tf`](../../terraform/lambda.tf), [`lambda/handler.py`](../../lambda/handler.py), [`docs/infra.md`](../infra.md), [`docs/architecture.md`](../architecture.md)

## Context

Two compute paths exist in the cloud Terraform:

1. **The MCP server itself** — long-running, stateful (DB connection
   pool, FastMCP session manager), serves SSE long-polls. Lives on
   **ECS Fargate** ([`terraform/ecs.tf`](../../terraform/ecs.tf)) behind
   an ALB with ACM TLS.
2. **An optional enrichment Lambda** — short-lived, stateless,
   triggered by the DynamoDB `links` stream on each new bookmark, runs
   an Anthropic Claude agent to extract content + tags + summary.
   Lives in [`terraform/lambda.tf`](../../terraform/lambda.tf) with the
   handler at [`lambda/handler.py`](../../lambda/handler.py).

Two production-aligned services would mean syncing the canonical
camelCase schema (`ogTitle`, `aiContent`, …) across both code bases and
keeping the Lambda's HTTP scraper, tag taxonomy reads, and Anthropic
prompt versioned alongside the main server. We chose not to do that.

## Decision

The MCP server runs on ECS. The Lambda ships as a **sample / template**,
not a production-aligned integration:

- It's gated `false` by default in
  [`terraform/terraform.tfvars.example`](../../terraform/terraform.tfvars.example)
  (`enable_lambda_processor = false`) so a fresh `terraform apply`
  doesn't deploy it.
- It uses a **simpler snake_case item schema** (`title`, `description`,
  `content`, `summary`, `tags`, `image_url`, `site_name`, `word_count`)
  for its `UpdateItem` calls — **not** the canonical camelCase wire
  shape the main app emits and reads (`ogTitle`, `aiContent`, …).
- Its module docstring carries the divergence notice
  ([`lambda/handler.py`](../../lambda/handler.py)): "if you wire this
  into a deployment that's also read by the main mcp-bookmarks server,
  either fork-and-rewrite the UpdateExpression to emit the canonical
  schema, or treat the items it writes as a separate corpus."
- The alarms in [`terraform/alarms.tf`](../../terraform/alarms.tf) for
  Lambda errors / duration / DLQ depth are wired conditionally on
  `enable_lambda_processor`.

The ECS path is the integration boundary. New enrichment work — say,
calling Anthropic for summaries — should be added to the ECS service
inside an `async def` background task or a separate ECS-hosted worker,
**not** to this Lambda.

## Consequences

- **Good:**
  - The ECS service is one canonical surface to reason about: one
    process, one schema, one auth boundary. Operators don't need to
    debug a separate Lambda's failure modes during a normal save.
  - The Lambda exists as a *worked example* of "this is how you wire
    a DynamoDB stream to a Claude agent" without committing the
    repo to maintaining it as production code.
  - Deployments that need long-running connection pools (RDS for
    pgvector — see [ADR-0005](0005-vector-search-roadmap.md)) or
    in-process MCP state work naturally on ECS without per-invocation
    cold-start cost.

- **Bad:**
  - Drive-by readers see a 420-line `lambda/handler.py` and assume
    it's part of the integration. The header comment is load-bearing
    documentation; if it's removed, the divergence becomes confusing.
  - Anyone who *does* run the Lambda with `enable_lambda_processor =
    true` against the main app's DynamoDB tables will produce items
    that don't surface cleanly through `read_bookmark` /
    `search_bookmarks` (snake_case keys instead of canonical
    camelCase). This is called out in the Lambda header and in
    [`docs/production-readiness.md`](../production-readiness.md).

- **Operational:**
  - **Default deploy: Lambda off.** `enable_lambda_processor=false`
    skips IAM role / function / event source mapping / log group /
    alarms.
  - **If you turn the Lambda on:** the alarms in `alarms.tf` start
    firing on real signal; the DLQ depth alarm in particular pages
    on un-processable batches. Confirm
    [`docs/runbook.md` § Backend failure handling](../runbook.md#backend-failure-handling)
    covers the on-call response.
  - **Cold start:** Anthropic-agent Lambda cold-starts can exceed 5s on
    first invocation per region. The `bisect_batch_on_function_error =
    true` + DLQ wiring isolates poison batches but doesn't paper over
    extended outages.

## Alternatives considered

- **Two production-aligned services with a shared schema package.**
  Considered for symmetry. Rejected because the Lambda's payload
  surface (DynamoDB stream record format) is narrow enough that the
  ergonomics of a shared Pydantic package across two repos
  (mcp-bookmarks + a deploy of the Lambda) didn't pay back the
  complexity. The Lambda would essentially shadow the main app's
  service code path; better to enrich inside ECS.
- **No Lambda at all; do enrichment in ECS only.** Considered for
  simplicity. Rejected because the DynamoDB-streams + Lambda + DLQ
  pattern is genuinely useful as a template for users who want to
  build their own pipeline against the same `links` table.
  Documentation-as-code: the Terraform file IS the documentation.
- **Step Functions orchestration instead of raw Lambda.** Considered for
  more graceful retry semantics. Rejected as overkill for the current
  template scope. If we ever promote the Lambda to production-aligned,
  Step Functions becomes a real option.

## References

- [`terraform/ecs.tf`](../../terraform/ecs.tf) — the production compute
  path (task definition, service, IAM role with table ARNs).
- [`terraform/lambda.tf`](../../terraform/lambda.tf) — the conditional
  Lambda (count on `enable_lambda_processor`); log group scoping per
  the IAM audit in
  [ADR-0007 — Multi-tenancy via API keys](0007-multi-tenancy-via-api-keys.md).
- [`lambda/handler.py`](../../lambda/handler.py) — the sample/template
  notice in the module docstring.
- [`docs/infra.md`](../infra.md) — full deployment topology and trust
  boundaries.
- [`docs/architecture.md`](../architecture.md) — application-layer
  layering (transport / services / domain / infrastructure).
