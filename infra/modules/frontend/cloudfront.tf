# ─────────────────────────────────────────────────────────────────────────────
# CloudFront distribution fronting the private S3 site bucket via Origin Access
# Control. SPA fallback: 403/404 → /index.html (200). WAF-associated, access-logged.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.name_prefix}-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  #checkov:skip=CKV2_AWS_42:Custom SSL certificate requires a customer-owned domain; this deployment uses the default *.cloudfront.net domain (no custom domain available). Customer sets their own cert at deploy time.
  #checkov:skip=CKV2_AWS_32:Response-headers policy is a product decision; the SPA sets security headers at the application layer. Accepted for this deployment.
  #checkov:skip=CKV2_AWS_47:Log4j WAF rule is covered by AWSManagedRulesKnownBadInputsRuleSet on the attached Web ACL; the specific managed-rule variant Checkov expects is not required here.
  #checkov:skip=CKV_AWS_310:Origin failover (a second origin group) is unnecessary — the single S3 origin is a highly-available static site with no secondary origin. Accepted risk.
  #checkov:skip=CKV_AWS_374:Geo restriction is a product/compliance decision; the platform is intended to be globally accessible for this deployment. Accepted risk.
  enabled             = true
  default_root_object = "index.html"
  web_acl_id          = aws_wafv2_web_acl.cloudfront.arn
  comment             = "${var.name_prefix} frontend"
  aliases             = var.cloudfront_aliases

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "frontend-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id       = "frontend-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]

    # AWS managed CachingOptimized policy.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  # SPA fallback: static export serves index.html for unknown routes.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Custom ACM cert when provided (enforces TLSv1.2_2021 — CKV_AWS_174 / CKV2_AWS_42);
  # otherwise the default CloudFront certificate. A custom cert requires a customer domain.
  viewer_certificate {
    cloudfront_default_certificate = var.cloudfront_acm_certificate_arn == ""
    acm_certificate_arn            = var.cloudfront_acm_certificate_arn != "" ? var.cloudfront_acm_certificate_arn : null
    ssl_support_method             = var.cloudfront_acm_certificate_arn != "" ? "sni-only" : null
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  logging_config {
    bucket          = "${var.access_logs_bucket_id}.s3.amazonaws.com"
    prefix          = "cloudfront/"
    include_cookies = false
  }

  tags = var.tags
}
