# ─────────────────────────────────────────────────────────────────────────────
# Ecosystem Evaluation Harness (docs/spec-ecosystem-evaluation.md).
#
# Terraform port of the original CDK `tessera-<env>-eval` stack. Creates the 5 eval
# Lambdas (deterministic names so the platform side can grant/point to them by name —
# NO cross-module refs from the platform's SFN role, mirroring the original), the Bedrock
# Evaluations (BYOI) role, and the EventBridge rule that fires online-eval when any run
# state machine SUCCEEDS.
#
# The per-ecosystem Eval State Machine is NOT created here — it is compiled at runtime by
# save-ecosystem (tessera_platform.evaluation.eval_compiler) when an ecosystem declares an
# `evaluation:` block. This module only provides the Lambdas + role + trigger those SMs use.
#
# The shared layer (tessera_platform.*) is created once in the compute module; its ARN is
# passed in (var.shared_layer_arn) — the eval Lambdas attach it rather than rebuilding it.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # function suffix -> {memory, timeout, extra_env}. Function name is
  # tessera-<env>-eval-<suffix>, EXCEPT online -> tessera-<env>-online-eval, matching the
  # ARNs the platform stack grants/points to in the original.
  fns = {
    "eval-runner"     = { memory = 1024, timeout = 300, name = "${var.name_prefix}-eval-runner" }
    "eval-aggregator" = { memory = 512, timeout = 300, name = "${var.name_prefix}-eval-aggregator" }
    "eval-judge"      = { memory = 768, timeout = 300, name = "${var.name_prefix}-eval-judge" }
    "eval-trajectory" = { memory = 768, timeout = 300, name = "${var.name_prefix}-eval-trajectory" }
    "online-eval"     = { memory = 512, timeout = 120, name = "${var.name_prefix}-online-eval" }
  }

  base_env = {
    ECOSYSTEMS_BUCKET   = var.ecosystems_bucket_name
    TESSERA_RUNS_BUCKET = var.runs_bucket_name
  }
}

# ── Bedrock Evaluations (BYOI) role — Bedrock assumes it to read the dataset + write
#    results (spec §7.1/§7.2). Passed to eval-judge as BEDROCK_EVAL_ROLE_ARN. ──
data "aws_iam_policy_document" "bedrock_eval_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "bedrock_eval" {
  name               = "${var.name_prefix}-bedrock-eval"
  assume_role_policy = data.aws_iam_policy_document.bedrock_eval_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "bedrock_eval" {
  name = "grants"
  role = aws_iam_role.bedrock_eval.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
        Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"]
      },
      {
        Effect = "Allow",
        Action = ["bedrock:InvokeModel"],
        Resource = [
          "arn:${var.partition}:bedrock:*::foundation-model/anthropic.claude*",
          "arn:${var.partition}:bedrock:*::foundation-model/us.anthropic.claude*",
          "arn:${var.partition}:bedrock:*::foundation-model/us.amazon.nova*",
          "arn:${var.partition}:bedrock:*::foundation-model/us.meta.llama*",
          "arn:${var.partition}:bedrock:${var.region}:${var.account_id}:inference-profile/us.*",
        ]
      },
    ]
  })
}

# ── Trigger role + inline policies (per function) ─────────────────────────────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.fns
  name               = each.value.name
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.fns
  name     = "grants"
  role     = aws_iam_role.fn[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${each.value.name}:*" },
        # S3 to both buckets (read eco YAML from ecosystems; read/write scorecards +
        # golden sets + judge datasets + drift/candidate records in runs).
        { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"], Resource = [
          var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*",
          var.runs_bucket_arn, "${var.runs_bucket_arn}/*",
        ] },
        # KMS: decrypt/encrypt Lambda env vars (CKV_AWS_173).
        { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = var.lambda_env_key_arn },
        # DLQ: deliver failed async invocations (CKV_AWS_116).
        { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = var.lambda_dlq_arn },
      ],
      # eval-judge: submit Bedrock eval jobs + pass the Bedrock eval role.
      each.key == "eval-judge" ? [
        { Effect = "Allow", Action = ["bedrock:CreateEvaluationJob", "bedrock:GetEvaluationJob", "bedrock:ListEvaluationJobs"], Resource = "*" },
        { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.bedrock_eval.arn },
      ] : [],
      # eval-trajectory: AgentCore Evaluations (best-effort scope).
      each.key == "eval-trajectory" ? [
        { Effect = "Allow", Action = ["bedrock-agentcore:CreateEvaluation", "bedrock-agentcore:GetEvaluation", "bedrock-agentcore:ListEvaluations"], Resource = "*" },
      ] : [],
    )
  })
}

