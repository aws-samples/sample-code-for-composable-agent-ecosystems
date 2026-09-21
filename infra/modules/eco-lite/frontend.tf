// terraform-tessera-eco :: single-eco frontend hosting (S3 + CloudFront + WAF)
//
// Hosts the purpose-built single-ecosystem SPA (webui/) — NOT the platform console.
// Static site behind CloudFront (OAC, private S3) + WAF. config.js is rendered from module
// outputs at deploy (runtime config; no build-time baking, no platform code shipped).
// NOTE: WAFv2 CLOUDFRONT scope must live in us-east-1; for non-us-east-1 deploys add a
// us-east-1 provider alias. (Test region is us-east-1.)

resource "aws_s3_bucket" "web" {
  #checkov:skip=CKV_AWS_144:Rebuildable static-site content; cross-region replication is out of scope for this single-region customer template (no DR requirement). Accepted risk.
  #checkov:skip=CKV_AWS_145:Static site served via CloudFront OAC; encrypted with SSE-S3 (AES256), which meets the at-rest requirement for public web assets. Accepted risk.
  bucket        = "${local.name}-web"
  force_destroy = var.force_destroy_data
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle (CKV2_AWS_61): the site content is a rebuildable static export, so no object
# expiration — just clean up abandoned multipart uploads (CKV_AWS_300).
resource "aws_s3_bucket_lifecycle_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# S3 event notification (CKV2_AWS_62): EventBridge delivery, additive.
resource "aws_s3_bucket_notification" "web" {
  bucket      = aws_s3_bucket.web.id
  eventbridge = true
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_wafv2_web_acl" "web" {
  name        = "${local.name}-web"
  scope       = "CLOUDFRONT"
  description = "WAF for the single-eco frontend"
  default_action {
    allow {}
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${replace(local.name, "-", "_")}_web"
    sampled_requests_enabled   = true
  }
  rule {
    name     = "common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "common"
      sampled_requests_enabled   = true
    }
  }
  # KnownBadInputs covers the Log4j/JNDI lookup exploit (CVE-2021-44228) — CKV_AWS_192.
  rule {
    name     = "known-bad-inputs"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "known_bad_inputs"
      sampled_requests_enabled   = true
    }
  }
  tags = local.tags
}

# ── WAF logging (CKV2_AWS_31) ─────────────────────────────────────────────────
# WAFv2 logging requires a CloudWatch log group whose name starts with "aws-waf-logs-",
# in the same region as the (CLOUDFRONT-scoped) Web ACL — i.e. us-east-1. Encrypted with
# the ecosystem CMK (key policy already grants logs.<region>.amazonaws.com).
resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${local.name}-web"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_wafv2_web_acl_logging_configuration" "web" {
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  resource_arn            = aws_wafv2_web_acl.web.arn
}

# CloudFront standard access-log target. CloudFront log delivery to S3 requires ACLs
# enabled (BucketOwnerPreferred) + SSE-S3 (AES256; SSE-KMS is not supported for CF logs).
resource "aws_s3_bucket" "cf_logs" {
  #checkov:skip=CKV_AWS_144:CloudFront access logs; cross-region replication is out of scope for this single-region customer template (no DR requirement). Accepted risk.
  #checkov:skip=CKV_AWS_145:CloudFront standard log delivery requires SSE-S3 (AES256) — SSE-KMS is NOT supported for CloudFront logs. AES256 is used by design. Accepted risk.
  bucket        = "${local.name}-cf-logs"
  force_destroy = var.force_destroy_data
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "cf_logs" {
  bucket                  = aws_s3_bucket.cf_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 event notification (CKV2_AWS_62): EventBridge delivery, additive.
resource "aws_s3_bucket_notification" "cf_logs" {
  bucket      = aws_s3_bucket.cf_logs.id
  eventbridge = true
}

resource "aws_s3_bucket_ownership_controls" "cf_logs" {
  #checkov:skip=CKV2_AWS_65:BucketOwnerPreferred (ACLs enabled) is REQUIRED — CloudFront standard log delivery writes objects via ACLs. Disabling ACLs would break CloudFront access logging.
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    id     = "expire-90d"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_cloudfront_distribution" "web" {
  #checkov:skip=CKV2_AWS_42:Custom SSL certificate requires a customer-owned domain; the template uses the default *.cloudfront.net unless the customer supplies cloudfront_acm_certificate_arn at deploy. Accepted risk.
  #checkov:skip=CKV2_AWS_32:Response-headers policy is a product decision; the single-eco SPA sets security headers at the app layer. Accepted for this template.
  #checkov:skip=CKV2_AWS_47:Log4j is covered by AWSManagedRulesKnownBadInputsRuleSet on the attached Web ACL; the specific managed-rule variant Checkov expects is not required here.
  #checkov:skip=CKV_AWS_310:Origin failover is unnecessary — a single highly-available S3 static-site origin with no secondary. Accepted risk.
  #checkov:skip=CKV_AWS_374:Geo restriction is a customer product/compliance decision; the template is globally accessible by default. Accepted risk.
  enabled             = true
  default_root_object = "index.html"
  web_acl_id          = aws_wafv2_web_acl.web.arn
  aliases             = var.cloudfront_aliases
  tags                = local.tags

  # CloudFront access logging (CKV_AWS_86).
  logging_config {
    bucket          = aws_s3_bucket.cf_logs.bucket_domain_name
    prefix          = "cloudfront/"
    include_cookies = false
  }

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS Managed-CachingOptimized
  }

  # SPA: serve index.html for unknown paths.
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
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFront"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web.arn } }
    }]
  })
}

