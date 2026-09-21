# ─────────────────────────────────────────────────────────────────────────────
# Per-function least-privilege inline policies. Each attaches to that function's role
# only when the function exists in this module's function set. Mirrors the grants from
# the original stack, scoped by the runtime-resolved account/region.
# ─────────────────────────────────────────────────────────────────────────────

# Helper to attach a JSON policy to a named function's role only if it exists.
locals {
  fn_exists = { for name in local.platform_lambda_dirs : name => true }
}

# ── S3: ecosystems read/write ─────────────────────────────────────────────────
data "aws_iam_policy_document" "ecosystems_rw" {
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      var.ecosystems_bucket_arn,
      "${var.ecosystems_bucket_arn}/*",
    ]
  }
}

data "aws_iam_policy_document" "runs_rw" {
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      var.runs_bucket_arn,
      "${var.runs_bucket_arn}/*",
    ]
  }
}

# list-ecosystems: ecosystems read + AVP authorize
resource "aws_iam_role_policy" "list_ecosystems" {
  count = contains(local.platform_lambda_dirs, "list-ecosystems") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["list-ecosystems"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["verifiedpermissions:IsAuthorized", "verifiedpermissions:BatchIsAuthorized"], Resource = var.policy_store_arn },
    ]
  })
}

# get-ecosystem / list-agents / list-tools: ecosystems read/write
resource "aws_iam_role_policy" "ecosystems_readers" {
  for_each = toset([for n in ["get-ecosystem", "list-agents", "list-tools", "ecosystem-files"] : n if contains(local.platform_lambda_dirs, n)])
  name     = "grants"
  role     = aws_iam_role.lambda[each.value].id
  policy   = data.aws_iam_policy_document.ecosystems_rw.json
}

# get-ecosystem also serves eval scorecard/golden-set artifacts, read from the runs bucket.
resource "aws_iam_role_policy" "get_ecosystem_runs_read" {
  count = contains(local.platform_lambda_dirs, "get-ecosystem") ? 1 : 0
  name  = "runs-read"
  role  = aws_iam_role.lambda["get-ecosystem"].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] }]
  })
}

# save-ecosystem: ecosystems rw + SFN create/update + iam:PassRole + AVP CreatePolicy
# + events PutEvents (regrounding bus) + lambda invoke (self + downstream)
resource "aws_iam_role_policy" "save_ecosystem" {
  count = contains(local.platform_lambda_dirs, "save-ecosystem") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["save-ecosystem"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      # Eval deploy-gate reads the latest scorecard from the runs bucket.
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["states:CreateStateMachine", "states:UpdateStateMachine", "states:DescribeStateMachine"], Resource = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*" },
      { Effect = "Allow", Action = ["states:ListStateMachines"], Resource = "*" },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = "arn:${var.partition}:iam::${var.account_id}:role/${var.name_prefix}-*" },
      { Effect = "Allow", Action = ["verifiedpermissions:CreatePolicy"], Resource = "arn:${var.partition}:verifiedpermissions::${var.account_id}:policy-store/*" },
      { Effect = "Allow", Action = ["events:PutEvents"], Resource = "arn:${var.partition}:events:${var.region}:${var.account_id}:event-bus/${var.regrounding_bus_name}" },
    ]
  })
}

# suggest-ecosystem: bedrock invoke + websearch identity + secrets + connections + WS + self-invoke
resource "aws_iam_role_policy" "suggest_ecosystem" {
  count = contains(local.platform_lambda_dirs, "suggest-ecosystem") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["suggest-ecosystem"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*", var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      # eval-approve writes the curated golden set (cases.jsonl) into the runs bucket.
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = local.bedrock_claude_resources },
      { Effect = "Allow", Action = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourceApiKey", "bedrock-agentcore:GetApiKeyCredentialProvider"], Resource = local.websearch_identity_resources },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = local.agentcore_secret_arn },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = var.connections_table_arn },
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-suggest-ecosystem" },
    ]
  })
}

# export-ecosystem: ecosystems read (incl export-inputs/) + exports bucket read/write
#   (GetObject on exports is required so the presigned download URL this Lambda signs
#   is valid — a presigner can only grant permissions it holds).
resource "aws_iam_role_policy" "export_ecosystem" {
  count = contains(local.platform_lambda_dirs, "export-ecosystem") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["export-ecosystem"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.exports_bucket_arn, "${var.exports_bucket_arn}/*"] },
    ]
  })
}

