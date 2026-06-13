output "mcp_url" {
  description = "Base HTTPS URL of the MCP server."
  value       = var.mcp_hostname == "" ? "https://${aws_cloudfront_distribution.demo.domain_name}" : "https://${var.mcp_hostname}"
}

output "mcp_sse_endpoint" {
  description = "SSE endpoint to point an MCP client at."
  value       = var.mcp_hostname == "" ? "https://${aws_cloudfront_distribution.demo.domain_name}/sse" : "https://${var.mcp_hostname}/sse"
}

output "cloudfront_domain" {
  description = "CloudFront distribution domain (the always-available default)."
  value       = aws_cloudfront_distribution.demo.domain_name
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.demo.id
}

output "elastic_ip" {
  description = "Stable public IP / origin for CloudFront."
  value       = aws_eip.demo.public_ip
}

output "ssm_connect" {
  description = "Open a shell on the box (no SSH key needed)."
  value       = "aws ssm start-session --target ${aws_instance.demo.id} --region ${var.aws_region}"
}

output "bootstrap_log_hint" {
  description = "Watch the bootstrap after SSM connect."
  value       = "sudo tail -f /var/log/mcp-bootstrap.log"
}
