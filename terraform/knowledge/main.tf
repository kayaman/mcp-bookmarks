locals {
  name            = "${var.project_name}-knowledge"
  index_user_ids  = var.index_user_ids != "" ? var.index_user_ids : var.owner_user_id
  index_s3_bucket = var.enable_index_snapshots ? aws_s3_bucket.index[0].bucket : ""
  index_s3_key    = "knowledge-index/index.bin"
  ecr_registry    = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
}

# ── Default VPC + a subnet (no VPC/NAT to pay for; public-subnet placement) ───
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ── Latest Amazon Linux 2023 (x86_64 matches t3.*) ────────────────────────────
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ── CloudFront's origin-facing IP ranges: the only inbound allowed ────────────
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# ── Shared secret CloudFront injects on every origin request (defense-in-depth)
resource "random_password" "origin_secret" {
  length  = 40
  special = false
}

resource "aws_security_group" "this" {
  name_prefix = "${local.name}-"
  description = "mcp-bookmarks knowledge: CloudFront origin-facing ingress only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "CloudFront edge to origin"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    description = "All outbound (image pulls, DynamoDB, Bedrock, S3)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

# ── The instance ──────────────────────────────────────────────────────────────
resource "aws_instance" "this" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  vpc_security_group_ids      = [aws_security_group.this.id]
  iam_instance_profile        = aws_iam_instance_profile.this.name
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/../../scripts/ec2-knowledge-userdata.sh.tftpl", {
    image                = var.container_image
    ecr_registry         = var.ecr_repo_name == "" ? "" : local.ecr_registry
    app_port             = var.app_port
    region               = var.aws_region
    links_table          = var.links_table
    tags_table           = var.tags_table
    type_index           = var.type_index
    user_index           = var.user_index
    connections_table    = var.connections_table
    tag_edits_table      = var.tag_edits_table
    cognito_user_pool_id = var.cognito_user_pool_id
    cognito_client_id    = var.cognito_client_id
    owner_user_id        = var.owner_user_id
    index_user_ids       = local.index_user_ids
    bedrock_embed_model  = var.bedrock_embed_model
    bedrock_embed_dims   = var.bedrock_embed_dims
    refresh_seconds      = var.refresh_seconds
    index_s3_bucket      = local.index_s3_bucket
    index_s3_key         = local.index_s3_key
    origin_shared_secret = random_password.origin_secret.result
  })
  user_data_replace_on_change = true

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    encrypted   = true
  }

  tags = { Name = local.name }
}

# ── Elastic IP: stable origin DNS across stop/start ───────────────────────────
resource "aws_eip" "this" {
  domain   = "vpc"
  instance = aws_instance.this.id
  tags     = { Name = "${local.name}-eip" }
}
