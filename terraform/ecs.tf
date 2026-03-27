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
      Resource = [
        aws_dynamodb_table.links.arn,
        "${aws_dynamodb_table.links.arn}/index/*",
        aws_dynamodb_table.tags.arn,
        "${aws_dynamodb_table.tags.arn}/index/*"
      ]
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
      { name = "DYNAMODB_LINKS_TABLE", value = aws_dynamodb_table.links.name },
      { name = "DYNAMODB_TAGS_TABLE", value = aws_dynamodb_table.tags.name },
      { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      { name = "MCP_HOST", value = "0.0.0.0" },
      { name = "MCP_PORT", value = tostring(var.mcp_container_port) }
    ]
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn }
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

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  tags = merge(local.extra_tags, {
    Component = "ecs-service"
  })
}
