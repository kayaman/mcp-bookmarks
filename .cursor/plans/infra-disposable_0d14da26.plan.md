---
name: infra-disposable
overview: "Make infrastructure fully disposable with IaC: remote Terraform state, documented destroy/rebuild runbook, data backup policy for post-launch, and CI/CD pipeline for plan/apply."
todos:
  - id: remote-state
    content: Bootstrap S3+DynamoDB for Terraform remote state; migrate local state
    status: pending
  - id: runbook
    content: Update destroy/rebuild runbook with pre/post checklists
    status: pending
  - id: backup-policy
    content: Define pre-launch (accept loss) vs post-launch (export+snapshot) policy
    status: pending
  - id: ci-cd
    content: Create GitHub Actions workflow for terraform plan on PR, apply on merge
    status: pending
  - id: env-separation
    content: Add environment variable for staging vs prod separation
    status: pending
isProject: false
---

# Infrastructure Disposable (Destroy/Rebuild)

## Current State

- Terraform in `[terraform/](mcp-bookmarks/terraform/)` with comprehensive resource definitions
- State is **local** (`terraform.tfstate`) -- not remote
- Documentation at `[docs/infra-disposable-runbook.md](mcp-bookmarks/docs/infra-disposable-runbook.md)`
- No CI/CD pipeline for Terraform (blogmarks has deploy.yml for frontend only)
- Currently greenfield -- no production data to preserve

## Implementation

### 1. Remote state backend

Create a bootstrap Terraform config (one-time) for the state backend:

```hcl
# terraform/backend-bootstrap/main.tf
resource "aws_s3_bucket" "tfstate" {
  bucket = "mcp-bookmarks-tfstate"
  # versioning, encryption, lifecycle
}
resource "aws_dynamodb_table" "tflock" {
  name         = "mcp-bookmarks-tflock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute { name = "LockID"; type = "S" }
}
```

Then in `[terraform/providers.tf](mcp-bookmarks/terraform/providers.tf)`:

```hcl
terraform {
  backend "s3" {
    bucket         = "mcp-bookmarks-tfstate"
    key            = "mcp-bookmarks/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "mcp-bookmarks-tflock"
    encrypt        = true
  }
}
```

### 2. Destroy/rebuild runbook

Update `[docs/infra-disposable-runbook.md](mcp-bookmarks/docs/infra-disposable-runbook.md)`:

```
Pre-destroy checklist:
1. [ ] No active users (or: maintenance mode enabled)
2. [ ] DynamoDB export to S3 (if post-launch)
3. [ ] RDS snapshot (if post-launch)
4. [ ] Note current secret values (re-create after)

Destroy: terraform destroy -auto-approve
Rebuild: terraform apply -auto-approve
Post-rebuild:
1. [ ] Re-create secrets in Secrets Manager
2. [ ] Import DynamoDB data (if applicable)
3. [ ] Verify Lambda function URLs match CloudFront origins
4. [ ] Run smoke tests
```

### 3. Data backup policy (pre-launch vs post-launch)

- **Pre-launch (now):** Accept data loss. `terraform destroy` is safe.
- **Post-launch:** Before destroy:
  - DynamoDB: `aws dynamodb export-table-to-point-in-time` to S3
  - RDS: automated snapshots + manual snapshot before destroy
  - S3 static assets: versioned bucket, no loss on destroy
  - Separate "data" stack with `prevent_destroy` lifecycle

### 4. CI/CD for Terraform

GitHub Actions workflow `terraform.yml`:

```yaml
on:
  pull_request:
    paths: ['terraform/**']
  push:
    branches: [main]
    paths: ['terraform/**']

jobs:
  plan:
    if: github.event_name == 'pull_request'
    # terraform init, plan, comment on PR
  apply:
    if: github.ref == 'refs/heads/main'
    # terraform init, apply -auto-approve
```

### 5. Environment separation

- Use Terraform workspaces or separate state files for `staging` vs `prod`
- Variables: `var.environment` controls naming, sizing, and budget thresholds
- Staging can be destroyed freely; prod requires backup checklist

## Key Files

- `[terraform/providers.tf](mcp-bookmarks/terraform/providers.tf)` - add S3 backend
- `[terraform/variables.tf](mcp-bookmarks/terraform/variables.tf)` - add environment variable
- `[docs/infra-disposable-runbook.md](mcp-bookmarks/docs/infra-disposable-runbook.md)` - destroy/rebuild docs

