// terraform-tessera-eco :: core resources (stable AWS services)
// The single-ecosystem run plane. AgentCore resources live in agentcore.tf.

locals {
  name = "tessera-${var.eco_id}"
  tags = merge(var.tags, {
    "tessera:ecosystem" = var.eco_id
    "tessera:version"   = var.eco_version
    "ManagedBy"         = "terraform-tessera-eco"
  })
  is_orchestrated = var.strategy == "orchestrated" && length(var.state_machine_definition) > 0

  // create-or-reference resolution (see bootstrap.tf)
  vpc_id             = var.create_networking ? aws_vpc.this[0].id : var.vpc_id
  private_subnet_ids = var.create_networking ? aws_subnet.private[*].id : var.private_subnet_ids
  kms_key_arn        = var.create_kms ? aws_kms_key.this[0].arn : var.kms_key_arn
  artifacts_bucket   = var.create_artifacts_bucket ? aws_s3_bucket.artifacts[0].id : var.artifacts_bucket
}

// Fail fast in reference mode when a required id/ARN wasn't supplied.
resource "terraform_data" "preconditions" {
  lifecycle {
    precondition {
      condition     = var.create_networking || (var.vpc_id != "" && length(var.private_subnet_ids) > 0)
      error_message = "Set create_networking=true, or supply vpc_id + private_subnet_ids."
    }
    precondition {
      condition     = var.create_kms || var.kms_key_arn != ""
      error_message = "Set create_kms=true, or supply kms_key_arn."
    }
    precondition {
      condition     = var.create_artifacts_bucket || var.artifacts_bucket != ""
      error_message = "Set create_artifacts_bucket=true, or supply artifacts_bucket."
    }
  }
}

