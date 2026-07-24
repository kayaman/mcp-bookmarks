# Custom domain — created ONLY when mcp_hostname is set. For staged validation
# (Phase B) leave it blank and use the free *.cloudfront.net domain; for the
# cutover (Phase C) set mcp_hostname = mcp.blogmarks.dev + the zone id.

# CloudFront certs must live in us-east-1 — hence the aliased provider.
resource "aws_acm_certificate" "this" {
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
    for o in aws_acm_certificate.this[0].domain_validation_options : o.domain_name => {
      name   = o.resource_record_name
      type   = o.resource_record_type
      record = o.resource_record_value
    }
  }

  zone_id         = var.route53_zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  count                   = var.mcp_hostname == "" ? 0 : 1
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# A/AAAA alias from the hostname to the distribution. allow_overwrite=true so
# the cutover takes over the existing (manually-created) mcp.blogmarks.dev
# record that currently points at the read-mcp Lambda CloudFront.
resource "aws_route53_record" "alias" {
  for_each = var.mcp_hostname == "" ? toset([]) : toset(["A", "AAAA"])
  zone_id  = var.route53_zone_id
  name     = var.mcp_hostname
  type     = each.value

  alias {
    name                   = aws_cloudfront_distribution.this.domain_name
    zone_id                = aws_cloudfront_distribution.this.hosted_zone_id
    evaluate_target_health = false
  }

  allow_overwrite = true
}
