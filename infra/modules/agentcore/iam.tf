# ─────────────────────────────────────────────────────────────────────────────
# IAM roles for AgentCore Memory and Gateway.
# ─────────────────────────────────────────────────────────────────────────────

# Trust policy for the MEMORY execution role. AgentCore requires it to be scoped to
# memory ARNs via aws:SourceArn — CreateMemory validation assumes the role to verify
# SNS/S3 access and rejects a role without this condition.
data "aws_iam_policy_document" "memory_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:memory/*"]
    }
  }
}

# Trust policy for the GATEWAY service role (not scoped to memory ARNs).
data "aws_iam_policy_document" "agentcore_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

# ── Memory execution role ─────────────────────────────────────────────────────
# The Memory extraction/reflection pipelines need Bedrock model access (reasoning +
# embeddings), CloudWatch Logs, and — for the self-managed strategy — S3 payload
# delivery + SNS publish (even though the trigger pipeline is not used, the grants
# must exist for UpdateMemory validation to pass).
resource "aws_iam_role" "memory_execution" {
  name               = "${var.name_prefix}-memory-execution"
  assume_role_policy = data.aws_iam_policy_document.memory_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "memory_execution" {
  name = "grants"
  role = aws_iam_role.memory_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockModels"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:${var.partition}:bedrock:*::foundation-model/anthropic.claude*",
          "arn:${var.partition}:bedrock:*::foundation-model/amazon.titan-embed*",
          "arn:${var.partition}:bedrock:*::foundation-model/cohere.embed*",
          "arn:${var.partition}:bedrock:*:${var.account_id}:inference-profile/*",
        ]
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/*",
          "arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/*:*",
        ]
      },
      {
        Sid      = "PayloadBucket"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation", "s3:PutObject"]
        Resource = [var.memory_payloads_bucket_arn, "${var.memory_payloads_bucket_arn}/*"]
      },
      {
        Sid      = "JobsTopic"
        Effect   = "Allow"
        Action   = ["sns:GetTopicAttributes", "sns:Publish"]
        Resource = aws_sns_topic.memory_jobs.arn
      },
      {
        # The jobs topic is SSE-encrypted (alias/aws/sns), so publishing to it requires
        # KMS. Scoped to the SNS service in this region via kms:ViaService (the AWS-owned
        # aws/sns key can't be referenced by a stable ARN, so resource is "*" + condition).
        Sid      = "SnsKms"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "sns.${var.region}.amazonaws.com" }
        }
      },
    ]
  })
}

# ── Gateway service role (outbound auth — invokes Lambda tool targets) ────────
resource "aws_iam_role" "gateway" {
  name               = "${var.name_prefix}-gateway"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "gateway" {
  name = "grants"
  role = aws_iam_role.gateway.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-tool-*"
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/*",
          "arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/*:*",
        ]
      },
    ]
  })
}

# SNS topic used by the self-managed memory strategy config (trigger pipeline not used).
resource "aws_sns_topic" "memory_jobs" {
  name              = "${var.name_prefix}-memory-jobs"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}
