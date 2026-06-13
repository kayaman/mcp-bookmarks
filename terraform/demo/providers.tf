provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = "demo"
      Service     = "mcp-bookmarks"
      Product     = "mcp-bookmarks"
      ManagedBy   = "terraform"
      Stack       = "mcp-bookmarks-demo"
      Owner       = var.owner_tag
    }
  }
}

# CloudFront requires its ACM cert in us-east-1, regardless of where the box runs.
# Used only when var.mcp_hostname is set (custom domain). Harmless otherwise.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = var.project_name
      Service   = "mcp-bookmarks"
      ManagedBy = "terraform"
      Stack     = "mcp-bookmarks-demo"
    }
  }
}
