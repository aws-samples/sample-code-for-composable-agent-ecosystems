# ─────────────────────────────────────────────────────────────────────────────
# IAM — one execution role per Lambda, with a shared baseline (logs, KMS, DLQ, VPC)
# plus per-function least-privilege inline policies (defined further below).
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each           = local.resolved_functions
  name               = "${var.name_prefix}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

# Baseline: CloudWatch Logs, KMS (env decrypt), DLQ send, and VPC ENI management for
# VPC-attached functions.
data "aws_iam_policy_document" "baseline" {
  for_each = local.resolved_functions

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${var.name_prefix}-${each.key}:*"]
  }

  statement {
    sid       = "KmsEnv"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.lambda_env_key_arn, var.data_key_arn]
  }

  statement {
    sid       = "Dlq"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [var.lambda_dlq_arn]
  }

  dynamic "statement" {
    for_each = each.value.vpc ? [1] : []
    content {
      sid    = "VpcEni"
      effect = "Allow"
      actions = [
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses",
      ]
      resources = ["*"] # EC2 ENI actions do not support resource-level scoping
    }
  }
}

resource "aws_iam_role_policy" "baseline" {
  for_each = local.resolved_functions
  name     = "baseline"
  role     = aws_iam_role.lambda[each.key].id
  policy   = data.aws_iam_policy_document.baseline[each.key].json
}

# ── Reusable policy fragments ─────────────────────────────────────────────────
locals {
  bedrock_claude_resources = [
    "arn:${var.partition}:bedrock:*::foundation-model/anthropic.claude*",
    "arn:${var.partition}:bedrock:*::foundation-model/us.anthropic.claude*",
    "arn:${var.partition}:bedrock:${var.region}:${var.account_id}:inference-profile/us.anthropic.claude*",
  ]

  sfn_resources = [
    "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*",
    "arn:${var.partition}:states:${var.region}:${var.account_id}:execution:${var.name_prefix}-*:*",
  ]

  agent_runtime_resources = var.agent_runtime_arn != "" ? [
    var.agent_runtime_arn,
    "${var.agent_runtime_arn}/runtime-endpoint/DEFAULT",
  ] : ["*"]

  websearch_identity_resources = [
    "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:credential-provider/${var.project}-*",
    "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:workload-identity-directory/default",
    "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:workload-identity-directory/default/workload-identity/${var.name_prefix}-*",
    "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:token-vault/default",
    "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:token-vault/default/apikeycredentialprovider/${var.name_prefix}-*",
  ]

  agentcore_secret_arn = "arn:${var.partition}:secretsmanager:${var.region}:${var.account_id}:secret:bedrock-agentcore-identity*" # pragma: allowlist secret — Secrets Manager ARN pattern from variables, not a secret value
}

# ── Step Functions execution role ─────────────────────────────────────────────
# Ecosystem state machines (compiled at runtime by save-ecosystem) assume this role
# to invoke the agent-invoker, grounding-check, and external-grounding Lambdas.
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "sfn_invoke" {
  name = "invoke-lambdas"
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = compact([
          try(aws_lambda_function.this["agent-invoker"].arn, ""),
          try(aws_lambda_function.this["grounding-check"].arn, ""),
          try(aws_lambda_function.this["external-grounding"].arn, ""),
        ])
      },
      # Evaluation harness: the per-eco Eval State Machine (compiled at runtime by
      # save-ecosystem) also assumes THIS role. It invokes the eval Lambdas (owned by the
      # eval module, referenced by deterministic name to avoid a module cycle) and drives
      # the target run SM via startExecution.sync.
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-eval-*"
      },
      {
        Effect = "Allow"
        Action = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution"]
        Resource = [
          "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*",
          "arn:${var.partition}:states:${var.region}:${var.account_id}:execution:${var.name_prefix}-*:*",
        ]
      },
      # startExecution.sync requires the managed EventBridge rule that relays child SFN
      # completion back to the parent.
      {
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:${var.partition}:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule"
      },
    ]
  })
}


