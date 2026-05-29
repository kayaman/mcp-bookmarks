# CloudWatch alarms + SNS topic for operational alerting (WDN-398 / OSS-8).
#
# All alarms send to a single SNS topic the operator subscribes ``budget_alert_email``
# to. Failure-domain coverage:
#
#   - ALB target 5xx                 (compute-tier server errors)
#   - ALB target response time p99    (end-to-end latency regression)
#   - DynamoDB read/write throttles    (capacity saturation per table)
#   - Lambda errors                    (enrichment pipeline failures)
#   - Lambda duration approaching timeout (silent slowdowns before they 500)
#
# Budgets already cover monthly cost (terraform/budgets.tf) so we do NOT
# re-implement a cost alarm here.

resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-alerts"

  tags = merge(local.extra_tags, {
    Component = "alerts"
  })
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.budget_alert_email
}


# ── ALB alarms (only when enable_alb) ──────────────────────────────


resource "aws_cloudwatch_metric_alarm" "alb_target_5xx" {
  count               = var.enable_alb ? 1 : 0
  alarm_name          = "${local.prefix}-alb-target-5xx"
  alarm_description   = "ECS target group returning 5xx — application errors past 5/min for 2 consecutive 1-minute windows."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  evaluation_periods  = 2
  period              = 60
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.mcp[0].arn_suffix
    TargetGroup  = aws_lb_target_group.mcp[0].arn_suffix
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-alb"
  })
}

resource "aws_cloudwatch_metric_alarm" "alb_target_p99_latency" {
  count               = var.enable_alb ? 1 : 0
  alarm_name          = "${local.prefix}-alb-target-p99-latency"
  alarm_description   = "p99 ALB target response time above 2s for 3 consecutive 1-minute windows."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  extended_statistic  = "p99"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 2.0
  evaluation_periods  = 3
  period              = 60
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.mcp[0].arn_suffix
    TargetGroup  = aws_lb_target_group.mcp[0].arn_suffix
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-alb"
  })
}


# ── DynamoDB throttle alarms (per table) ───────────────────────────


locals {
  dynamodb_tables = {
    links         = aws_dynamodb_table.links.name
    tags          = aws_dynamodb_table.tags.name
    usage_events  = aws_dynamodb_table.usage_events.name
    subscriptions = aws_dynamodb_table.subscriptions.name
  }
}

resource "aws_cloudwatch_metric_alarm" "ddb_read_throttle" {
  for_each            = local.dynamodb_tables
  alarm_name          = "${local.prefix}-ddb-${each.key}-read-throttle"
  alarm_description   = "DynamoDB ReadThrottleEvents > 0 over a 5-min window for ${each.value}."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-dynamodb"
  })
}

resource "aws_cloudwatch_metric_alarm" "ddb_write_throttle" {
  for_each            = local.dynamodb_tables
  alarm_name          = "${local.prefix}-ddb-${each.key}-write-throttle"
  alarm_description   = "DynamoDB WriteThrottleEvents > 0 over a 5-min window for ${each.value}."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-dynamodb"
  })
}


# ── Lambda processor alarms (only when enable_lambda_processor) ────


resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  count               = var.enable_lambda_processor ? 1 : 0
  alarm_name          = "${local.prefix}-lambda-processor-errors"
  alarm_description   = "Lambda processor reported errors > 1 in a 5-min window (after retries are accounted for, this is real failure)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 1
  evaluation_periods  = 1
  period              = 300
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.processor[0].function_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-lambda"
  })
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration_p95_near_timeout" {
  count               = var.enable_lambda_processor ? 1 : 0
  alarm_name          = "${local.prefix}-lambda-processor-duration-p95"
  alarm_description   = "Lambda processor p95 duration > 80% of the 300s configured timeout for 2 consecutive 5-min windows."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  extended_statistic  = "p95"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 240000 # ms — 80% of the 300s timeout in lambda.tf
  evaluation_periods  = 2
  period              = 300
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.processor[0].function_name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-lambda"
  })
}

resource "aws_cloudwatch_metric_alarm" "lambda_dlq_messages" {
  count               = var.enable_lambda_processor ? 1 : 0
  alarm_name          = "${local.prefix}-lambda-dlq-not-empty"
  alarm_description   = "DLQ has messages — the stream batch could not be processed after retries."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 300
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = merge(local.extra_tags, {
    Component = "alarms-dlq"
  })
}
