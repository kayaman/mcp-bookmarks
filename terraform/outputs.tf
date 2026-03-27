output "dynamodb_links_table" {
  description = "Set DYNAMODB_LINKS_TABLE to this value."
  value       = aws_dynamodb_table.links.name
}

output "dynamodb_tags_table" {
  description = "Set DYNAMODB_TAGS_TABLE to this value."
  value       = aws_dynamodb_table.tags.name
}

output "dynamodb_stream_arn" {
  value = aws_dynamodb_table.links.stream_arn
}

output "sqs_dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "lambda_function_name" {
  description = "Lambda processor (empty if enable_lambda_processor=false)."
  value         = try(aws_lambda_function.processor[0].function_name, null)
}

output "rds_endpoint" {
  description = "PostgreSQL host:port (credentials in Secrets Manager)."
  value       = aws_db_instance.pgvector.endpoint
}

output "database_secret_arn" {
  description = "DATABASE_URL for apps (ECS / future workers)."
  value       = aws_secretsmanager_secret.database_url.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.mcp.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "vpc_id" {
  value = aws_vpc.main.id
}
