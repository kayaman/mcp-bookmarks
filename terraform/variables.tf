variable "aws_region" {
  description = "AWS region (e.g. us-east-1)."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Logical prefix and Project tag."
  type        = string
  default     = "blogmarks"
}

variable "environment" {
  description = "Environment tag: dev, staging, prod."
  type        = string
  default     = "prod"
}

variable "cost_center" {
  description = "CostCenter tag for Cost Explorer allocation."
  type        = string
  default     = "product-mvp"
}

variable "owner_tag" {
  description = "Owner tag (person or team)."
  type        = string
  default     = "founder"
}

variable "monthly_budget_usd" {
  description = "Monthly budget cap in USD for AWS Budgets (e.g. 300)."
  type        = string
  default     = "300"
}

variable "budget_alert_email" {
  description = "Email for SNS subscription and budget notifications (must confirm)."
  type        = string
}

variable "db_instance_class" {
  description = "RDS instance class for PostgreSQL + pgvector."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "Initial gp3 storage for RDS."
  type        = number
  default     = 20
}

variable "ecs_desired_count" {
  description = "Fargate tasks for MCP server. Use 0 until image is in ECR."
  type        = number
  default     = 0
}

variable "mcp_container_image" {
  description = "Container image for MCP server (ECR URI after push)."
  type        = string
  default     = "public.ecr.aws/docker/library/nginx:alpine"
}

variable "mcp_container_port" {
  description = "Container port exposed by mcp-bookmarks (uvicorn)."
  type        = number
  default     = 8000
}

variable "anthropic_api_key" {
  description = "Anthropic API key for Lambda processor. Prefer TF_VAR_anthropic_api_key."
  type        = string
  sensitive   = true
}

variable "enable_lambda_processor" {
  description = "If false, skip Lambda zip/archive (DynamoDB + SQS only)."
  type        = bool
  default     = true
}

variable "budget_period_start" {
  description = "Start of the monthly budget window (AWS format YYYY-MM-DD_HH:MM). Set to the 1st of the month when you begin tracking."
  type        = string
  default     = "2025-03-01_00:00"
}

variable "enable_alb" {
  description = "If true, create an internet-facing ALB with HTTPS :443 (ACM cert) and HTTP→HTTPS redirect. Requires ecs_desired_count > 0."
  type        = bool
  default     = false
}

variable "mcp_hostname" {
  description = "Public hostname for the MCP server (e.g. mcp.blogmarks.dev). Used for ACM cert and Route 53 alias."
  type        = string
  default     = "mcp.blogmarks.dev"
}

variable "route53_zone_id" {
  description = "Route 53 Hosted Zone ID for mcp_hostname. Required when enable_alb=true."
  type        = string
  default     = ""
}

variable "mcp_api_keys" {
  description = "Comma-separated bearer tokens for MCP_API_KEYS (e.g. 'key1,key2:org-id'). Stored in Secrets Manager."
  type        = string
  sensitive   = true
  default     = ""
}

variable "dynamodb_org_id" {
  description = "Optional tenant/org id written on new bookmarks (DYNAMODB_ORG_ID on ECS tasks)."
  type        = string
  default     = ""
}

variable "ai_gateway_url" {
  description = "Base URL of ai-gateway-rs for LLM ensemble calls."
  type        = string
  default     = ""
}

variable "gateway_api_key" {
  description = "API key for ai-gateway-rs. Required when ai_gateway_url is set."
  type        = string
  sensitive   = true
  default     = ""
}

# ── Cognito JWT verification (PWA traffic) ──────────────────────────

variable "cognito_user_pool_id" {
  description = "AWS Cognito User Pool ID that mints PWA ID tokens. When set together with cognito_client_id and mcp_bearer_auth=true, the BearerAuthMiddleware validates Cognito JWTs against this pool's JWKS. Empty disables Cognito auth (fall back to bm_v1_* tokens only)."
  type        = string
  default     = ""
}

variable "cognito_client_id" {
  description = "AWS Cognito User Pool App Client ID. Becomes the `aud` claim that JWTs are checked against."
  type        = string
  default     = ""
}

variable "cognito_region" {
  description = "AWS region for the Cognito User Pool (used to build the JWKS URL)."
  type        = string
  default     = "us-east-1"
}

variable "mcp_connections_table" {
  description = "DynamoDB table holding bm_v1_* scoped tokens (issued by the read-mcp Lambda's /mcp/connections). Empty disables scoped-token validation."
  type        = string
  default     = ""
}

variable "mcp_bearer_auth" {
  description = "Enable BearerAuthMiddleware on the combined app. When true, /mcp /sse /messages require a valid Cognito JWT or bm_v1_* token. Public paths (/health, /, /bookmarklet, /ai-gateway, /webhooks/stripe, /api/*) always pass through."
  type        = bool
  default     = false
}

# ── Override DynamoDB table names (use existing blogmarks app tables) ──

variable "links_table_override" {
  description = "If set, use this table name instead of the terraform-managed blogmarks-prod-links. Set to 'blogmarks-links' to share data with the blogmarks app's existing bookmark store."
  type        = string
  default     = ""
}

variable "tags_table_override" {
  description = "If set, use this table name instead of blogmarks-prod-tags. Set to 'blogmarks-tags' to share the tag taxonomy with the blogmarks app."
  type        = string
  default     = ""
}