# ── Lambda functions ──────────────────────────────────────────────────────────
# Prefer the prebuilt zip (scripts/package-lambdas.sh); fall back to a source-only
# archive so plan/validate work before the packaging script has run.
data "archive_file" "src" {
  for_each    = local.fns
  type        = "zip"
  source_dir  = "${var.lambdas_src_dir}/${each.key}"
  output_path = "${path.module}/.src-archives/${each.key}.zip"
  excludes    = ["__pycache__", "requirements.txt"]
}

resource "aws_lambda_function" "fn" {
  #checkov:skip=CKV_AWS_115:Reserved (function-level) concurrency is intentionally unset — eval functions are invoked on run completion and must scale with run volume; a reserved limit would throttle evaluations. Accepted risk.
  #checkov:skip=CKV_AWS_117:Eval functions run outside the VPC to reach Bedrock Evaluations + S3 without NAT, matching the platform split. VPC attachment would add NAT cost/cold-start with no benefit. Accepted risk.
  #checkov:skip=CKV_AWS_272:Code-signing requires a Signer profile + signed artifacts (pipeline change out of scope). Artifacts are integrity-checked via source hashes. Accepted risk.
  for_each = local.fns

  function_name    = each.value.name
  role             = aws_iam_role.fn[each.key].arn
  runtime          = var.lambda_runtime
  handler          = "handler.handler"
  filename         = fileexists("${var.build_dir}/${each.key}.zip") ? "${var.build_dir}/${each.key}.zip" : data.archive_file.src[each.key].output_path
  source_code_hash = fileexists("${var.build_dir}/${each.key}.zip") ? filebase64sha256("${var.build_dir}/${each.key}.zip") : data.archive_file.src[each.key].output_base64sha256
  timeout          = each.value.timeout
  memory_size      = min(each.value.memory, var.lambda_max_memory)
  layers           = [var.shared_layer_arn]
  kms_key_arn      = var.lambda_env_key_arn # CMK env encryption (CKV_AWS_173)

  dead_letter_config {
    target_arn = var.lambda_dlq_arn # (CKV_AWS_116)
  }

  environment {
    variables = merge(
      local.base_env,
      each.key == "eval-judge" ? { BEDROCK_EVAL_ROLE_ARN = aws_iam_role.bedrock_eval.arn } : {},
    )
  }

  # Active X-Ray tracing (CKV_AWS_50).
  tracing_config {
    mode = "Active"
  }

  tags = var.tags
}

# ── Online eval trigger (spec §7.4, decision D — ASYNC, zero run latency):
#    fire online-eval when ANY per-eco run state machine SUCCEEDS. The handler
#    self-normalises the SFN envelope, no-ops for non-opted-in ecos, and skips eval SMs. ──
resource "aws_cloudwatch_event_rule" "online_eval" {
  name = "${var.name_prefix}-online-eval"
  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status          = ["SUCCEEDED"]
      stateMachineArn = [{ prefix = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-" }]
    }
  })
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "online_eval" {
  rule      = aws_cloudwatch_event_rule.online_eval.name
  target_id = "online-eval"
  arn       = aws_lambda_function.fn["online-eval"].arn
}

resource "aws_lambda_permission" "online_eval_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["online-eval"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.online_eval.arn
}
