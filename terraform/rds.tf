resource "random_password" "db_master" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.prefix}-db-subnets"
  subnet_ids = aws_subnet.private[*].id

  tags = merge(local.extra_tags, {
    Name      = "${local.prefix}-db-subnets"
    Component = "rds-pgvector"
  })
}

resource "aws_db_instance" "pgvector" {
  identifier                 = "${local.prefix}-pgvector"
  engine                     = "postgres"
  engine_version             = "16"
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage_gb
  storage_type               = "gp3"
  db_name                    = "blogmarks"
  username                   = "blogmarks"
  password                   = random_password.db_master.result
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [aws_security_group.rds.id]
  skip_final_snapshot        = true
  publicly_accessible        = false
  deletion_protection        = false
  backup_retention_period    = var.environment == "prod" ? 7 : 1
  auto_minor_version_upgrade = true

  tags = merge(local.extra_tags, {
    Name        = "${local.prefix}-pgvector"
    Component   = "rds-pgvector"
    CostRole    = "knowledge-islands"
    Description = "PostgreSQL + pgvector: semantic islands per tenant"
  })
}

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.prefix}/database-url"
  recovery_window_in_days = 0

  tags = merge(local.extra_tags, {
    Component = "secrets"
  })
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql://blogmarks:%s@%s:%s/blogmarks",
    random_password.db_master.result,
    aws_db_instance.pgvector.address,
    aws_db_instance.pgvector.port
  )
}