// Per-Lambda environment. Common env + per-function extras (from each handler's os.environ
// reads). Gaps that need infra the module doesn't yet create are intentionally unset.
locals {
  common_lambda_env = {
    ECO_ID                  = var.eco_id
    TESSERA_RUNS_BUCKET     = aws_s3_bucket.runs.bucket
    CONNECTIONS_TABLE       = aws_dynamodb_table.connections.name
    ATTENTION_TABLE         = aws_dynamodb_table.attention.name
    ECOSYSTEMS_BUCKET       = local.artifacts_bucket
    AWS_ACCOUNT_ID          = data.aws_caller_identity.current.account_id
    WS_ENDPOINT             = "https://${aws_apigatewayv2_api.ws.id}.execute-api.${var.region}.amazonaws.com/prod"
    CORS_ORIGIN             = "*"
    TESSERA_PROJECT         = "tessera"
    TESSERA_ENVIRONMENT     = "eco"
    ALLOWED_GROUPS          = join(",", var.allowed_groups)
    ALLOW_ALL_AUTHENTICATED = tostring(var.allow_all_authenticated)
    # Single-eco export = single tenant: access is already gated by THIS eco's own Cognito pool
    # (only users you create can sign in), and there is exactly one ecosystem. The eco YAML still
    # carries its original PLATFORM `owner` id, which will never match the solo operator's user id
    # — so runs-api's owner/admin gate would return 403 "Access denied" on GET /runs and every run
    # operation. DEMO_OPEN_ACCESS lets any authenticated user view + operate runs of this eco
    # (list/view/resume/redrive/rate/guidance). It does NOT grant user-management/edit (those
    # lambdas aren't shipped in the export anyway). Correct posture for a self-serve single-eco deploy.
    DEMO_OPEN_ACCESS = "true"
  }
  lambda_env = {
    "agent-invoker" = {
      # Stream-silence watchdog. 30s is too tight for data-heavy agents (e.g. `quant`
      # makes 50+ tool calls + generateUI renders); a normal pause between the last
      # TEXT_MESSAGE_END and the next SSE event tripped it and aborted a near-complete
      # run with AgentStreamSilenceError. 120s tolerates slow model turns / long tool
      # renders while still catching a genuinely stuck agent (Lambda timeout is 900s).
      TESSERA_STREAM_SILENCE_SECONDS = "120"
      TESSERA_AGENT_RUNTIME_ARN      = awscc_bedrockagentcore_runtime.eco.agent_runtime_arn
    }
    "websocket" = {
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.eco.id
      COGNITO_CLIENT_ID    = aws_cognito_user_pool_client.eco.id
      STATE_MACHINE_PREFIX = "tessera"
      QUORUM_RUN_FUNCTION  = ""
      # REQUIRED: the websocket handler reads TESSERA_AGENT_RUNTIME_ARN at MODULE IMPORT
      # (os.environ["TESSERA_AGENT_RUNTIME_ARN"]). Without it the Lambda crashes on cold start,
      # so $connect fails and the browser sees "WebSocket connection failed" / the mission chat
      # reports "Live connection not ready". The chat route only invokes the agent runtime for
      # run "Ask"; mission planning doesn't need it, but the import does — so it must be set.
      TESSERA_AGENT_RUNTIME_ARN = awscc_bedrockagentcore_runtime.eco.agent_runtime_arn
    }
    # WebSocket $connect authorizer — verifies the eco pool's ID token from ?token=.
    "ws-authorizer" = {
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.eco.id
      COGNITO_CLIENT_ID    = aws_cognito_user_pool_client.eco.id
    }
    "run-artifact" = { INLINE_MAX_BYTES = "100000", PRESIGN_EXPIRY_S = "3600" }
    "grounding-check" = {
      TESSERA_GUARDRAIL_ID      = try(aws_bedrock_guardrail.grounding[0].guardrail_id, "")
      TESSERA_GUARDRAIL_VERSION = "DRAFT"
    }
    "gate-decision" = { GATE_CHAT_MODEL = lookup(var.model_ids, "leaf", "us.anthropic.claude-sonnet-4-5-20250929-v1:0") }
    # Conversational run-planner (mode=mission) — pick a capable model; streams over the WS.
    "mission-console" = {
      MISSION_MODEL_ID = lookup(var.model_ids, "synthesizer", lookup(var.model_ids, "leaf", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"))
    }
    # presign-upload reads RUNS_BUCKET (run attachments land under {sessionId}/inputs/).
    "presign-upload" = { RUNS_BUCKET = aws_s3_bucket.runs.bucket }
    # runs-api resolves the state-machine name from STATE_MACHINE_PREFIX (same as the
    # websocket launcher). The eco-lite SM is `tessera-${eco_id}` (no env segment), so
    # without this prefix runs-api builds `tessera-eco-${eco_id}` and every GET /runs
    # 500s with StateMachineDoesNotExist.
    "runs-api" = { STATE_MACHINE_PREFIX = "tessera" }
  }
}

// ── State: runs bucket + connection/attention tables ─────────────────────────────────
resource "aws_s3_bucket" "runs" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for this single-region customer template (no DR requirement). The bucket IS CMK-encrypted (SSE-KMS with local.kms_key_arn below). Accepted risk.
  bucket        = "${local.name}-runs"
  force_destroy = var.force_destroy_data # teardown: delete non-empty bucket (spec §14.1)
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "runs" {
  bucket                  = aws_s3_bucket.runs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.kms_key_arn
    }
  }
}

resource "aws_s3_bucket_versioning" "runs" {
  bucket = aws_s3_bucket.runs.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Lifecycle (CKV2_AWS_61): no object expiration — run artifacts/deliverables must persist —
# just clean up abandoned multipart uploads (CKV_AWS_300) and expire noncurrent versions.
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

# S3 event notification (CKV2_AWS_62): EventBridge delivery, additive.
resource "aws_s3_bucket_notification" "runs" {
  bucket      = aws_s3_bucket.runs.id
  eventbridge = true
}

resource "aws_dynamodb_table" "connections" {
  name         = "${local.name}-connections"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "connectionId"
  attribute {
    name = "connectionId"
    type = "S"
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }
  point_in_time_recovery {
    enabled = true # CKV_AWS_28 — secure-by-default for the customer-shipped module.
  }
  tags = local.tags
}

resource "aws_dynamodb_table" "attention" {
  name         = "${local.name}-attention"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = local.kms_key_arn
  }
  point_in_time_recovery {
    enabled = true # CKV_AWS_28 — secure-by-default for the customer-shipped module.
  }
  tags = local.tags
}

