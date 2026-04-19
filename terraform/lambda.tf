check "lambda_zip_present" {
  assert {
    condition = (
      !var.enable_lambda_processor ||
      fileexists("${path.module}/.build/lambda_processor.zip")
    )
    error_message = "Run scripts/package-lambda.sh from repo root to create terraform/.build/lambda_processor.zip, or set enable_lambda_processor=false."
  }
}

resource "aws_cloudwatch_log_group" "lambda_processor" {
  count             = var.enable_lambda_processor ? 1 : 0
  name              = "/aws/lambda/${local.prefix}-processor"
  retention_in_days = 14

  tags = merge(local.extra_tags, {
    Component = "lambda-logs"
  })
}

resource "aws_iam_role" "lambda_processor" {
  count = var.enable_lambda_processor ? 1 : 0
  name  = "${local.prefix}-lambda-processor"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = merge(local.extra_tags, {
    Component = "lambda-iam"
  })
}

resource "aws_iam_role_policy" "lambda_processor" {
  count = var.enable_lambda_processor ? 1 : 0
  name  = "${local.prefix}-lambda-processor"
  role  = aws_iam_role.lambda_processor[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBStream"
        Effect = "Allow"
        Action = [
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:DescribeStream",
          "dynamodb:ListStreams"
        ]
        Resource = aws_dynamodb_table.links.stream_arn
      },
      {
        Sid    = "DynamoDBTables"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchGetItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          aws_dynamodb_table.links.arn,
          "${aws_dynamodb_table.links.arn}/index/*",
          aws_dynamodb_table.tags.arn,
          "${aws_dynamodb_table.tags.arn}/index/*"
        ]
      },
      {
        Sid      = "DLQ"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dlq.arn
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_lambda_function" "processor" {
  count            = var.enable_lambda_processor ? 1 : 0
  function_name    = "${local.prefix}-processor"
  role             = aws_iam_role.lambda_processor[0].arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  filename         = "${path.module}/.build/lambda_processor.zip"
  source_code_hash = filebase64sha256("${path.module}/.build/lambda_processor.zip")

  environment {
    variables = {
      LINKS_TABLE        = aws_dynamodb_table.links.name
      TAGS_TABLE         = aws_dynamodb_table.tags.name
      ANTHROPIC_API_KEY  = var.anthropic_api_key
      AWS_DEFAULT_REGION = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda_processor]

  tags = merge(local.extra_tags, {
    Component = "lambda-processor"
    CostRole  = "ai-enrichment"
  })
}

resource "aws_lambda_event_source_mapping" "links_stream" {
  count                              = var.enable_lambda_processor ? 1 : 0
  event_source_arn                   = aws_dynamodb_table.links.stream_arn
  function_name                      = aws_lambda_function.processor[0].arn
  starting_position                  = "LATEST"
  batch_size                         = 5
  maximum_batching_window_in_seconds = 10
  bisect_batch_on_function_error     = true

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}