# mission-console: bedrock invoke + connections + WS mgmt + self-invoke + runs read
#   (reads run/mission attachments from the runs bucket for _enrich_attachments).
resource "aws_iam_role_policy" "mission_console" {
  count = contains(local.platform_lambda_dirs, "mission-console") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["mission-console"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = local.bedrock_claude_resources },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = var.connections_table_arn },
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-mission-console" },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
    ]
  })
}

# suggest-agent / domain-seed: bedrock invoke + ecosystems read
resource "aws_iam_role_policy" "bedrock_readers" {
  for_each = toset([for n in ["suggest-agent", "domain-seed"] : n if contains(local.platform_lambda_dirs, n)])
  name     = "grants"
  role     = aws_iam_role.lambda[each.value].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = local.bedrock_claude_resources },
    ]
  })
}

# domains: ecosystems rw + invoke save-ecosystem
resource "aws_iam_role_policy" "domains" {
  count = contains(local.platform_lambda_dirs, "domains") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["domains"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-save-ecosystem" },
    ]
  })
}

# runs-api: SFN read/redrive/start + runs rw + ecosystems read + attention rw
resource "aws_iam_role_policy" "runs_api" {
  count = contains(local.platform_lambda_dirs, "runs-api") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["runs-api"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["states:ListExecutions", "states:GetExecutionHistory", "states:DescribeExecution", "states:ListStateMachines", "states:DescribeStateMachine", "states:RedriveExecution", "states:StartExecution"], Resource = local.sfn_resources },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*", var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"], Resource = var.attention_table_arn },
    ]
  })
}

