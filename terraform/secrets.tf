# Secrets Manager entry for MCP_API_KEYS.
# Format: comma-separated bearer tokens, optionally with tenant suffix: "key:org-id".
# The ECS task reads this secret at startup via the secrets block in the container definition.

resource "aws_secretsmanager_secret" "mcp_api_keys" {
  name                    = "${local.prefix}-mcp-api-keys"
  description             = "MCP_API_KEYS for mcp-bookmarks: comma-separated bearer tokens (key or key:org-id)."
  recovery_window_in_days = 0

  tags = merge(local.extra_tags, {
    Component = "secrets"
  })
}

resource "aws_secretsmanager_secret_version" "mcp_api_keys" {
  secret_id     = aws_secretsmanager_secret.mcp_api_keys.id
  secret_string = var.mcp_api_keys
}

# Allow the ECS task execution role to read this secret
resource "aws_iam_role_policy" "ecs_read_mcp_api_keys" {
  name = "${local.prefix}-ecs-read-mcp-api-keys"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.mcp_api_keys.arn]
    }]
  })
}
