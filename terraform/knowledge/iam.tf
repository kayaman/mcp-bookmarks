data "aws_caller_identity" "current" {}

locals {
  ddb_prefix    = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table"
  links_arn     = "${local.ddb_prefix}/${var.links_table}"
  tags_arn      = "${local.ddb_prefix}/${var.tags_table}"
  conns_arn     = "${local.ddb_prefix}/${var.connections_table}"
  tag_edits_arn = "${local.ddb_prefix}/${var.tag_edits_table}"
  # Titan/Cohere embedding models are AWS-owned foundation models (no account id).
  bedrock_model_arn = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_embed_model}"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name_prefix        = "${local.name}-"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# SSM Session Manager (no SSH key, no port 22).
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app" {
  # Read bookmarks + tags; query the Knowledge type GSI (and any table GSI).
  statement {
    sid     = "ReadBookmarks"
    actions = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:BatchGetItem", "dynamodb:Scan"]
    resources = [
      local.links_arn,
      "${local.links_arn}/index/*",
      local.tags_arn,
      "${local.tags_arn}/index/*",
    ]
  }

  # Admin tag editing (Phase 1): replace-set aiTags + write-once snapshot
  # fields (aiTagsOriginal, tagsReviewedAt) on links rows.
  statement {
    sid       = "WriteBookmarkTags"
    actions   = ["dynamodb:UpdateItem"]
    resources = [local.links_arn]
  }

  # usage_count reconciliation (+/-1) and net-new tag-row creation.
  statement {
    sid       = "WriteTags"
    actions   = ["dynamodb:UpdateItem", "dynamodb:PutItem"]
    resources = [local.tags_arn]
  }

  # Tag-edit event log: append events + newest-first history reads
  # (single PK Query). Table is created by blogmarks CDK; granted by ARN.
  statement {
    sid       = "TagEditEvents"
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [local.tag_edits_arn]
  }

  # Validate bearer tokens via tokenHash-index; best-effort lastAccessAt update.
  statement {
    sid = "McpConnections"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [
      local.conns_arn,
      "${local.conns_arn}/index/*",
    ]
  }

  # Embed Knowledge content + queries.
  statement {
    sid       = "BedrockEmbeddings"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.bedrock_model_arn]
  }

  # Pull the container image from ECR (when ECR-hosted).
  dynamic "statement" {
    for_each = var.ecr_repo_name == "" ? [] : [1]
    content {
      sid       = "EcrAuth"
      actions   = ["ecr:GetAuthorizationToken"]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = var.ecr_repo_name == "" ? [] : [1]
    content {
      sid = "EcrPull"
      actions = [
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
      ]
      resources = ["arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${var.ecr_repo_name}"]
    }
  }

  # Persist / warm-start the ANN index snapshot.
  dynamic "statement" {
    for_each = var.enable_index_snapshots ? [1] : []
    content {
      sid     = "IndexSnapshots"
      actions = ["s3:GetObject", "s3:PutObject"]
      resources = [
        "${aws_s3_bucket.index[0].arn}/${local.index_s3_key}",
        "${aws_s3_bucket.index[0].arn}/${local.index_s3_key}.manifest.json",
      ]
    }
  }
}

resource "aws_iam_role_policy" "app" {
  name_prefix = "${local.name}-"
  role        = aws_iam_role.this.id
  policy      = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "this" {
  name_prefix = "${local.name}-"
  role        = aws_iam_role.this.name
}
