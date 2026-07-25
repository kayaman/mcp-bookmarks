provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      Service   = "mcp-bookmarks"
      Product   = "mcp-bookmarks"
      ManagedBy = "terraform"
      Stack     = "mcp-bookmarks-knowledge"
      CostRole  = "knowledge-islands"
      Owner     = var.owner_tag
    }
  }
}

# CloudFront requires its ACM cert in us-east-1, regardless of where the box runs.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = var.project_name
      Service   = "mcp-bookmarks"
      ManagedBy = "terraform"
      Stack     = "mcp-bookmarks-knowledge"
    }
  }
}
