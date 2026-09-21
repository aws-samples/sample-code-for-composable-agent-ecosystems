// terraform-tessera-eco :: bootstrap (create-or-reference infra)
//
// When the create_* toggles are true, the module stands up dedicated infra so the ecosystem
// is deployable in a bare account. When false, it references caller-supplied ids/ARNs (the
// bring-your-own-landing-zone model, §7). `locals` in main.tf resolve create-vs-reference.

data "aws_availability_zones" "azs" {
  state = "available"
}

data "aws_caller_identity" "current" {}

// ── VPC (create_networking=true) ──────────────────────────────────────────────────────
resource "aws_vpc" "this" {
  #checkov:skip=CKV2_AWS_11:VPC flow logs ARE created (aws_flow_log.this + flow_logs log group/role below), but they are count-gated to match this count-gated VPC and Checkov cannot statically link two count-gated resources. False positive.
  #checkov:skip=CKV2_AWS_12:The default security group IS managed as deny-all (aws_default_security_group.this below), count-gated to match this VPC; Checkov cannot link the two count-gated resources. False positive.
  count                = var.create_networking ? 1 : 0
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${local.name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  count  = var.create_networking ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = merge(local.tags, { Name = "${local.name}-igw" })
}

// Default security group: deny all (CKV2_AWS_12). Managing it with no rules ensures nothing
// inadvertently uses the VPC's default SG; workloads use the explicit egress-only SG.
resource "aws_default_security_group" "this" {
  count  = var.create_networking ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  tags   = merge(local.tags, { Name = "${local.name}-default-sg-deny-all" })
}

// VPC flow logs (CKV2_AWS_11) → encrypted CloudWatch log group.
resource "aws_cloudwatch_log_group" "flow_logs" {
  count             = var.create_networking ? 1 : 0
  name              = "/aws/vpc/${local.name}-flow-logs"
  retention_in_days = max(var.log_retention_days, 365) // >= 1 year (CKV_AWS_338)
  kms_key_id        = local.kms_key_arn                // key policy grants logs.<region>.amazonaws.com
  tags              = local.tags
}

data "aws_iam_policy_document" "flow_logs_assume" {
  count = var.create_networking ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  count              = var.create_networking ? 1 : 0
  name               = "${local.name}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy" "flow_logs" {
  count = var.create_networking ? 1 : 0
  name  = "flow-logs"
  role  = aws_iam_role.flow_logs[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow_logs[0].arn}:*"
    }]
  })
}

resource "aws_flow_log" "this" {
  count                    = var.create_networking ? 1 : 0
  vpc_id                   = aws_vpc.this[0].id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs[0].arn
  iam_role_arn             = aws_iam_role.flow_logs[0].arn
  max_aggregation_interval = 600
  tags                     = merge(local.tags, { Name = "${local.name}-vpc-flow-logs" })
}

resource "aws_subnet" "public" {
  count             = var.create_networking ? 2 : 0
  vpc_id            = aws_vpc.this[0].id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = data.aws_availability_zones.azs.names[count.index]
  tags              = merge(local.tags, { Name = "${local.name}-public-${count.index}" })
}

resource "aws_subnet" "private" {
  count             = var.create_networking ? 2 : 0
  vpc_id            = aws_vpc.this[0].id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  availability_zone = data.aws_availability_zones.azs.names[count.index]
  tags              = merge(local.tags, { Name = "${local.name}-private-${count.index}" })
}

resource "aws_eip" "nat" {
  #checkov:skip=CKV2_AWS_19:This EIP IS attached — it is associated with the count-gated NAT gateway (aws_nat_gateway.this) below; Checkov cannot link the two count-gated resources. False positive.
  count  = var.create_networking ? 1 : 0
  domain = "vpc"
  tags   = local.tags
}

resource "aws_nat_gateway" "this" {
  count         = var.create_networking ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.tags, { Name = "${local.name}-nat" })
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  count  = var.create_networking ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }
  tags = merge(local.tags, { Name = "${local.name}-public-rt" })
}

resource "aws_route_table" "private" {
  count  = var.create_networking ? 1 : 0
  vpc_id = aws_vpc.this[0].id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this[0].id
  }
  tags = merge(local.tags, { Name = "${local.name}-private-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.create_networking ? 2 : 0
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table_association" "private" {
  count          = var.create_networking ? 2 : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

// ── KMS (create_kms=true) ──────────────────────────────────────────────────────────────
resource "aws_kms_key" "this" {
  count                   = var.create_kms ? 1 : 0
  description             = "${local.name} encryption key"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  tags                    = local.tags
  # Grant CloudWatch Logs use of the key (required to encrypt the Lambda log groups); root
  # keeps IAM-delegated admin. Reference-mode customers must grant logs on their own key.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:*"
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "this" {
  count         = var.create_kms ? 1 : 0
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.this[0].key_id
}

// ── Artifacts bucket (create_artifacts_bucket=true) — holds the Lambda zips ────────────
resource "aws_s3_bucket" "artifacts" {
  #checkov:skip=CKV2_AWS_6:A public-access block IS created (aws_s3_bucket_public_access_block.artifacts), count-gated to match this bucket; Checkov cannot link the two count-gated resources. False positive.
  #checkov:skip=CKV2_AWS_61:A lifecycle configuration IS created (aws_s3_bucket_lifecycle_configuration.artifacts), count-gated to match this bucket; Checkov cannot link the two count-gated resources. False positive.
  #checkov:skip=CKV2_AWS_62:An event notification IS created (aws_s3_bucket_notification.artifacts), count-gated to match this bucket; Checkov cannot link the two count-gated resources. False positive.
  #checkov:skip=CKV_AWS_144:Re-uploadable Lambda artifacts; cross-region replication is out of scope for this single-region customer template (no DR requirement). Accepted risk.
  #checkov:skip=CKV_AWS_145:Encrypted with SSE-S3 by default; the customer supplies their own CMK via create_kms/kms_key_arn for data buckets. AES256 meets the at-rest encryption requirement for build artifacts. Accepted risk.
  count         = var.create_artifacts_bucket ? 1 : 0
  bucket        = "${local.name}-artifacts"
  force_destroy = var.force_destroy_data
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  count                   = var.create_artifacts_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle (CKV2_AWS_61): the artifacts (Lambda zips) are re-uploadable, so just clean up
# abandoned multipart uploads (CKV_AWS_300).
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  count  = var.create_artifacts_bucket ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id
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
resource "aws_s3_bucket_notification" "artifacts" {
  count       = var.create_artifacts_bucket ? 1 : 0
  bucket      = aws_s3_bucket.artifacts[0].id
  eventbridge = true
}

// ── ECR (create_ecr=true) — runtime image repo; build-and-push script pushes here ──────
resource "aws_ecr_repository" "runtime" {
  count                = var.create_ecr ? 1 : 0
  name                 = "${local.name}-runtime"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.force_destroy_data # teardown: remove repo even with images (spec §14.1)
  image_scanning_configuration {
    scan_on_push = true
  }
  # Customer-managed KMS encryption (CKV_AWS_136).
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = local.kms_key_arn
  }
  tags = local.tags
}
