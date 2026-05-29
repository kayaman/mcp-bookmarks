# Go live — first production deploy

A one-page operator walkthrough for the first `terraform apply` of
mcp-bookmarks into a new AWS account. Steps are linear; do not skip
ahead.

| Section | What you do | Wait time |
|---|---|---|
| [A. Pre-flight](#a-pre-flight-checks) | Confirm tooling + credentials + domain | 5 min |
| [B. tfvars](#b-fill-in-terraformterraformtfvars) | Copy and edit one file | 5 min |
| [C. First apply](#c-first-apply--empty-stack) | Provision the empty stack | 10–15 min |
| [D. DNS validation](#d-dns-validation-completes-itself) | ACM cert validates against Route 53 | 1–5 min |
| [E. Image push](#e-build--push-the-container-image) | Build + push to ECR | 5–10 min |
| [F. Second apply](#f-second-apply--bring-up-ecs) | Bring up the ECS task | 3–5 min |
| [G. Smoke test](#g-smoke-test) | Run the verification matrix | 2 min |
| [H. Stripe (optional)](#h-stripe-wiring-optional) | Wire the billing webhook | 5 min |
| [I. Lambda (optional)](#i-optional-enable-lambda-enrichment) | Turn on the sample enrichment Lambda | 5 min |
| [J. Rollback](#j-rollback) | How to back out safely | — |
| [K. Out of scope](#k-whats-intentionally-not-covered) | What this walkthrough does not cover | — |

The walkthrough cites — and does not duplicate —
[`docs/infra.md`](infra.md), [`docs/runbook.md`](runbook.md),
[`docs/production-smoke.md`](production-smoke.md), and the seven ADRs
in [`docs/adr/`](adr/). Open each link when the relevant section asks
you to.

---

## A. Pre-flight checks

Confirm in your shell, in this order:

1. **AWS identity**:
   ```bash
   aws configure list           # right profile selected?
   aws sts get-caller-identity  # right account?
   ```
   You need PowerUser-equivalent or a custom least-privilege policy
   covering VPC, ECS, ECR, RDS, DynamoDB, ALB, ACM, Secrets Manager,
   IAM (role-creation), CloudWatch, SNS, SQS, Lambda, Budgets.

2. **Required `TF_VAR_*` env vars** (the secrets stay out of
   `terraform.tfvars`; see
   [`docs/infra.md` § Secret handling audit](infra.md#secret-handling-audit)):
   ```bash
   export TF_VAR_anthropic_api_key=sk-ant-...   # required by lambda.tf
   export TF_VAR_mcp_api_keys="devkey1,devkey2:tenant-2"  # REST auth
   # optional, set later if you want them:
   export TF_VAR_stripe_webhook_secret=whsec_...
   export TF_VAR_gateway_api_key=sk-...
   ```
   `direnv` + a gitignored `.envrc.local` is the cleanest way to keep
   these out of your shell history.

3. **Hosted zone for the chosen hostname**. If you plan to serve
   `mcp.example.com`, you already need a Route 53 hosted zone for
   `example.com` (or `mcp.example.com` itself). Note the zone id —
   it's a string like `Z0123456789ABCDEFGHIJ`.

4. **Container runtime**. `podman` is the documented choice; `docker`
   works identically. `uv` (the Python package manager) for the local
   `mcp-bookmarks` install used by the smoke test.

If any of the four are missing, fix them before continuing.

---

## B. Fill in `terraform/terraform.tfvars`

```bash
cd terraform/
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars
```

Required (no defaults):

| Key | Example value |
|---|---|
| `budget_alert_email` | `you@example.com` |
| `mcp_hostname` | `mcp.example.com` *(only when `enable_alb = true`)* |
| `route53_zone_id` | `Z0123456789ABCDEFGHIJ` *(only when `enable_alb = true`)* |

Recommended overrides for production:

| Key | Production value | Why |
|---|---|---|
| `enable_alb` | `true` | The public HTTPS endpoint lives behind the ALB + ACM cert |
| `enable_lambda_processor` | `false` | The Lambda is a sample template — see [ADR-0006](adr/0006-lambda-vs-ecs-deployment-boundary.md). Off for v1. |
| `ecs_desired_count` | `0` *(for now)* | Stays at 0 until the image is pushed in [section E](#e-build--push-the-container-image). |
| `mcp_container_image` | leave as the placeholder | The real URI gets filled in after the ECR repo exists. |

**Secrets stay in env vars**, not in `terraform.tfvars`.
`terraform.tfvars` itself is gitignored, but treating it as
secret-bearing creates one more file to be careful with. The
`TF_VAR_*` env vars from [section A](#a-pre-flight-checks) are
the contract.

---

## C. First apply — empty stack

```bash
cd terraform/

# The check "lambda_zip_present" block in lambda.tf requires the file
# to exist EVEN when enable_lambda_processor=false. One-off command:
./scripts/package-lambda.sh

terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

What you should see come up (per [`docs/infra.md` § Runtime
architecture](infra.md#runtime-architecture)):

- VPC + public/private subnets, internet gateway
- ECR repository
- RDS PostgreSQL `db.t4g.micro` in the private subnets
- DynamoDB tables (`links`, `tags`, `usage_events`, `subscriptions`)
  with **PITR enabled** — see
  [ADR-0001](adr/0001-sqlite-dynamodb-dual-mode-storage.md) for why
- Secrets Manager entries (`mcp-api-keys`, `database-url`)
- ACM cert + Route 53 validation record (validation now waits in
  the apply — see [section D](#d-dns-validation-completes-itself))
- ALB + target group
- SQS DLQ
- SNS topic `${prefix}-alerts` + email subscription **pending**
- CloudWatch alarms (`terraform/alarms.tf`)
- Budgets entries
- **No running ECS task** — `ecs_desired_count = 0`

After the apply finishes, **confirm both SNS / Budgets emails**. AWS
sends a one-time confirm-subscription link to `budget_alert_email`;
clicking it activates the alarms wire-up.

---

## D. DNS validation completes itself

Terraform inserted the ACM challenge record into your Route 53 zone in
[section C](#c-first-apply--empty-stack). The apply blocks on
`aws_acm_certificate_validation.mcp` resolving — typically 1–5
minutes. If it blocks for longer than 10 minutes, your `route53_zone_id`
likely points at the wrong zone; verify with `aws route53
list-resource-record-sets --hosted-zone-id "$route53_zone_id"` and look
for the `_acme-challenge` record.

You do not need to do anything in this section beyond waiting.

---

## E. Build + push the container image

The repo's [`Containerfile`](../Containerfile) builds against Python
3.12-slim, copies `src/` (including `src/mcp_bookmarks/_static/` for the
self-hosted JetBrains Mono woff2), and runs as a non-root user on port
8000.

```bash
cd terraform/
ECR_URI=$(terraform output -raw ecr_repository_url)
AWS_REGION=$(terraform output -raw aws_region 2>/dev/null || echo us-east-1)
cd ..

aws ecr get-login-password --region "$AWS_REGION" \
  | podman login --username AWS --password-stdin "${ECR_URI%/*}"

podman build -t mcp-bookmarks:latest .
VERSION=$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
podman tag mcp-bookmarks:latest "$ECR_URI:v$VERSION"
podman tag mcp-bookmarks:latest "$ECR_URI:latest"
podman push "$ECR_URI:v$VERSION"
podman push "$ECR_URI:latest"
```

`docker` substitutes for `podman` identically. The build is
single-stage; expect ~3 minutes on a warm cache.

---

## F. Second apply — bring up ECS

Edit `terraform/terraform.tfvars`:

```hcl
mcp_container_image = "<ECR_URI>:v<VERSION>"   # from section E
ecs_desired_count   = 1
```

Apply again:

```bash
cd terraform/
terraform plan -out=tfplan
terraform apply tfplan
```

The ECS service pulls the image, starts the task, and the ALB target
group flips healthy once `GET /ready` returns 200. The ALB health check
is intentionally `/ready` and **not** `/health` — `/health` is liveness
(process up) while `/ready` proves the backend is reachable; an outage
in the data layer must drain the task. See
[`docs/runbook.md` § Health + readiness](runbook.md#health--readiness)
and [ADR-0002](adr/0002-mcp-rest-coexistence-on-single-starlette-app.md).

If the target stays unhealthy: tail the ECS task logs (`aws logs tail
/ecs/${prefix}-task --follow`) and look for `ready_check_failed` —
the `reason` field tells you whether the backend never opened
(`open_timeout` / `open_error`) or opened but failed a ping
(`ping_error`).

---

## G. Smoke test

Set the host + key in your shell:

```bash
export HOST="$(cd terraform && terraform output -raw mcp_public_url | sed 's,^https://,,')"
export MCP_API_KEY="<first key from TF_VAR_mcp_api_keys>"
```

Then run the verification matrix from
[`docs/runbook.md` § Startup verification](runbook.md#startup-verification)
in order:

1. `GET /health` → `{"status":"ok"}`
2. `GET /ready` → `{"status":"ready"}` (proves the backend is reachable)
3. `GET /api/capabilities` (with `Authorization: Bearer $MCP_API_KEY`)
   → `{"backend":"dynamodb","capabilities":{...}}`
4. `GET /api/stats` → `{"total_bookmarks":0,"total_tags":0}` on a fresh deploy
5. SSE transport handshake — `curl -N -H 'Accept: text/event-stream'
   -H "Authorization: Bearer $MCP_API_KEY" https://$HOST/sse` →
   `event: endpoint`
6. Streamable HTTP `initialize` — the two-step `POST /mcp` recipe
   in [`docs/production-smoke.md` § Production HTTPS endpoint](production-smoke.md#production-https-endpoint)
   returns a JSON tool list

All six green → live.

---

## H. Stripe wiring (optional)

Terraform doesn't automate Stripe; the operator-side steps are:

1. In the Stripe Dashboard → **Developers → Webhooks → Add endpoint**.
   URL = `https://<mcp_hostname>/webhooks/stripe`.
2. Subscribe to `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`.
3. Copy the signing secret (`whsec_...`).
4. `export TF_VAR_stripe_webhook_secret=whsec_...` and re-apply
   ([section F](#f-second-apply--bring-up-ecs)) — the ECS task picks
   up the new env var on the next deployment.
5. Verify per [`docs/runbook.md` § Stripe verification](runbook.md#stripe-verification):
   ```bash
   stripe trigger customer.subscription.created --forward-to "https://$HOST/webhooks/stripe"
   # → 200  {"received":true,"type":"customer.subscription.created"}
   # → log:  stripe_webhook_processed{type=..., customer_id=..., plan=...}
   ```

Plan → quota mapping is **not** automated: the webhook persists
subscription state but doesn't bump `MCP_MONTHLY_USAGE_LIMIT` per
plan. See
[ADR-0003](adr/0003-quota-and-usage-metering.md#consequences)
and [`docs/production-readiness.md`](production-readiness.md) for
the seam to extend.

---

## I. Optional: enable Lambda enrichment

The Lambda is a **sample template** with a snake_case schema that
doesn't match the canonical camelCase the main app emits — see
[ADR-0006](adr/0006-lambda-vs-ecs-deployment-boundary.md) before
turning it on. If you still want to:

1. `./scripts/package-lambda.sh` (already run in
   [section C](#c-first-apply--empty-stack); re-run if you've changed
   `lambda/handler.py`).
2. Edit `terraform/terraform.tfvars`:
   `enable_lambda_processor = true`.
3. Confirm `TF_VAR_anthropic_api_key` is set ([section A](#a-pre-flight-checks)).
4. `terraform apply`.
5. Watch the `${prefix}-lambda-dlq-not-empty` CloudWatch alarm — it
   pages when batches fail past the retry budget. Documented in
   [`docs/runbook.md` § Backend failure handling](runbook.md#backend-failure-handling).

---

## J. Rollback

- **Drain traffic without destroying state**:
  ```bash
  cd terraform/
  terraform apply -var='ecs_desired_count=0'
  ```
- **Roll the image back**:
  ```bash
  terraform apply -var="mcp_container_image=<previous-ECR-tag>"
  ```
- **Stateful resources** (DynamoDB, RDS, Secrets Manager) survive a
  `terraform destroy` only if you've added
  `lifecycle { prevent_destroy = true }` to them or rely on PITR. The
  shipped Terraform does not currently mark them
  `prevent_destroy` — review before destructive operations and use
  [`docs/infra.md` § Recovery objectives (RPO / RTO)](infra.md#recovery-objectives-rpo--rto)
  to assess blast radius.
- **Full teardown** is documented in
  [`docs/infra-disposable-runbook.md`](infra-disposable-runbook.md).

---

## K. What's intentionally not covered

- **Multi-region failover.** Single-region by design; the trade-off
  is in [ADR-0001](adr/0001-sqlite-dynamodb-dual-mode-storage.md) and
  the multi-region sketch in [`docs/multicloud.md`](multicloud.md).
- **Plan → quota mapping from Stripe events.** The webhook records
  state; mapping is operator-extensible per
  [ADR-0003](adr/0003-quota-and-usage-metering.md) and
  [`docs/production-readiness.md`](production-readiness.md).
- **Container image signing / supply-chain attestation** (cosign,
  SLSA). Separate hardening pass.
- **Auto-scaling.** The ECS service runs at `desired_count` you set;
  no scaling policy is wired. Vertical bumps (task CPU/memory in
  `terraform/ecs.tf`) and horizontal bumps (raise `ecs_desired_count`
  for DynamoDB-mode, which is stateless) are documented in
  [`docs/infra.md` § Scaling path](infra.md#scaling-path).
- **Stripe product + price setup.** Manual one-time configuration in
  the Stripe Dashboard.

---

## Related documents

- [`docs/infra.md`](infra.md) — runtime topology, RPO/RTO, secret audit
- [`docs/runbook.md`](runbook.md) — health/ready/quota/Stripe verification
- [`docs/production-smoke.md`](production-smoke.md) — curl matrix
- [`docs/production-readiness.md`](production-readiness.md) — what's wired
- [`docs/architecture.md`](architecture.md) — layer boundaries, capability matrix
- [`docs/adr/`](adr/) — seven decision records the operator should
  understand before extending the stack
