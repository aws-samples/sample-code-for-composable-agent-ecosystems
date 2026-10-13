# ─────────────────────────────────────────────────────────────────────────────
# Per-Lambda grants for the orchestration/ingest/quorum functions.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  bedrock_all_models = [
    "arn:${var.partition}:bedrock:*::foundation-model/*",
    "arn:${var.partition}:bedrock:*:${var.account_id}:inference-profile/*",
  ]
}

# catalog-ingest: refresh table rw + memory rw + catalog-content S3 + weekly schedule
resource "aws_iam_role_policy" "catalog_ingest" {
  name = "grants"
  role = aws_iam_role.fn["catalog-ingest"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = [var.catalog_refresh_table_arn, "${var.catalog_refresh_table_arn}/index/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${var.ecosystems_bucket_arn}/catalog-content/*"] },
      ], length(local.memory_resources) > 0 ? [
      { Effect = "Allow", Action = local.memory_rw_actions, Resource = local.memory_resources },
    ] : [])
  })
}

# catalog-ingest-generic: refresh rw + memory rw + bedrock + domains/catalog-content S3
resource "aws_iam_role_policy" "catalog_ingest_generic" {
  name = "grants"
  role = aws_iam_role.fn["catalog-ingest-generic"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = [var.catalog_refresh_table_arn, "${var.catalog_refresh_table_arn}/index/*"] },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = local.bedrock_all_models },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.ecosystems_bucket_arn}/domains/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${var.ecosystems_bucket_arn}/catalog-content/*"] },
      ], length(local.memory_resources) > 0 ? [
      { Effect = "Allow", Action = local.memory_rw_actions, Resource = local.memory_resources },
    ] : [])
  })
}

# catalog-api: memory read + refresh rw + domains S3 + SFN start (build/refresh/agent) + invoke ingest
resource "aws_iam_role_policy" "catalog_api" {
  name = "grants"
  role = aws_iam_role.fn["catalog-api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = [var.catalog_refresh_table_arn, "${var.catalog_refresh_table_arn}/index/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = ["${var.ecosystems_bucket_arn}/domains/*"] },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.ecosystems_bucket_arn}/catalog-content/*"] },
      { Effect = "Allow", Action = ["states:StartExecution"], Resource = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*" },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [aws_lambda_function.fn["catalog-ingest"].arn, aws_lambda_function.fn["catalog-ingest-generic"].arn] },
      ], length(local.memory_resources) > 0 ? [
      { Effect = "Allow", Action = ["bedrock-agentcore:GetMemory", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords"], Resource = local.memory_resources },
    ] : [])
  })
}

# catalog-write: memory rw + catalog-content S3
resource "aws_iam_role_policy" "catalog_write" {
  name = "grants"
  role = aws_iam_role.fn["catalog-write"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${var.ecosystems_bucket_arn}/catalog-content/*"] },
      ], length(local.memory_resources) > 0 ? [
      { Effect = "Allow", Action = local.memory_rw_actions, Resource = local.memory_resources },
    ] : [])
  })
}

# catalog-regrounding: refresh rw + memory rw + ecosystems read/list
resource "aws_iam_role_policy" "catalog_regrounding" {
  name = "grants"
  role = aws_iam_role.fn["catalog-regrounding"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"], Resource = [var.catalog_refresh_table_arn] },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.ecosystems_bucket_arn}/ecosystems/*"] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [var.ecosystems_bucket_arn], Condition = { StringLike = { "s3:prefix" = ["ecosystems/*"] } } },
      ], length(local.memory_resources) > 0 ? [
      { Effect = "Allow", Action = local.memory_rw_actions, Resource = local.memory_resources },
    ] : [])
  })
}

# interactive-invoke: refresh rw + memory read + bedrock + ecosystems read + self-invoke
resource "aws_iam_role_policy" "interactive_invoke" {
  name = "grants"
  role = aws_iam_role.fn["interactive-invoke"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"], Resource = [var.catalog_refresh_table_arn] },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-interactive-invoke" },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = local.bedrock_all_models },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.ecosystems_bucket_arn}/ecosystems/*"] },
      ], length(local.memory_resources) > 0 ? [
      { Effect = "Allow", Action = ["bedrock-agentcore:GetMemory", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords"], Resource = local.memory_resources },
    ] : [])
  })
}

# concierge-ws: refresh read + memory read + bedrock + ecosystems/runs read + SFN read
#   + execute-api manage (push to WS) + AVP authorize
resource "aws_iam_role_policy" "concierge_ws" {
  name = "grants"
  role = aws_iam_role.fn["concierge-ws"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query"], Resource = [var.catalog_refresh_table_arn] },
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = local.bedrock_all_models },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.ecosystems_bucket_arn}/ecosystems/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["states:ListExecutions", "states:DescribeExecution", "states:GetExecutionHistory"], Resource = ["arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*", "arn:${var.partition}:states:${var.region}:${var.account_id}:execution:${var.name_prefix}-*:*"] },
      ],
      var.websocket_api_id != "" ? [
        { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${var.websocket_api_id}/*" },
      ] : [],
      var.policy_store_id != "" ? [
        { Effect = "Allow", Action = ["verifiedpermissions:IsAuthorized"], Resource = "arn:${var.partition}:verifiedpermissions::${var.account_id}:policy-store/${var.policy_store_id}" },
      ] : [],
      length(local.memory_resources) > 0 ? [
        { Effect = "Allow", Action = ["bedrock-agentcore:GetMemory", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords"], Resource = local.memory_resources },
    ] : [])
  })
}

# quorum-run: bedrock + memory read + runs rw + ecosystems read + connections + WS push
resource "aws_iam_role_policy" "quorum_run" {
  count = var.enable_quorum ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.fn["quorum-run"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = local.bedrock_all_models },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:Query"], Resource = [var.connections_table_arn] },
      # Read the catalog refresh table to resolve the active scrutineer catalog version
      # (pk=DOMAIN#scrutineer sk=ACTIVE) so the live persona pool loads from the promoted version.
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query"], Resource = [var.catalog_refresh_table_arn] },
      ],
      var.websocket_api_id != "" ? [
        { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${var.websocket_api_id}/*" },
      ] : [],
      length(local.memory_resources) > 0 ? [
        { Effect = "Allow", Action = ["bedrock-agentcore:GetMemory", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords"], Resource = local.memory_resources },
    ] : [])
  })
}
