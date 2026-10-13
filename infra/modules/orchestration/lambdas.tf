# ─────────────────────────────────────────────────────────────────────────────
# Ingest / catalog / quorum runtime Lambdas.
#
# These run OUTSIDE the VPC (default internet egress) so they can fetch public source
# feeds and reach Bedrock/AgentCore without NAT. Each gets a role with a baseline
# (logs/kms/dlq) plus its specific grants. The Memory ARN is empty on the first apply
# (AgentCore module wires it later); grants that need it are gated on memory_arn != "".
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

locals {
  memory_resources = var.memory_arn != "" ? [var.memory_arn, "${var.memory_arn}/*"] : []

  memory_rw_actions = [
    "bedrock-agentcore:GetMemory",
    "bedrock-agentcore:BatchCreateMemoryRecords",
    "bedrock-agentcore:BatchDeleteMemoryRecords",
    "bedrock-agentcore:ListMemoryRecords",
    "bedrock-agentcore:RetrieveMemoryRecords",
  ]

  # Per-Lambda spec: memory (MB), timeout (s), env.
  fn_spec = {
    "catalog-ingest" = {
      memory = 2048, timeout = 900
      env    = { CATALOG_REFRESH_TABLE = var.catalog_refresh_table_name }
    }
    "catalog-ingest-generic" = {
      memory = 1024, timeout = 900
      env = {
        TESSERA_DOMAINS_BUCKET = var.ecosystems_bucket_name
        CATALOG_REFRESH_TABLE  = var.catalog_refresh_table_name
        CATALOG_CONTENT_BUCKET = var.ecosystems_bucket_name
      }
    }
    "catalog-api" = {
      memory = 512, timeout = 30
      env = {
        COGNITO_USER_POOL_ID            = var.cognito_user_pool_id
        COGNITO_CLIENT_ID               = var.cognito_client_id
        ALLOWED_ORIGIN                  = var.allowed_origin
        ECOSYSTEMS_BUCKET               = var.ecosystems_bucket_name
        CATALOG_DOMAIN                  = var.default_catalog_domain
        CATALOG_REFRESH_TABLE           = var.catalog_refresh_table_name
        CATALOG_CONTENT_BUCKET          = var.ecosystems_bucket_name
        CATALOG_INGEST_FUNCTION         = "${var.name_prefix}-catalog-ingest"
        CATALOG_GENERIC_INGEST_FUNCTION = "${var.name_prefix}-catalog-ingest-generic"
        CATALOG_AGENT_STATE_MACHINE_ARN = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-catalog-agent-ingest"
        CATALOG_BUILD_SM_PREFIX         = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-catalog-build-"
        CATALOG_GENERIC_BUILD_SM_ARN    = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-catalog-build"
        # Generic refresh SM (domain from input) — powers the "Refresh from sources" button
        # in the catalog Admin tab for every domain. Matches the original CDK wiring.
        CATALOG_REFRESH_STATE_MACHINE_ARN = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-catalog-refresh"
      }
    }
    "catalog-write" = {
      memory = 512, timeout = 60
      env    = { CATALOG_CONTENT_BUCKET = var.ecosystems_bucket_name }
    }
    "catalog-regrounding" = {
      memory = 512, timeout = 120
      env = {
        CATALOG_REFRESH_TABLE = var.catalog_refresh_table_name
        ECOSYSTEMS_BUCKET     = var.ecosystems_bucket_name
      }
    }
    "interactive-invoke" = {
      memory = 512, timeout = 120
      env = {
        CATALOG_REFRESH_TABLE = var.catalog_refresh_table_name
        ECOSYSTEMS_BUCKET     = var.ecosystems_bucket_name
        INTERACTIVE_MODEL_ID  = var.models["interactive"]
        INTERACTIVE_SELF_FN   = "${var.name_prefix}-interactive-invoke"
      }
    }
    "concierge-ws" = {
      memory = 1024, timeout = 300
      env = {
        CATALOG_REFRESH_TABLE = var.catalog_refresh_table_name
        ECOSYSTEMS_BUCKET     = var.ecosystems_bucket_name
        TESSERA_RUNS_BUCKET   = var.runs_bucket_name
        COGNITO_USER_POOL_ID  = var.cognito_user_pool_id
        COGNITO_CLIENT_ID     = var.cognito_client_id
        CONCIERGE_MODEL_ID    = var.models["concierge"]
      }
    }
    "quorum-run" = {
      memory = 512, timeout = 900
      env = {
        RUNS_BUCKET           = var.runs_bucket_name
        ECOSYSTEMS_BUCKET     = var.ecosystems_bucket_name
        CONNECTIONS_TABLE     = var.connections_table_name
        WS_ENDPOINT           = var.websocket_callback_url
        QUORUM_ENABLED        = "true"
        WHITEBOARD_ENABLED    = "true"
        QUORUM_MODEL_ID       = var.models["quorum"]
        # Needed so from_catalog() can resolve the active scrutineer catalog version
        # (pk=DOMAIN#scrutineer sk=ACTIVE) and read the promoted versioned namespace;
        # without it the live persona pool reads empty and the panel can't be convened.
        CATALOG_REFRESH_TABLE = var.catalog_refresh_table_name
      }
    }
  }

  # quorum-run is only deployed when quorum is enabled.
  active_lambdas = var.enable_quorum ? local.owned_lambdas : [for l in local.owned_lambdas : l if l != "quorum-run"]

  common_env = {
    TESSERA_MEMORY_ID = var.memory_id
  }
}

