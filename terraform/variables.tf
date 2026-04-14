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
  description = "If true, create an internet-facing ALB :80 and register ECS tasks (requires ecs_desired_count > 0)."
  type        = bool
  default     = false
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
