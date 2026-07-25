# ── AWS-managed policies: don't cache, and forward everything (incl. Authorization)
#    CachingDisabled is essential for SSE — otherwise CloudFront tries to buffer the
#    stream. AllViewerExceptHostHeader passes the bearer token + query string through.
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "demo" {
  enabled         = true
  comment         = "${local.name} — MCP bookmarks demo"
  http_version    = "http2and3"
  price_class     = "PriceClass_100" # NA + EU edges; cheapest
  is_ipv6_enabled = true

  # Custom origin = the EC2 box, reached at its stable Elastic-IP DNS over plain HTTP.
  origin {
    origin_id   = "ec2-mcp"
    domain_name = aws_eip.demo.public_dns

    custom_origin_config {
      http_port                = var.app_port
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # max between-byte wait; SSE keepalives stay under this
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "ec2-mcp"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false # don't buffer/compress the SSE stream

    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Free default cert on *.cloudfront.net, OR the custom-domain cert from domain.tf.
  dynamic "viewer_certificate" {
    for_each = var.mcp_hostname == "" ? [1] : []
    content {
      cloudfront_default_certificate = true
    }
  }

  dynamic "viewer_certificate" {
    for_each = var.mcp_hostname == "" ? [] : [1]
    content {
      acm_certificate_arn      = aws_acm_certificate_validation.demo[0].certificate_arn
      ssl_support_method       = "sni-only"
      minimum_protocol_version = "TLSv1.2_2021"
    }
  }

  aliases = var.mcp_hostname == "" ? [] : [var.mcp_hostname]

  tags = { Name = local.name }
}
