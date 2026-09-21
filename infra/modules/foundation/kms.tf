# ─────────────────────────────────────────────────────────────────────────────
# KMS keys
# ─────────────────────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  # Scoped root-account KMS administration actions — the key-management operations the
  # account admin needs, WITHOUT the kms:* wildcard (least privilege). Excludes data-plane
  # Encrypt/Decrypt/GenerateDataKey, which are granted to service principals / IAM roles.
  kms_admin_actions = [
    "kms:Create*",
    "kms:Describe*",
    "kms:Enable*",
    "kms:List*",
    "kms:Put*",
    "kms:Update*",
    "kms:Revoke*",
    "kms:Disable*",
    "kms:Get*",
    "kms:Delete*",
    "kms:TagResource",
    "kms:UntagResource",
    "kms:ScheduleKeyDeletion",
    "kms:CancelKeyDeletion",
  ]
}

# Encrypts Lambda environment variables.
resource "aws_kms_key" "lambda_env" {
  description             = "Encrypts Lambda environment variables"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  tags                    = var.tags

  # Explicit key policy (CKV2_AWS_64). Scoped admin actions for the root account, plus a
  # data-plane grant so the Lambda service can encrypt/decrypt function env vars on the
  # account's behalf (scoped by kms:ViaService = lambda — no wildcard action).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = local.kms_admin_actions
        Resource  = "*"
      },
      {
        Sid       = "AllowLambdaEnvCrypto"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant"]
        Resource  = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "lambda.${local.region}.amazonaws.com" }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "lambda_env" {
  name          = "alias/${var.name_prefix}-lambda-env"
  target_key_id = aws_kms_key.lambda_env.key_id
}

# Encrypts CloudWatch Log Groups. The key policy grants the CloudWatch Logs
# service principal use of the key. resources = ["*"] in a KMS key policy is
# self-referential — it means "this key" — which is the AWS-required pattern for
# service-principal grants and cannot be scoped further.
resource "aws_kms_key" "logs" {
  description             = "Encrypts CloudWatch Log Groups"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  tags                    = var.tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = local.kms_admin_actions
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_kms_alias" "logs" {
  name          = "alias/${var.name_prefix}-logs"
  target_key_id = aws_kms_key.logs.key_id
}

# General-purpose customer-managed key for at-rest data services: S3 (SSE-KMS),
# DynamoDB, Secrets Manager, ECR. Satisfies CKV_AWS_145/119/149/136. The key policy
# grants the root account (admin) and lets the relevant AWS service principals use the
# key on behalf of resources in this account (scoped by the kms:ViaService condition),
# which is the AWS-recommended pattern and keeps service integrations working.
resource "aws_kms_key" "data" {
  description             = "Encrypts data at rest (S3, DynamoDB, Secrets Manager, ECR)"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  tags                    = var.tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = local.kms_admin_actions
        Resource  = "*"
      },
      {
        Sid       = "AllowServiceUseViaAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:CreateGrant",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = [
              "s3.${local.region}.amazonaws.com",
              "dynamodb.${local.region}.amazonaws.com",
              "secretsmanager.${local.region}.amazonaws.com",
              "ecr.${local.region}.amazonaws.com",
            ]
          }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${var.name_prefix}-data"
  target_key_id = aws_kms_key.data.key_id
}
