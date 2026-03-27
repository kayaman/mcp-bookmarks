# Multi-cloud notes (after AWS is stable)

## Principles

- Keep the **application** in containers; avoid AWS-only SDK calls in hot paths where a portable alternative exists (HTTP, S3-compatible APIs, Postgres).
- **IaC**: Terraform modules per provider (`modules/aws`, `modules/azure`, `modules/gcp`) sharing variable interfaces (`environment`, `service_name`, `tags`).

## Mapping

| Concern | AWS (current) | Azure | GCP |
|--------|---------------|-------|-----|
| Object storage | S3 | Blob Storage | GCS |
| NoSQL | DynamoDB | Cosmos DB / Table API | Firestore / Datastore |
| Serverless HTTP | API Gateway + Lambda | Functions + API Management | Cloud Functions + LB |
| Secrets | Secrets Manager | Key Vault | Secret Manager |
| Cost tags | Cost Explorer tags | Cost Management tags | Labels + BigQuery billing export |

## When to split

Only after AWS production is **boring** (monitoring, backups, on-call). Premature second-cloud duplication increases drift and cognitive load.
