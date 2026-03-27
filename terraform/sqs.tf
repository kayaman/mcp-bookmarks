resource "aws_sqs_queue" "dlq" {
  name                      = "${local.prefix}-processor-dlq"
  message_retention_seconds = 1209600

  tags = merge(local.extra_tags, {
    Component = "sqs-dlq"
  })
}
