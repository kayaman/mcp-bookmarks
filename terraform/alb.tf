# Public ALB in front of Fargate MCP tasks.
# Listens on :443 (HTTPS, ACM cert) with :80 redirecting to HTTPS.
# idle_timeout is 300s to keep SSE long-polls alive.

resource "aws_security_group" "alb" {
  count       = var.enable_alb ? 1 : 0
  name        = "${local.prefix}-alb"
  description = "Public ALB for MCP / REST"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP (redirects to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
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
  # 300s keeps SSE connections alive through ALB idle timeout
  idle_timeout = 300

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

  # Redirect all HTTP traffic to HTTPS
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "mcp_https" {
  count             = var.enable_alb ? 1 : 0
  load_balancer_arn = aws_lb.mcp[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.mcp[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mcp[0].arn
  }
}
