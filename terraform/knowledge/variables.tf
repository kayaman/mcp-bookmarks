variable "aws_region" {
  description = "AWS region for the EC2 box. CloudFront + its cert are global / us-east-1."
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
  description = "EC2 instance type. t3.small (2 GB) holds the ANN index + Python comfortably."
  type        = string
  default     = "t3.small"
}

variable "app_port" {
  description = "Port the mcp-bookmarks container listens on (CloudFront origin port)."
  type        = number
  default     = 8000
}

variable "root_volume_gb" {
  description = "gp3 root volume: OS, image, and the persisted ANN index."
  type        = number
  default     = 20
}

variable "container_image" {
  description = "Semantic image (built from Containerfile.knowledge) to run on the box."
  type        = string
  default     = "ghcr.io/kayaman/mcp-bookmarks-knowledge:latest"
}

variable "ecr_repo_name" {
  description = "ECR repo the instance pulls the image from (grants scoped ecr:Pull). Blank = image is not ECR-hosted (e.g. GHCR)."
  type        = string
  default     = ""
}

# ── Blogmarks data plane (shared tables in account 257394450889) ──────────────

variable "links_table" {
  description = "DynamoDB bookmarks table."
  type        = string
  default     = "blogmarks-links"
}

variable "tags_table" {
  description = "DynamoDB tags table."
  type        = string
  default     = "blogmarks-tags"
}

variable "type_index" {
  description = "Optional GSI (userId, bookmarkType). blogmarks-links doesn't have one, so default blank → use the userId GSI + a server-side type filter."
  type        = string
  default     = ""
}

variable "user_index" {
  description = "GSI keyed on userId (present on blogmarks-links as userId-savedAt-index); queried by userId with a bookmarkType filter when type_index is blank."
  type        = string
  default     = "userId-savedAt-index"
}

variable "connections_table" {
  description = "Shared MCP bearer-token connections table (validated via tokenHash-index)."
  type        = string
  default     = "blogmarks-mcp-connections"
}

# ── Auth (first-party owner JWT path) ─────────────────────────────────────────

variable "cognito_user_pool_id" {
  description = "Cognito user pool id for the owner JWT path (prod: us-east-1_iVTzSS4Ky)."
  type        = string
  default     = ""
}

variable "cognito_client_id" {
  description = "Cognito app client id for the owner JWT path."
  type        = string
  default     = ""
}

# ── Semantic index ────────────────────────────────────────────────────────────

variable "owner_user_id" {
  description = "Cognito sub whose Knowledge bookmarks are indexed."
  type        = string
}

variable "index_user_ids" {
  description = "Optional comma-separated override for whose Knowledge is indexed (blank = owner_user_id)."
  type        = string
  default     = ""
}

variable "bedrock_embed_model" {
  description = "Bedrock embeddings model id."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "bedrock_embed_dims" {
  description = "Embedding dimensionality (Titan v2: 256|512|1024)."
  type        = number
  default     = 1024
}

variable "refresh_seconds" {
  description = "Background index refresh interval (seconds)."
  type        = number
  default     = 300
}

variable "enable_index_snapshots" {
  description = "Create an S3 bucket and snapshot the ANN index to it for warm-starts."
  type        = bool
  default     = true
}

# ── Custom domain (leave blank to validate on the free *.cloudfront.net) ──────

variable "mcp_hostname" {
  description = "Hostname the server serves (e.g. mcp.blogmarks.dev). Blank = CloudFront default domain + cert."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone id for mcp_hostname. Required only when mcp_hostname is set."
  type        = string
  default     = ""
}
