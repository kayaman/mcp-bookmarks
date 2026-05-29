locals {
  prefix = "${var.project_name}-${var.environment}"

  # Tags extras por recurso (somam às default_tags do provider)
  extra_tags = {
    Stack = "${var.project_name}-saas-mvp"
  }
}

data "aws_caller_identity" "current" {}
