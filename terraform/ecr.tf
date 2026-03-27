resource "aws_ecr_repository" "mcp" {
  name                 = "${local.prefix}-mcp-bookmarks"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.extra_tags, {
    Component = "ecr"
  })
}
