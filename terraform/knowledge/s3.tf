# ANN index snapshot bucket — lets an instance replacement warm-start from the
# last saved index instead of re-embedding the whole Knowledge corpus.
resource "aws_s3_bucket" "index" {
  count         = var.enable_index_snapshots ? 1 : 0
  bucket_prefix = "${local.name}-index-"
  force_destroy = true
  tags          = { Name = "${local.name}-index" }
}

resource "aws_s3_bucket_public_access_block" "index" {
  count                   = var.enable_index_snapshots ? 1 : 0
  bucket                  = aws_s3_bucket.index[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "index" {
  count  = var.enable_index_snapshots ? 1 : 0
  bucket = aws_s3_bucket.index[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
