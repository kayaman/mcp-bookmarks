# terraform/knowledge — Blogmarks Knowledge semantic MCP (EC2)

Stands up a single always-on EC2 box that runs the FastMCP server in **DynamoDB
mode** against the shared **blogmarks** tables and serves **semantic search over
`bookmarkType == "knowledge"` bookmarks** (in-process Bedrock-embedded hnswlib
index). Fronted by CloudFront; ingress restricted to CloudFront's origin-facing
prefix list + a shared-secret origin header. Reuses the `blogmarks-mcp-connections`
bearer tokens (validated via `tokenHash-index`).

This is the EC2 realization of the plan in
`~/.claude/plans/scalable-nibbling-beaver.md` and is governed by
blogmarks ADR-017. It deploys into account **257394450889**.

## What it creates
- EC2 `t3.small` (AL2023, public subnet + Elastic IP), IMDSv2-only, gp3 encrypted root.
- Security group: inbound `app_port` from the CloudFront origin-facing prefix list only.
- IAM instance role: DynamoDB read on `blogmarks-links` (+GSIs) / `blogmarks-tags`,
  read+update on `blogmarks-mcp-connections`, `bedrock:InvokeModel` on the Titan
  embeddings model, S3 get/put on the index snapshot object, SSM.
- CloudFront distribution (CachingDisabled, AllViewerExceptHostHeader, HTTP/2+3),
  injecting `X-Origin-Secret` on every origin request.
- Optional S3 bucket for the ANN index snapshot (warm-starts on instance replacement).
- Optional ACM cert + Route53 records when `mcp_hostname` is set.

## Status — LIVE at `mcp2.blogmarks.dev` (2026-07-24)

Deployed **side-by-side** with the `read-mcp` Lambda, which still serves
`mcp.blogmarks.dev`. Both accept the same `bm_v1_` tokens. See "Why not
`mcp.blogmarks.dev`" below — that takeover is deferred and has a hard
prerequisite.

Operator runbook: `blogmarks/docs/runbooks/knowledge-mcp-ec2.md`.
User guide: `blogmarks/docs/knowledge-mcp-guide.md`.

## Prerequisites
1. Build + push the semantic image (Docker, to ECR — the box does an ECR login
   in user-data; the `ghcr.io` variable default is stale):
   `docker build -f Containerfile.knowledge -t mcp-bookmarks-knowledge . && docker push 257394450889.dkr.ecr.us-east-1.amazonaws.com/mcp-bookmarks-knowledge:latest`
2. `cp terraform.tfvars.example terraform.tfvars` and fill it in.

## Put every override in `terraform.tfvars` — not on the CLI

Terraform does not remember `-var` flags between runs. An apply that omits one
silently reverts to the variable default, changes the rendered `user_data`, and
**replaces the instance** (`user_data_replace_on_change = true`). Two defaults
are actively wrong for this deployment:

- `container_image` defaults to `ghcr.io/...`; prod runs the ECR image.
- `type_index` defaults to `userId-type-savedAt-index` — **a GSI that has never
  existed** on `blogmarks-links` (which has only `feed-savedAt-index`,
  `userId-savedAt-index`, `rate-limit-index`). Non-empty means every index build
  throws `ValidationException`, the index never becomes ready, and
  `semantic_search_bookmarks` returns an empty "still building or disabled" hint
  forever. **Keep `type_index = ""`** to select the scan fallback in
  `query_raw_by_type`.

Always `terraform plan -out=…` and check whether `aws_instance.this` is being
replaced before applying.

## Validating

```
BM_TOKEN=bm_v1_… ../../scripts/smoke-test-knowledge-mcp.sh
```

Covers transport, auth, handshake, tool surface, keyword search and semantic
search. The scope gate still needs a manual check: an `mcpExposed=false` row must
not appear, and a `tags`-scoped token must see only allow-listed tags. Note there
are currently **zero** `knowledge` bookmarks with `mcpExposed=false`, so that gate
has no negative case in production data.

## Why not `mcp.blogmarks.dev`

ADR-017 originally proposed taking that hostname over. It is claimed by a
**CDK-managed** CloudFront distribution (`api-stack.ts:722`, `McpDistribution` →
read-mcp Lambda), and CloudFront CNAME aliases are globally exclusive. This
module's `allow_overwrite = true` overwrites only the Route53 record — it cannot
release CloudFront's alias claim, so `mcp_hostname = "mcp.blogmarks.dev"` fails
with `CNAMEAlreadyExists`.

A takeover requires, **in this order**:

1. A blogmarks PR dropping `domainNames` + `certificate` from `McpDistribution`,
   merged and CDK-deployed.
2. Then `terraform apply` with `mcp_hostname = "mcp.blogmarks.dev"`.

Do not hand-remove the alias via CLI while the CDK code still declares it — the
next `cdk deploy ApiStack` re-adds it, fails, and rolls back the whole
`BlogmarksApi` stack.

**Rollback (current side-by-side layout):** delete the `mcp2` A/AAAA records —
nothing else depends on them. This is genuinely a DNS-only rollback *because* the
takeover was not performed; it would not be after one.

## Decommission the idle RDS (separate, gated)
The idle pgvector RDS in `terraform/rds.tf` (main stack) is superseded by the
in-process index and should be destroyed **after** cutover is validated. That is
a destructive apply on the *main* stack, done deliberately — it is intentionally
NOT part of this module. Remove `rds.tf` (and its references) and
`terraform apply` the main stack, or `terraform destroy -target` the RDS
resources.
