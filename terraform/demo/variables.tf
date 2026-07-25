variable "aws_region" {
  description = "AWS region for the EC2 box. (CloudFront + its cert are global / us-east-1.)"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Logical prefix and Project tag."
  type        = string
  default     = "mcp-bookmarks"
}

variable "owner_tag" {
  description = "Owner tag (person or team)."
  type        = string
  default     = "founder"
}

variable "instance_type" {
  description = "EC2 instance type. t3.micro (1 GB) is enough with the bootstrap swapfile."
  type        = string
  default     = "t3.micro"
}

variable "app_port" {
  description = "Port the mcp-bookmarks container listens on (CloudFront origin port)."
  type        = number
  default     = 8000
}

variable "root_volume_gb" {
  description = "gp3 root volume size. Holds the OS, image, and the SQLite DB."
  type        = number
  default     = 12
}

# ── Optional custom domain (leave blank to demo on the free *.cloudfront.net) ──

variable "mcp_hostname" {
  description = "Custom hostname for the MCP server (e.g. mcp-demo.example.com). Blank = use the CloudFront default domain + cert (no Route 53 / ACM needed)."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for mcp_hostname. Required only when mcp_hostname is set."
  type        = string
  default     = ""
}
