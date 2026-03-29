resource "aws_dynamodb_table" "links" {
  name         = "${local.prefix}-links"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "organization_id"
    type = "S"
  }

  attribute {
    name = "savedAt"
    type = "S"
  }

  global_secondary_index {
    name            = "orgId-savedAt-index"
    hash_key        = "organization_id"
    range_key       = "savedAt"
    projection_type = "ALL"
  }

  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-links"
    Component = "dynamodb-links"
    CostRole  = "bookmark-store"
  })
}

resource "aws_dynamodb_table" "tags" {
  name         = "${local.prefix}-tags"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "slug"

  attribute {
    name = "slug"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-tags"
    Component = "dynamodb-tags"
    CostRole  = "taxonomy"
  })
}
