locals {
  prefix = "${var.project_name}-${var.environment}"

  # Tags extras por recurso (somam às default_tags do provider)
  extra_tags = {
    Stack = "blogmarks-saas-mvp"
  }
}

data "aws_caller_identity" "current" {}
