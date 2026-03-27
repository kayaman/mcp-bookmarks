# Disposable infrastructure runbook

## Preconditions

- **Remote state (recommended):** add a `backend "s3" {}` block to `terraform/` (or a root module) pointing at a **dedicated** state bucket and lock table (e.g. DynamoDB `terraform-locks`). The state bucket must **not** be destroyed with the app stack. Example:

```hcl
terraform {
  backend "s3" {
    bucket         = "your-org-terraform-state"
    key            = "mcp-bookmarks/prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
```

- No production users you care about, or run **data export** first (DynamoDB to S3, RDS snapshot).

## Destroy stack

```bash
cd terraform
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

## Greenfield apply

```bash
terraform init
terraform apply
```

## After first real users

- Enable **RDS automated backups** / snapshots before any destroy.
- Export **DynamoDB** tables or enable PITR; document RPO/RTO.
- Never `terraform destroy` production without approval and backups.

## Lambda package

```bash
./terraform/scripts/package-lambda.sh
```

Then `terraform apply` when `enable_lambda_processor` is true. Align Lambda item attributes with the **blogmarks** PWA schema (`aiContent`, etc.) if pointing at production tables.