resource "aws_iam_role" "fn" {
  for_each           = toset(local.active_lambdas)
  name               = "${var.name_prefix}-${each.value}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "baseline" {
  for_each = toset(local.active_lambdas)

  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${var.name_prefix}-${each.value}:*"]
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
}

resource "aws_iam_role_policy" "baseline" {
  for_each = toset(local.active_lambdas)
  name     = "baseline"
  role     = aws_iam_role.fn[each.value].id
  policy   = data.aws_iam_policy_document.baseline[each.value].json
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = toset(local.active_lambdas)
  name              = "/aws/lambda/${var.name_prefix}-${each.value}"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn                 # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

resource "aws_lambda_function" "fn" {
  #checkov:skip=CKV_AWS_115:Reserved (function-level) concurrency is intentionally unset — these ingest/catalog runtimes must scale on demand; a reserved limit would throttle builds. Account-level concurrency governs blast radius. Accepted risk.
  #checkov:skip=CKV_AWS_117:These functions run OUTSIDE the VPC by design (documented above) so they can fetch public source feeds and reach Bedrock/AgentCore without NAT. VPC attachment would break ingest + add NAT cost/cold-start. Accepted risk.
  #checkov:skip=CKV_AWS_272:Code-signing requires a Signer profile + signed artifacts (pipeline change out of scope). Artifacts are integrity-checked via source_code_hash. Accepted risk.
  for_each = toset(local.active_lambdas)

  function_name    = "${var.name_prefix}-${each.value}"
  role             = aws_iam_role.fn[each.value].arn
  runtime          = var.lambda_runtime
  handler          = "handler.handler"
  filename         = local.package[each.value].filename
  source_code_hash = local.package[each.value].hash
  timeout          = local.fn_spec[each.value].timeout
  memory_size      = min(local.fn_spec[each.value].memory, var.lambda_max_memory)
  layers           = [var.shared_layer_arn]
  kms_key_arn      = var.lambda_env_key_arn

  dead_letter_config {
    target_arn = var.lambda_dlq_arn
  }

  environment {
    variables = merge(local.common_env, local.fn_spec[each.value].env)
  }

  # Active X-Ray tracing (CKV_AWS_50).
  tracing_config {
    mode = "Active"
  }

  tags       = var.tags
  depends_on = [aws_cloudwatch_log_group.fn]
}
