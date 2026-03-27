provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      Service     = "blogmarks"
      Product     = "mcp-bookmarks"
      ManagedBy   = "terraform"
      CostCenter  = var.cost_center
      Owner       = var.owner_tag
    }
  }
}

provider "random" {}
