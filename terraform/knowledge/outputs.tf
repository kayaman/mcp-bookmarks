output "mcp_url" {
  description = "Base HTTPS URL of the Knowledge MCP server."
  value       = var.mcp_hostname == "" ? "https://${aws_cloudfront_distribution.this.domain_name}" : "https://${var.mcp_hostname}"
}

output "mcp_endpoint" {
  description = "Streamable-HTTP endpoint to point an MCP client at."
  value       = var.mcp_hostname == "" ? "https://${aws_cloudfront_distribution.this.domain_name}/mcp" : "https://${var.mcp_hostname}/mcp"
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain (always-available default)."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.this.id
}

output "elastic_ip" {
  description = "Stable public IP / origin for CloudFront."
  value       = aws_eip.this.public_ip
}

output "index_snapshot_bucket" {
  description = "S3 bucket holding the ANN index snapshot (empty when disabled)."
  value       = local.index_s3_bucket
}

output "ssm_connect" {
  description = "Open a shell on the box (no SSH key needed)."
  value       = "aws ssm start-session --target ${aws_instance.this.id} --region ${var.aws_region}"
}

output "bootstrap_log_hint" {
  description = "Watch the bootstrap after SSM connect."
  value       = "sudo tail -f /var/log/mcp-bootstrap.log"
}