// ── Networking: run-plane Lambda security group (private profile, D10) ────────────────
resource "aws_security_group" "lambda" {
  #checkov:skip=CKV_AWS_382:Run-plane Lambdas need outbound egress to reach Bedrock/AgentCore + AWS APIs; there is no fixed egress CIDR/port set to restrict to (tighten to VPC-endpoint prefix lists in production, noted below). Egress-only, no inbound. Accepted risk.
  name_prefix = "${local.name}-lambda-"
  vpc_id      = local.vpc_id
  description = "Egress-only SG for run-plane Lambdas (reach AWS via interface endpoints)."
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All egress (tighten to VPC-endpoint prefix lists in production)."
  }
  tags = local.tags
}

// ── IAM: least-privilege role for the run-plane Lambdas (spec §9) ─────────────────────
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
  name               = "${local.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "RunsBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.runs.arn, "${aws_s3_bucket.runs.arn}/*"]
  }
  statement {
    # get-ecosystem / runs-api read the eco YAML from the artifacts (ecosystems) bucket.
    sid       = "EcosystemsBucket"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.artifacts_bucket}", "arn:aws:s3:::${local.artifacts_bucket}/*"]
  }
  statement {
    sid       = "StateTables"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.connections.arn, aws_dynamodb_table.attention.arn]
  }
  statement {
    # Tables, runs bucket and Lambda env are CMK-encrypted → the role must use the key.
    sid       = "KmsUse"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [local.kms_key_arn]
  }
  statement {
    # Scoped to this eco's own AgentCore runtime. The runtime ARN carries a random suffix,
    # so we also allow the versioned child ARNs under it (…/runtime/<name>-<id>/*).
    sid     = "InvokeAgentRuntime"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      awscc_bedrockagentcore_runtime.eco.agent_runtime_arn,
      "${awscc_bedrockagentcore_runtime.eco.agent_runtime_arn}/*",
    ]
  }
  statement {
    sid       = "PostToConnections"
    actions   = ["execute-api:ManageConnections"]
    resources = ["${aws_apigatewayv2_api.ws.execution_arn}/*"]
  }
  statement {
    # mission-console returns 202 then self-invokes async (InvocationType=Event) to stream the
    # planner turn over the WebSocket. Constructed ARN (not a resource ref) to avoid a cycle.
    sid       = "SelfInvokeAsync"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${local.name}-mission-console"]
  }
  statement {
    # agent-invoker runs under Step Functions waitForTaskToken and reports back.
    sid       = "SfnCallback"
    actions   = ["states:SendTaskSuccess", "states:SendTaskFailure", "states:SendTaskHeartbeat"]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = local.is_orchestrated ? [1] : []
    content {
      // Run plane: websocket StartExecution; runs-api list/inspect/redrive.
      // ListExecutions/Describe*/DescribeStateMachine act on the STATE MACHINE arn;
      // GetExecutionHistory/DescribeExecution/RedriveExecution act on EXECUTION arns
      // (arn:...:execution:<sm-name>:*, a distinct resource type — the stateMachine
      // arn + ":*" does NOT cover it), so both resource forms are listed.
      sid = "StartRun"
      actions = [
        "states:StartExecution",
        "states:StopExecution",
        "states:DescribeExecution",
        "states:DescribeStateMachine",
        "states:ListExecutions",
        "states:GetExecutionHistory",
        "states:RedriveExecution",
      ]
      resources = [
        aws_sfn_state_machine.eco[0].arn,
        // execution arn = the SM arn with the resource type swapped, then :* for any execution id.
        "${replace(aws_sfn_state_machine.eco[0].arn, ":stateMachine:", ":execution:")}:*",
      ]
    }
  }
  statement {
    # InvokeModel — gate-decision/mission-console/file-classify. Scoped to the pinned
    # model_ids: each id may be a foundation model or an inference profile, and a
    # cross-region inference profile fans out to the underlying foundation models in
    # sibling regions, so we allow both the foundation-model and inference-profile ARN
    # forms (account-scoped foundation-model ARNs have an empty account field).
    sid     = "BedrockInvokeModel"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = concat(
      [for m in values(var.model_ids) : "arn:aws:bedrock:*::foundation-model/${m}"],
      [for m in values(var.model_ids) : "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/${m}"],
    )
  }
  dynamic "statement" {
    # ApplyGuardrail — grounding-check calls bedrock.apply_guardrail() against the
    # provisioned grounding guardrail after every dependent agent; without it grounding
    # checks AccessDenied and the Step Functions run fails. Only present when grounding is.
    for_each = var.grounding_enabled ? [1] : []
    content {
      sid       = "BedrockApplyGuardrail"
      actions   = ["bedrock:ApplyGuardrail"]
      resources = [aws_bedrock_guardrail.grounding[0].guardrail_arn]
    }
  }
  dynamic "statement" {
    for_each = length(var.tool_secret_arns) > 0 ? [1] : []
    content {
      sid       = "ToolSecrets"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = values(var.tool_secret_arns)
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.name}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

// Per-function timeout/memory that MUST match the platform's compute config for the run
// plane to work. The export tfvars only carries s3_key per lambda, so var.lambdas.timeout
// falls back to the variable default (120s) for every function — which killed agent-invoker
// mid-run ("Sandbox.Timedout after 120.00s"): the `quant` agent legitimately runs minutes,
// but the platform gives agent-invoker 900s. These overrides restore the right ceilings even
// when tfvars doesn't specify them; unspecified functions keep the var.lambdas value.
locals {
  fn_timeout_overrides = {
    "agent-invoker"   = 900
    "mission-console" = 300
    "run-artifact"    = 60
    "gate-cleanup"    = 60
  }
  fn_memory_overrides = {
    "run-artifact" = 1024
  }
}

// ── Run-plane Lambdas (BoM closure) — prebuilt, pinned artifacts (spec §8.2) ──────────
// TF-managed log groups so teardown removes them (no orphaned /aws/lambda/* groups).
resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = var.lambdas
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = local.kms_key_arn                # key policy must grant logs.<region>.amazonaws.com
  tags              = local.tags
}

