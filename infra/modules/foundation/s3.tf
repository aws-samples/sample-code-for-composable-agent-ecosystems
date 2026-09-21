# ─────────────────────────────────────────────────────────────────────────────
# S3 buckets
#
# Names use bucket_prefix (not a fixed bucket) so they are globally unique per
# account/region without hardcoding an account id into the name. Data buckets
# (ecosystems, runs) use prevent_destroy to mirror the original RETAIN policy so a
# terraform destroy cannot silently delete user data; the access-logs bucket also
# retains. The memory-payloads bucket is ephemeral (1-day lifecycle) and safe to
# destroy.
# ─────────────────────────────────────────────────────────────────────────────

# ── Access logging bucket ────────────────────────────────────────────────────
resource "aws_s3_bucket" "access_logs" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for this single-region deployment (no DR requirement; adds a second-region bucket + replication role + cost). Accepted risk.
  #checkov:skip=CKV_AWS_145:This is the S3 server-access-logging target bucket; S3 access logging requires the log-delivery group to write, which is incompatible with SSE-KMS. Uses SSE-S3 (AES256) by design.
  bucket_prefix = "${var.name_prefix}-access-logs-"
  tags          = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 server-access logging requires the log-delivery group to write to the bucket.
resource "aws_s3_bucket_ownership_controls" "access_logs" {
  #checkov:skip=CKV2_AWS_65:BucketOwnerPreferred (ACLs enabled) is REQUIRED here — S3 server-access-logging delivery writes objects owned by the log-delivery group, which needs ACLs. Disabling ACLs would break access logging.
  bucket = aws_s3_bucket.access_logs.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = var.access_logs_expiration_days
    }
    # Clean up abandoned multipart uploads (CKV_AWS_300).
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Enforce TLS on all requests.
resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.access_logs.arn,
          "${aws_s3_bucket.access_logs.arn}/*",
        ]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AllowS3ServerAccessLogging"
        Effect    = "Allow"
        Principal = { Service = "logging.s3.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.access_logs.arn}/*"
      },
    ]
  })
}

# ── Ecosystems bucket (ecosystem YAML, domain descriptors, catalog content) ──
resource "aws_s3_bucket" "ecosystems" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for this single-region deployment (no DR requirement; adds a second-region bucket + replication role + cost). Accepted risk.
  #checkov:skip=CKV_AWS_145:Encrypted with SSE-S3 (AES256). SSE-KMS default was reverted after it broke agent-runtime + cross-service S3 I/O in the deployed account; AES256 meets the at-rest encryption requirement.
  bucket_prefix = "${var.name_prefix}-ecosystems-"
  tags          = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "ecosystems" {
  bucket                  = aws_s3_bucket.ecosystems.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "ecosystems" {
  bucket = aws_s3_bucket.ecosystems.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ecosystems" {
  bucket = aws_s3_bucket.ecosystems.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_logging" "ecosystems" {
  bucket        = aws_s3_bucket.ecosystems.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "ecosystems/"
}

# Lifecycle (CKV2_AWS_61): no object expiration (ecosystem content must persist), but
# clean up abandoned multipart uploads (CKV_AWS_300) and expire noncurrent versions to
# bound the cost of the enabled versioning.
resource "aws_s3_bucket_lifecycle_configuration" "ecosystems" {
  bucket = aws_s3_bucket.ecosystems.id
  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# Browser-side presigned PUT uploads need a CORS rule allowing PUT.
resource "aws_s3_bucket_cors_configuration" "ecosystems" {
  bucket = aws_s3_bucket.ecosystems.id
  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
  }
}

resource "aws_s3_bucket_policy" "ecosystems" {
  bucket = aws_s3_bucket.ecosystems.id
  policy = data.aws_iam_policy_document.deny_insecure_ecosystems.json
}

data "aws_iam_policy_document" "deny_insecure_ecosystems" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.ecosystems.arn, "${aws_s3_bucket.ecosystems.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# ── Runs bucket (execution run artifacts + deliverables) ──────────────────────
# No expiration lifecycle: run artifacts include user-facing deliverables that must
# persist for past-run download.
resource "aws_s3_bucket" "runs" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for this single-region deployment (no DR requirement; adds a second-region bucket + replication role + cost). Accepted risk.
  #checkov:skip=CKV_AWS_145:Encrypted with SSE-S3 (AES256). SSE-KMS default was reverted after it broke agent-runtime + cross-service S3 I/O in the deployed account; AES256 meets the at-rest encryption requirement.
  bucket_prefix = "${var.name_prefix}-runs-"
  tags          = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "runs" {
  bucket                  = aws_s3_bucket.runs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "runs" {
  bucket = aws_s3_bucket.runs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_logging" "runs" {
  bucket        = aws_s3_bucket.runs.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "runs/"
}

# Lifecycle (CKV2_AWS_61): deliberately NO object expiration — run artifacts include
# user-facing deliverables that must persist for past-run download. This rule only
# cleans up abandoned multipart uploads (CKV_AWS_300) and expires noncurrent versions
# to bound the cost of the enabled versioning.
resource "aws_s3_bucket_lifecycle_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# Run/mission attachments are uploaded from the browser via presigned PUT.
resource "aws_s3_bucket_cors_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
  }
}

