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

## Prerequisites
1. Build + push the semantic image:
   `podman build -f Containerfile.knowledge -t ghcr.io/kayaman/mcp-bookmarks-knowledge:latest . && podman push …`
2. `cp terraform.tfvars.example terraform.tfvars` and set `owner_user_id` (+ Cognito ids).

## Phase B — stage & validate (no DNS change)
Leave `mcp_hostname` blank; validate on the free `*.cloudfront.net` domain.
```
terraform init
terraform apply            # mcp_hostname = ""
```
Then, with a real `bm_v1_` token minted at `blogmarks.dev/settings`, confirm:
`initialize` → `tools/list` → `search_bookmarks` → `semantic_search_bookmarks`,
and the scope gate (an `mcpExposed=false` row must not appear; a `tags`-scoped
token sees only allow-listed tags). The `read-mcp` Lambda keeps serving
`mcp.blogmarks.dev` untouched during this phase.

## Phase C — cutover (takes over mcp.blogmarks.dev)
Set `mcp_hostname = "mcp.blogmarks.dev"` and `route53_zone_id` to the
`blogmarks.dev` zone, then `terraform apply`. The alias record uses
`allow_overwrite = true`, so it takes over the manually-created record that
currently points at the Lambda's CloudFront.

**Rollback:** repoint `mcp.blogmarks.dev` back to the Lambda CloudFront (keep the
Lambda warm). The Lambda continues to mint/manage tokens at
`api.blogmarks.dev/mcp/*` throughout — only the JSON-RPC front door moves.

## Decommission the idle RDS (separate, gated)
The idle pgvector RDS in `terraform/rds.tf` (main stack) is superseded by the
in-process index and should be destroyed **after** cutover is validated. That is
a destructive apply on the *main* stack, done deliberately — it is intentionally
NOT part of this module. Remove `rds.tf` (and its references) and
`terraform apply` the main stack, or `terraform destroy -target` the RDS
resources.