resource "aws_lambda_function" "fn" {
  #checkov:skip=CKV_AWS_115:Reserved (function-level) concurrency is intentionally unset — the run-plane must scale with run/WebSocket demand; a reserved limit would throttle runs. Account-level concurrency governs blast radius. Accepted risk.
  #checkov:skip=CKV_AWS_116:These Lambdas run SYNCHRONOUSLY under Step Functions (agent-invoker via waitForTaskToken; others request/response) — there is no async invocation path for a dead-letter queue to catch. A DLQ would be dead config. N/A.
  #checkov:skip=CKV_AWS_272:Code-signing requires a Signer profile + signed artifacts (customer build/pipeline change out of scope for the template). Artifacts are integrity-checked via source_code_hash on the shipped zips. Accepted risk.
  for_each = var.lambdas

  function_name = "${local.name}-${each.key}"
  s3_bucket     = local.artifacts_bucket
  s3_key        = each.value.s3_key
  handler       = each.value.handler
  runtime       = "python3.12"
  # x86_64 to match the dependency wheels shipped in lambdas/<name>.zip: the shared
  # build (scripts/package-lambdas.sh) installs manylinux2014_x86_64 wheels (the
  # platform deploys x86_64), so native deps like cryptography (PyJWT → the websocket
  # JWT verifier) MUST run on x86_64. arm64 here fails to load _rust.abi3.so
  # ("cannot open shared object file"), silently breaking WS token verification / launch.
  architectures = ["x86_64"]
  # Prefer the run-plane override (matches the platform); fall back to the tfvars value.
  timeout     = lookup(local.fn_timeout_overrides, each.key, each.value.timeout)
  memory_size = lookup(local.fn_memory_overrides, each.key, each.value.memory)
  role        = aws_iam_role.lambda.arn
  kms_key_arn = local.kms_key_arn
  layers      = [aws_lambda_layer_version.shared.arn]

  # The prebuilt per-lambda zips are shipped inside the exported package (lambdas/<name>.zip)
  # and uploaded to the artifacts bucket by aws_s3_object.lambda; source_code_hash reads the
  # shipped zip so redeploys pick up code changes, and the function waits on the upload.
  source_code_hash = filebase64sha256("${path.root}/lambdas/${each.key}.zip")

  depends_on = [aws_cloudwatch_log_group.lambda, aws_s3_object.lambda]

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = merge(local.common_lambda_env, lookup(local.lambda_env, each.key, {}))
  }

  # Active X-Ray tracing (CKV_AWS_50).
  tracing_config {
    mode = "Active"
  }

  tags = local.tags
}