resource "aws_s3_bucket_policy" "runs" {
  bucket = aws_s3_bucket.runs.id
  policy = data.aws_iam_policy_document.deny_insecure_runs.json
}

data "aws_iam_policy_document" "deny_insecure_runs" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.runs.arn, "${aws_s3_bucket.runs.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# ── Memory payloads bucket (ephemeral, self-managed strategy overflow) ────────
# Short-lived (1-day lifecycle); safe to destroy with the stack. force_destroy so
# a terraform destroy can empty it (mirrors the original auto_delete_objects).
resource "aws_s3_bucket" "memory_payloads" {
  #checkov:skip=CKV_AWS_144:Ephemeral 1-day bucket; cross-region replication is out of scope for this single-region deployment (no DR requirement). Accepted risk.
  #checkov:skip=CKV_AWS_145:Encrypted with SSE-S3 (AES256). SSE-KMS default was reverted after it broke agent-runtime + cross-service S3 I/O in the deployed account; AES256 meets the at-rest encryption requirement.
  bucket_prefix = "${var.name_prefix}-mem-payloads-"
  force_destroy = true
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "memory_payloads" {
  bucket                  = aws_s3_bucket.memory_payloads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "memory_payloads" {
  bucket = aws_s3_bucket.memory_payloads.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "memory_payloads" {
  bucket = aws_s3_bucket.memory_payloads.id
  rule {
    id     = "expire-1d"
    status = "Enabled"
    filter {}
    expiration {
      days = 1
    }
    # Clean up abandoned multipart uploads (CKV_AWS_300).
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "memory_payloads" {
  bucket = aws_s3_bucket.memory_payloads.id
  policy = data.aws_iam_policy_document.deny_insecure_memory_payloads.json
}

data "aws_iam_policy_document" "deny_insecure_memory_payloads" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.memory_payloads.arn, "${aws_s3_bucket.memory_payloads.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# ── Export bucket (productized ecosystem export packages) ─────────────────────
# Holds the generated self-serve Terraform packages the export-ecosystem Lambda
# produces (exports/<user>/<eco>/<ts>.zip). Ephemeral by design: a 7-day lifecycle
# expiration keeps storage cost bounded (packages are re-exportable on demand), and
# force_destroy lets a terraform destroy empty it with the stack. The download UI
# tells users the package is available for 7 days, then auto-deleted.
resource "aws_s3_bucket" "exports" {
  #checkov:skip=CKV_AWS_144:Ephemeral 7-day export packages; cross-region replication is out of scope for this single-region deployment (no DR requirement). Accepted risk.
  #checkov:skip=CKV_AWS_145:Encrypted with SSE-S3 (AES256). SSE-KMS default was reverted after it broke agent-runtime + cross-service S3 I/O in the deployed account; AES256 meets the at-rest encryption requirement.
  bucket_prefix = "${var.name_prefix}-exports-"
  force_destroy = true
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "exports" {
  bucket                  = aws_s3_bucket.exports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "exports" {
  bucket = aws_s3_bucket.exports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_logging" "exports" {
  bucket        = aws_s3_bucket.exports.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "exports/"
}

resource "aws_s3_bucket_lifecycle_configuration" "exports" {
  bucket = aws_s3_bucket.exports.id
  rule {
    id     = "expire-7d"
    status = "Enabled"
    filter {}
    expiration {
      days = var.export_expiration_days
    }
    # Clean up abandoned multipart uploads (CKV_AWS_300).
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "exports" {
  bucket = aws_s3_bucket.exports.id
  policy = data.aws_iam_policy_document.deny_insecure_exports.json
}

data "aws_iam_policy_document" "deny_insecure_exports" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.exports.arn, "${aws_s3_bucket.exports.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# ── S3 event notifications (CKV2_AWS_62) ──────────────────────────────────────
# Enable EventBridge delivery on every bucket so object events are observable (audit /
# future automation) without wiring a specific consumer. This is additive and does not
# change any existing data path.
resource "aws_s3_bucket_notification" "access_logs" {
  bucket      = aws_s3_bucket.access_logs.id
  eventbridge = true
}

resource "aws_s3_bucket_notification" "ecosystems" {
  bucket      = aws_s3_bucket.ecosystems.id
  eventbridge = true
}

resource "aws_s3_bucket_notification" "runs" {
  bucket      = aws_s3_bucket.runs.id
  eventbridge = true
}

resource "aws_s3_bucket_notification" "memory_payloads" {
  bucket      = aws_s3_bucket.memory_payloads.id
  eventbridge = true
}

resource "aws_s3_bucket_notification" "exports" {
  bucket      = aws_s3_bucket.exports.id
  eventbridge = true
}
