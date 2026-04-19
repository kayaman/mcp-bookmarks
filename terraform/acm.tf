# ACM TLS certificate for mcp.blogmarks.dev with DNS validation via Route 53.
# Requires enable_alb=true; the certificate is used by the HTTPS ALB listener.

resource "aws_acm_certificate" "mcp" {
  count             = var.enable_alb ? 1 : 0
  domain_name       = var.mcp_hostname
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.extra_tags, {
    Component = "acm-cert"
  })
}

# DNS validation record in the hosted zone.
resource "aws_route53_record" "cert_validation" {
  for_each = var.enable_alb ? {
    for dvo in aws_acm_certificate.mcp[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id         = var.route53_zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "mcp" {
  count                   = var.enable_alb ? 1 : 0
  certificate_arn         = aws_acm_certificate.mcp[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# A-alias record: mcp.blogmarks.dev → ALB DNS
resource "aws_route53_record" "mcp_alias" {
  count   = var.enable_alb ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.mcp_hostname
  type    = "A"

  alias {
    name                   = aws_lb.mcp[0].dns_name
    zone_id                = aws_lb.mcp[0].zone_id
    evaluate_target_health = true
  }
}