// ── Orchestration: Step Functions (orchestrated ecos only) ────────────────────────────
data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "states" {
  name               = "${local.name}-states"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "states" {
  name = "${local.name}-states"
  role = aws_iam_role.states.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [for f in aws_lambda_function.fn : f.arn]
      },
      # X-Ray tracing (CKV_AWS_284). X-Ray API actions do not support resource scoping.
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
        Resource = ["*"]
      },
      # Execution history logging to CloudWatch Logs (CKV_AWS_285).
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
        ]
        Resource = ["*"]
      },
    ]
  })
}

# Execution-history log group for the eco state machine (CKV_AWS_285).
resource "aws_cloudwatch_log_group" "sfn_eco" {
  count             = local.is_orchestrated ? 1 : 0
  name              = "/aws/vendedlogs/states/${local.name}"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_sfn_state_machine" "eco" {
  count    = local.is_orchestrated ? 1 : 0
  name     = local.name
  role_arn = aws_iam_role.states.arn

  # X-Ray tracing (CKV_AWS_284) + execution history logging (CKV_AWS_285).
  tracing_configuration {
    enabled = true
  }
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_eco[0].arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }
  // The production compiler (save-ecosystem) emits placeholder tokens for the platform
  // lambda ARNs; resolve them to this eco's own functions at apply. external-grounding
  // is only present when the eco enables it (var.lambdas ships it); if it isn't shipped
  // the token resolves empty — an external-grounding eco must ship that lambda.
  definition = replace(replace(replace(
    var.state_machine_definition,
    "__AGENT_INVOKER_ARN__", try(aws_lambda_function.fn["agent-invoker"].arn, "")),
    "__GROUNDING_CHECK_ARN__", try(aws_lambda_function.fn["grounding-check"].arn, "")),
    "__EXTERNAL_GROUNDING_ARN__", try(aws_lambda_function.fn["external-grounding"].arn, "")
  )
  tags = local.tags
}

// ── Quality: Bedrock Guardrail (grounding) ────────────────────────────────────────────
resource "aws_bedrock_guardrail" "grounding" {
  count                     = var.grounding_enabled ? 1 : 0
  name                      = "${local.name}-grounding"
  blocked_input_messaging   = "This request was blocked by the ecosystem guardrail."
  blocked_outputs_messaging = "This response was blocked by the ecosystem guardrail."
  tags                      = local.tags

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = 0.75
    }
  }
}

// ── Auth: Cognito direct login (always on) + optional federated IdP (D11) ─────────────
resource "aws_cognito_user_pool" "eco" {
  name                = local.name
  deletion_protection = var.cognito_deletion_protection # ACTIVE in prod; INACTIVE lets teardown remove it
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  tags = local.tags
}

resource "aws_cognito_identity_provider" "federated" {
  count         = var.federated_idp.enabled ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.eco.id
  provider_name = var.federated_idp.provider_name
  provider_type = "OIDC"

  provider_details = {
    client_id = var.federated_idp.client_id
    # The client secret is supplied out-of-band from the referenced secret ARN; use a
    # placeholder here (no data source) so validate passes without the secret present.
    client_secret             = "REPLACE_VIA_SECRET:${var.federated_idp.secret_arn}" # pragma: allowlist secret — placeholder token referencing a secret ARN, not a secret value
    oidc_issuer               = var.federated_idp.oidc_issuer
    authorize_scopes          = "openid"
    attributes_request_method = "GET"
  }

  attribute_mapping = {
    email    = "sub"
    username = "sub"
  }
}

resource "aws_cognito_user_pool_client" "eco" {
  name         = local.name
  user_pool_id = aws_cognito_user_pool.eco.id

  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_ADMIN_USER_PASSWORD_AUTH"]
  supported_identity_providers = concat(
    ["COGNITO"],
    var.federated_idp.enabled ? [var.federated_idp.provider_name] : [],
  )

  depends_on = [aws_cognito_identity_provider.federated]
}
