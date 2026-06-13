# ── Optional custom domain. Everything here is created ONLY when mcp_hostname is set;
#    leave it blank and you demo on the free CloudFront domain with zero extra setup.

# CloudFront certs must live in us-east-1 — hence the aliased provider.
resource "aws_acm_certificate" "demo" {
  count             = var.mcp_hostname == "" ? 0 : 1
  provider          = aws.us_east_1
  domain_name       = var.mcp_hostname
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = var.mcp_hostname == "" ? {} : {
    for o in aws_acm_certificate.demo[0].domain_validation_options : o.domain_name => {
      name   = o.resource_record_name
      type   = o.resource_record_type
      record = o.resource_record_value
    }
  }

  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "demo" {
  count                   = var.mcp_hostname == "" ? 0 : 1
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.demo[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# A/AAAA alias from the custom hostname to the distribution.
resource "aws_route53_record" "alias" {
  for_each = var.mcp_hostname == "" ? toset([]) : toset(["A", "AAAA"])
  zone_id  = var.route53_zone_id
  name     = var.mcp_hostname
  type     = each.value

  alias {
    name                   = aws_cloudfront_distribution.demo.domain_name
    zone_id                = aws_cloudfront_distribution.demo.hosted_zone_id
    evaluate_target_health = false
  }
}