# chat: ecosystems+runs read + SFN read + bedrock + agentcore runtime + memory events
resource "aws_iam_role_policy" "chat" {
  count = contains(local.platform_lambda_dirs, "chat") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["chat"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*", var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["states:DescribeExecution", "states:GetExecutionHistory", "states:ListStateMachines"], Resource = local.sfn_resources },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = local.bedrock_claude_resources },
      { Effect = "Allow", Action = ["bedrock-agentcore:InvokeAgentRuntime"], Resource = local.agent_runtime_resources },
      # Async run "Ask": when invoked over the WS path, the chat Lambda pushes its answer
      # back to the WebSocket connection (ASK_RESPONSE), so it needs ManageConnections.
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      ], var.memory_arn != "" ? [
      { Effect = "Allow", Action = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents", "bedrock-agentcore:GetEvent", "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:RetrieveMemoryRecords"], Resource = [var.memory_arn, "${var.memory_arn}/*"] },
    ] : [])
  })
}

# optimize-ecosystem: ecosystems+runs read + SFN list/describe/start
resource "aws_iam_role_policy" "optimize_ecosystem" {
  count = contains(local.platform_lambda_dirs, "optimize-ecosystem") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["optimize-ecosystem"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*", var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["states:ListExecutions", "states:DescribeExecution", "states:StartExecution"], Resource = local.sfn_resources },
    ]
  })
}

# apply-eco-patch: ecosystems rw + invoke save-ecosystem
resource "aws_iam_role_policy" "apply_eco_patch" {
  count = contains(local.platform_lambda_dirs, "apply-eco-patch") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["apply-eco-patch"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-save-ecosystem" },
    ]
  })
}

# run-artifact: runs rw
resource "aws_iam_role_policy" "run_artifact" {
  count  = contains(local.platform_lambda_dirs, "run-artifact") ? 1 : 0
  name   = "grants"
  role   = aws_iam_role.lambda["run-artifact"].id
  policy = data.aws_iam_policy_document.runs_rw.json
}

# presign-upload: put to both buckets
resource "aws_iam_role_policy" "presign_upload" {
  count = contains(local.platform_lambda_dirs, "presign-upload") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["presign-upload"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${var.ecosystems_bucket_arn}/*", "${var.runs_bucket_arn}/*"] },
    ]
  })
}

# file-classify: both buckets rw + bedrock invoke
resource "aws_iam_role_policy" "file_classify" {
  count = contains(local.platform_lambda_dirs, "file-classify") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["file-classify"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*", var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = local.bedrock_claude_resources },
    ]
  })
}

# gate-cleanup: attention rw + runs rw
resource "aws_iam_role_policy" "gate_cleanup" {
  count = contains(local.platform_lambda_dirs, "gate-cleanup") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["gate-cleanup"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Scan is required: gate-cleanup scans the attention table to sweep stale entries.
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = var.attention_table_arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
    ]
  })
}

# gate-decision: runs rw + attention rw + SFN task callbacks + bedrock + browser + WS
resource "aws_iam_role_policy" "gate_decision" {
  count = contains(local.platform_lambda_dirs, "gate-decision") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["gate-decision"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query"], Resource = var.attention_table_arn },
      { Effect = "Allow", Action = ["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"], Resource = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*" },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = local.bedrock_claude_resources },
      { Effect = "Allow", Action = ["bedrock-agentcore:CreateBrowserProfile", "bedrock-agentcore:ListBrowserProfiles", "bedrock-agentcore:GetBrowserProfile", "bedrock-agentcore:StartBrowserSession", "bedrock-agentcore:StopBrowserSession", "bedrock-agentcore:GetBrowserSession", "bedrock-agentcore:InvokeBrowser", "bedrock-agentcore:SaveBrowserSessionProfile", "bedrock-agentcore:UpdateBrowserStream", "bedrock-agentcore:ConnectBrowserLiveViewStream", "bedrock-agentcore:ConnectBrowserAutomationStream"], Resource = "*" },
    ]
  })
}

# agent-invoker: agentcore runtime + WS mgmt + connections/attention + runs rw
#   + ecosystems (read + catalog-content rw) + memory data plane + SFN task callbacks
resource "aws_iam_role_policy" "agent_invoker" {
  count = contains(local.platform_lambda_dirs, "agent-invoker") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["agent-invoker"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["bedrock-agentcore:InvokeAgentRuntime"], Resource = local.agent_runtime_resources },
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = [var.connections_table_arn, var.attention_table_arn] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*", var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
      { Effect = "Allow", Action = ["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"], Resource = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*" },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.attention.arn },
      ], var.memory_arn != "" ? [
      { Effect = "Allow", Action = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetMemory", "bedrock-agentcore:BatchCreateMemoryRecords", "bedrock-agentcore:BatchUpdateMemoryRecords", "bedrock-agentcore:BatchDeleteMemoryRecords", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:RetrieveMemoryRecords"], Resource = [var.memory_arn, "${var.memory_arn}/*"] },
    ] : [])
  })
}

# websocket: SFN start + WS mgmt + agentcore runtime + connections + runs rw + quorum invoke
resource "aws_iam_role_policy" "websocket" {
  count = contains(local.platform_lambda_dirs, "websocket") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["websocket"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["states:StartExecution"], Resource = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*" },
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      { Effect = "Allow", Action = ["bedrock-agentcore:InvokeAgentRuntime"], Resource = local.agent_runtime_resources },
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = var.connections_table_arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      # Async run "Ask": the WS `chat` route Event-invokes the chat Lambda (CHAT_FUNCTION).
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-chat" },
      ], var.enable_quorum ? [
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.quorum_run_function_name}" },
    ] : [])
  })
}

# grounding-check: guardrail apply + WS mgmt + connections + runs read
resource "aws_iam_role_policy" "grounding_check" {
  count = contains(local.platform_lambda_dirs, "grounding-check") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["grounding-check"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      # Scan is required: push_event() scans the connections table to fan out live events.
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = var.connections_table_arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.runs_bucket_arn, "${var.runs_bucket_arn}/*"] },
      ], var.guardrail_id != "" ? [
      { Effect = "Allow", Action = ["bedrock:ApplyGuardrail"], Resource = "arn:${var.partition}:bedrock:${var.region}:${var.account_id}:guardrail/${var.guardrail_id}" },
    ] : [])
  })
}

# external-grounding: bedrock + WS + connections + websearch identity + secrets
resource "aws_iam_role_policy" "external_grounding" {
  count = contains(local.platform_lambda_dirs, "external-grounding") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["external-grounding"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = local.bedrock_claude_resources },
      { Effect = "Allow", Action = ["execute-api:ManageConnections"], Resource = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:*/${var.environment}/*" },
      # Scan is required: push_event() scans the connections table to fan out live events.
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = var.connections_table_arn },
      { Effect = "Allow", Action = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourceApiKey", "bedrock-agentcore:GetApiKeyCredentialProvider"], Resource = local.websearch_identity_resources },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = local.agentcore_secret_arn },
    ]
  })
}

# tool-catalog: agentcore gateway-target CRUD + credential providers + secrets + ecosystems read
resource "aws_iam_role_policy" "tool_catalog" {
  count = contains(local.platform_lambda_dirs, "tool-catalog") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["tool-catalog"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["bedrock-agentcore:CreateGatewayTarget", "bedrock-agentcore:UpdateGatewayTarget", "bedrock-agentcore:DeleteGatewayTarget", "bedrock-agentcore:GetGatewayTarget", "bedrock-agentcore:ListGatewayTargets"], Resource = "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:gateway-target/*", Condition = { StringEquals = { "aws:ResourceTag/Project" = var.project } } },
      { Effect = "Allow", Action = ["bedrock-agentcore:CreateApiKeyCredentialProvider", "bedrock-agentcore:UpdateApiKeyCredentialProvider", "bedrock-agentcore:GetApiKeyCredentialProvider"], Resource = "arn:${var.partition}:bedrock-agentcore:${var.region}:${var.account_id}:credential-provider/${var.project}-*" },
      { Effect = "Allow", Action = ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"], Resource = "arn:${var.partition}:secretsmanager:${var.region}:${var.account_id}:secret:bedrock-agentcore-identity*" },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:ListBucket"], Resource = [var.ecosystems_bucket_arn, "${var.ecosystems_bucket_arn}/*"] },
    ]
  })
}

# sharing: AVP policy CRUD + cognito list
resource "aws_iam_role_policy" "sharing" {
  count = contains(local.platform_lambda_dirs, "sharing") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["sharing"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["verifiedpermissions:CreatePolicy", "verifiedpermissions:ListPolicies", "verifiedpermissions:DeletePolicy", "verifiedpermissions:GetPolicy"], Resource = "arn:${var.partition}:verifiedpermissions::${var.account_id}:policy-store/${var.policy_store_id}" },
      { Effect = "Allow", Action = ["cognito-idp:ListUsers", "cognito-idp:ListGroups", "cognito-idp:ListUsersInGroup"], Resource = var.user_pool_arn },
    ]
  })
}

# admin-users: cognito user admin
resource "aws_iam_role_policy" "admin_users" {
  count = contains(local.platform_lambda_dirs, "admin-users") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["admin-users"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["cognito-idp:ListUsers", "cognito-idp:AdminEnableUser", "cognito-idp:AdminDeleteUser"], Resource = var.user_pool_arn },
    ]
  })
}

# groups: cognito group admin
resource "aws_iam_role_policy" "groups" {
  count = contains(local.platform_lambda_dirs, "groups") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["groups"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["cognito-idp:ListGroups", "cognito-idp:CreateGroup", "cognito-idp:DeleteGroup", "cognito-idp:ListUsersInGroup", "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminRemoveUserFromGroup", "cognito-idp:ListUsers"], Resource = var.user_pool_arn },
    ]
  })
}

# Allow Cognito to invoke the post-confirmation trigger. source_arn scoped to this
# account's user pools (userpool/*) so the compute module needs no reference to the
# identity module's pool — avoiding a module dependency cycle.
resource "aws_lambda_permission" "cognito_post_confirmation" {
  count         = contains(local.platform_lambda_dirs, "post-confirmation") ? 1 : 0
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["post-confirmation"].function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = "arn:${var.partition}:cognito-idp:${var.region}:${var.account_id}:userpool/*"
}

# post-confirmation: disable user + publish to signup topic
resource "aws_iam_role_policy" "post_confirmation" {
  count = contains(local.platform_lambda_dirs, "post-confirmation") ? 1 : 0
  name  = "grants"
  role  = aws_iam_role.lambda["post-confirmation"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["cognito-idp:AdminDisableUser"], Resource = "arn:${var.partition}:cognito-idp:${var.region}:${var.account_id}:userpool/*" },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.signup_approvals.arn },
    ]
  })
}
