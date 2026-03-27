resource "aws_security_group" "ecs_tasks" {
  name        = "${local.prefix}-ecs-tasks"
  description = "MCP / Fargate tasks"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.extra_tags, {
    Component = "ecs"
  })
}

resource "aws_security_group" "rds" {
  name        = "${local.prefix}-rds"
  description = "PostgreSQL pgvector (knowledge islands)"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Postgres from ECS tasks only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.extra_tags, {
    Component = "rds-pgvector"
  })
}
