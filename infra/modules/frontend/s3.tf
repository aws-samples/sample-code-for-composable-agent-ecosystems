# ─────────────────────────────────────────────────────────────────────────────
# Frontend site bucket. Private (all public access blocked); served only through
# CloudFront via Origin Access Control. force_destroy so a teardown can empty it
# (the site content is a rebuildable static export).
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "frontend" {
  #checkov:skip=CKV_AWS_144:Rebuildable static-site content; cross-region replication is out of scope for this single-region deployment (no DR requirement). Accepted risk.
  #checkov:skip=CKV_AWS_145:Encrypted with SSE-S3 (AES256). SSE-KMS default was reverted after it broke cross-service S3 I/O in the deployed account; AES256 meets the at-rest encryption requirement.
  bucket_prefix = "${var.name_prefix}-frontend-"
  force_destroy = true
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_logging" "frontend" {
  bucket        = aws_s3_bucket.frontend.id
  target_bucket = var.access_logs_bucket_id
  target_prefix = "frontend/"
}

# Lifecycle (CKV2_AWS_61): the site content is a rebuildable static export, so no object
# expiration — just clean up abandoned multipart uploads (CKV_AWS_300) and expire
# noncurrent versions to bound the cost of the enabled versioning.
resource "aws_s3_bucket_lifecycle_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# S3 event notification (CKV2_AWS_62): EventBridge delivery, additive.
resource "aws_s3_bucket_notification" "frontend" {
  bucket      = aws_s3_bucket.frontend.id
  eventbridge = true
}

# Bucket policy: allow only this CloudFront distribution (via OAC) to read objects.
resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCloudFrontOAC"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.frontend.arn}/*"
        Condition = {
          StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.frontend.arn, "${aws_s3_bucket.frontend.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}