# ── Upload the /solo static export + a runtime config rendered from deployed endpoints ────
# The built single-eco frontend static site is shipped inside the exported package under
# frontend/** (the flattened /solo Next.js export). This uploads every file to the web bucket;
# a separate tessera-config.js object (rendered from module outputs below) injects the eco's
# runtime endpoints at load, so ONE build serves any eco.
locals {
  web_root  = "${path.root}/frontend"
  web_files = fileset(local.web_root, "**")
  content_types = {
    html  = "text/html"
    js    = "application/javascript"
    mjs   = "application/javascript"
    css   = "text/css"
    json  = "application/json"
    map   = "application/json"
    svg   = "image/svg+xml"
    ico   = "image/x-icon"
    png   = "image/png"
    jpg   = "image/jpeg"
    jpeg  = "image/jpeg"
    gif   = "image/gif"
    webp  = "image/webp"
    woff  = "font/woff"
    woff2 = "font/woff2"
    ttf   = "font/ttf"
    txt   = "text/plain"
    wasm  = "application/wasm"
  }
}

resource "aws_s3_object" "web" {
  for_each     = local.web_files
  bucket       = aws_s3_bucket.web.id
  key          = each.value
  source       = "${local.web_root}/${each.value}"
  etag         = filemd5("${local.web_root}/${each.value}")
  content_type = lookup(local.content_types, lower(reverse(split(".", each.value))[0]), "application/octet-stream")
}

locals {
  solo_config_js = <<-EOT
    window.__TESSERA_SOLO__ = {
      ecoId: "${var.eco_id}",
      apiUrl: "${aws_apigatewayv2_api.rest.api_endpoint}",
      wsUrl: "wss://${aws_apigatewayv2_api.ws.id}.execute-api.${var.region}.amazonaws.com/prod",
      region: "${var.region}",
      userPoolId: "${aws_cognito_user_pool.eco.id}",
      clientId: "${aws_cognito_user_pool_client.eco.id}",
      cognitoDomain: "${aws_cognito_user_pool_domain.eco.domain}.auth.${var.region}.amazoncognito.com"
    };
  EOT
}

resource "aws_s3_object" "web_config" {
  bucket       = aws_s3_bucket.web.id
  key          = "tessera-config.js"
  content      = local.solo_config_js
  content_type = "application/javascript"
  etag         = md5(local.solo_config_js)
}

# CloudFront serves the site with the Managed-CachingOptimized policy (long TTLs), so new
# uploads are NOT visible until the edge cache is invalidated. Without this, a redeploy with
# updated frontend/** uploads to S3 but users keep seeing the OLD bundle (observed: UI changes
# "didn't take" after re-export). Trigger keyed on the content hashes so it invalidates ONLY
# when the site actually changes. Requires the AWS CLI on the apply host (present wherever
# `terraform apply` runs for this module); no-op if nothing changed.
resource "terraform_data" "web_invalidation" {
  triggers_replace = {
    files  = sha1(join(",", [for k in sort(keys(aws_s3_object.web)) : aws_s3_object.web[k].etag]))
    config = aws_s3_object.web_config.etag
  }
  provisioner "local-exec" {
    command = "aws cloudfront create-invalidation --distribution-id ${aws_cloudfront_distribution.web.id} --paths '/*' --region ${var.region}"
  }
  depends_on = [aws_s3_object.web, aws_s3_object.web_config]
}
