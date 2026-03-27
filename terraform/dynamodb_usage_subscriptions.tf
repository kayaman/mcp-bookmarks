# Optional usage metering and Stripe subscription snapshots (app-level).

resource "aws_dynamodb_table" "usage_events" {
  name         = "${local.prefix}-usage-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-usage-events"
    Component = "dynamodb-usage"
    CostRole  = "usage-meter"
  })
}

resource "aws_dynamodb_table" "subscriptions" {
  name         = "${local.prefix}-subscriptions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "customerId"

  attribute {
    name = "customerId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-subscriptions"
    Component = "dynamodb-subscriptions"
    CostRole  = "billing"
  })
}
