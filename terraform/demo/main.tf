locals {
  name = "${var.project_name}-demo"
}

# ── Look up the default VPC + a subnet (no VPC/NAT to pay for) ────────────────
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ── Latest Amazon Linux 2023 AMI (architecture matches t3.* = x86_64) ─────────
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ── CloudFront's origin-facing IP ranges (the ONLY thing allowed inbound) ─────
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# ── Security group: inbound :app_port from CloudFront only; all egress ────────
resource "aws_security_group" "demo" {
  name_prefix = "${local.name}-"
  description = "mcp-bookmarks demo: CloudFront origin-facing ingress only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "CloudFront edge to origin"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    description = "All outbound (git clone, image pulls, scraping)"
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

# ── IAM: instance role for SSM Session Manager (no SSH key, no port 22) ───────
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "demo" {
  name_prefix        = "${local.name}-"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.demo.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "demo" {
  name_prefix = "${local.name}-"
  role        = aws_iam_role.demo.name
}

# ── The one little box ───────────────────────────────────────────────────────
resource "aws_instance" "demo" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  vpc_security_group_ids      = [aws_security_group.demo.id]
  iam_instance_profile        = aws_iam_instance_profile.demo.name
  associate_public_ip_address = true

  user_data                   = file("${path.module}/../../scripts/ec2-demo-userdata.sh")
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

# ── Elastic IP: stable origin DNS across stop/start (public_dns changes otherwise)
resource "aws_eip" "demo" {
  domain   = "vpc"
  instance = aws_instance.demo.id
  tags     = { Name = "${local.name}-eip" }
}
