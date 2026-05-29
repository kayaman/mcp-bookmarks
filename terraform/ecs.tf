resource "aws_ecs_cluster" "main" {
  name = "${local.prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.extra_tags, {
    Component = "ecs-cluster"
  })
}

resource "aws_cloudwatch_log_group" "ecs_mcp" {
  name              = "/ecs/${local.prefix}-mcp"
  retention_in_days = 14

  tags = merge(local.extra_tags, {
    Component = "ecs-logs"
  })
}

resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.prefix}-ecs-task-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = merge(local.extra_tags, {
    Component = "ecs-iam"
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_read_db_secret" {
  name = "${local.prefix}-ecs-read-db-secret"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue"
      ]
      Resource = [
        aws_secretsmanager_secret.database_url.arn
      ]
    }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name = "${local.prefix}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = merge(local.extra_tags, {
    Component = "ecs-iam"
  })
}

resource "aws_iam_role_policy" "ecs_task_dynamo" {
  name = "${local.prefix}-ecs-dynamo"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:BatchGetItem",
        "dynamodb:BatchWriteItem"
      ]
      Resource = concat([
        aws_dynamodb_table.links.arn,
        "${aws_dynamodb_table.links.arn}/index/*",
        aws_dynamodb_table.tags.arn,
        "${aws_dynamodb_table.tags.arn}/index/*",
        aws_dynamodb_table.usage_events.arn,
        "${aws_dynamodb_table.usage_events.arn}/index/*",
        aws_dynamodb_table.subscriptions.arn,
        "${aws_dynamodb_table.subscriptions.arn}/index/*",
        ],
        # mcp-connections table is owned by an upstream provisioner that
        # writes scoped tokens. BearerAuthMiddleware needs read access to
        # validate bm_v1_* tokens. Grant by name when configured.
        var.mcp_connections_table != "" ? [
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.mcp_connections_table}",
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.mcp_connections_table}/index/*",
        ] : [],
        # Override tables (e.g. existing external production tables).
        var.links_table_override != "" ? [
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.links_table_override}",
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.links_table_override}/index/*",
        ] : [],
        var.tags_table_override != "" ? [
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.tags_table_override}",
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.tags_table_override}/index/*",
        ] : [],
      )
    }]
  })
}

locals {
  ecs_container_definitions = jsonencode([{
    name      = "mcp-bookmarks"
    image     = var.mcp_container_image
    essential = true
    portMappings = [{
      containerPort = var.mcp_container_port
      hostPort      = var.mcp_container_port
      protocol      = "tcp"
    }]
    environment = [
      { name = "DYNAMODB_MODE", value = "true" },
      # When *_table_override is set, mcp-bookmarks reads/writes the
      # specified external tables instead of the terraform-managed
      # ${var.project_name}-${var.environment}-* tables. Useful for
      # sharing data with an existing external bookmark store.
      { name = "DYNAMODB_LINKS_TABLE", value = coalesce(var.links_table_override, aws_dynamodb_table.links.name) },
      { name = "DYNAMODB_TAGS_TABLE", value = coalesce(var.tags_table_override, aws_dynamodb_table.tags.name) },
      { name = "DYNAMODB_USAGE_TABLE", value = aws_dynamodb_table.usage_events.name },
      { name = "DYNAMODB_SUBSCRIPTIONS_TABLE", value = aws_dynamodb_table.subscriptions.name },
      { name = "DYNAMODB_ORG_ID", value = var.dynamodb_org_id },
      { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      { name = "MCP_HOST", value = "0.0.0.0" },
      { name = "MCP_PORT", value = tostring(var.mcp_container_port) },
      { name = "AI_GATEWAY_BASE_URL", value = var.ai_gateway_url },
      { name = "AI_GATEWAY_API_KEY", value = var.gateway_api_key },
      # ── Bearer auth: Cognito JWT (first-party clients) + bm_v1_* scoped tokens ──
      { name = "MCP_BEARER_AUTH", value = var.mcp_bearer_auth ? "true" : "false" },
      { name = "COGNITO_USER_POOL_ID", value = var.cognito_user_pool_id },
      { name = "COGNITO_CLIENT_ID", value = var.cognito_client_id },
      { name = "COGNITO_REGION", value = var.cognito_region },
      { name = "MCP_CONNECTIONS_TABLE", value = var.mcp_connections_table },
    ]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "MCP_API_KEYS", valueFrom = aws_secretsmanager_secret.mcp_api_keys.arn }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_mcp.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "mcp"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "mcp" {
  family                   = "${local.prefix}-mcp"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = local.ecs_container_definitions

  tags = merge(local.extra_tags, {
    Component = "ecs-taskdef"
  })
}

resource "aws_ecs_service" "mcp" {
  count           = var.ecs_desired_count > 0 ? 1 : 0
  name            = "${local.prefix}-mcp"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.mcp.arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"
  dynamic "load_balancer" {
    for_each = var.enable_alb ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.mcp[0].arn
      container_name   = "mcp-bookmarks"
      container_port   = var.mcp_container_port
    }
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  tags = merge(local.extra_tags, {
    Component = "ecs-service"
  })
}
