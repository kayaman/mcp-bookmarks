# Optional public ALB in front of Fargate MCP tasks (HTTP :80).

resource "aws_security_group" "alb" {
  count       = var.enable_alb ? 1 : 0
  name        = "${local.prefix}-alb"
  description = "Public ALB for MCP / REST"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.extra_tags, {
    Component = "alb-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  count                        = var.enable_alb ? 1 : 0
  security_group_id            = aws_security_group.ecs_tasks.id
  referenced_security_group_id = aws_security_group.alb[0].id
  from_port                    = var.mcp_container_port
  to_port                      = var.mcp_container_port
  ip_protocol                  = "tcp"
  description                  = "MCP/REST from ALB"

  tags = merge(local.extra_tags, {
    Component = "ecs-alb-ingress"
  })
}

resource "aws_lb" "mcp" {
  count              = var.enable_alb ? 1 : 0
  name               = "${local.prefix}-mcp"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb[0].id]
  subnets            = aws_subnet.public[*].id

  tags = merge(local.extra_tags, {
    Component = "alb"
    CostRole  = "edge"
  })
}

resource "aws_lb_target_group" "mcp" {
  count       = var.enable_alb ? 1 : 0
  name        = "${local.prefix}-mcp-tg"
  port        = var.mcp_container_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    path                = "/api/stats"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  tags = merge(local.extra_tags, {
    Component = "alb-tg"
  })
}

resource "aws_lb_listener" "mcp_http" {
  count             = var.enable_alb ? 1 : 0
  load_balancer_arn = aws_lb.mcp[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mcp[0].arn
  }
}
