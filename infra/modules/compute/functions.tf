# ─────────────────────────────────────────────────────────────────────────────
# Lambda functions (data-driven)
#
# Every function is created uniformly from the `functions` map below. Per-function
# overrides: handler, timeout, memory, whether it attaches to the VPC, and its env
# vars. Shared defaults (runtime, KMS env encryption, DLQ, shared layer, log group)
# are applied to all. IAM policies (per-function least privilege) live in iam.tf.
#
# Only functions owned by the platform control-plane live here. Ingest/quorum runtime
# Lambdas live in the orchestration module (matching the original stack split), so
# they are excluded below.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Functions owned by the orchestration module (ingest + quorum runtimes) — excluded here.
  orchestration_owned = toset([
    "catalog-ingest",
    "catalog-ingest-generic",
    "catalog-api",
    "catalog-write",
    "catalog-regrounding",
    "interactive-invoke",
    "concierge-ws",
    "quorum-run",
    # federated triggers are owned by the auth-federated module
    "federated-presignup",
    "federated-pretoken",
    # evaluation harness lambdas are owned by the eval module
    "eval-runner",
    "eval-judge",
    "eval-aggregator",
    "eval-trajectory",
    "online-eval",
  ])

  platform_lambda_dirs = setsubtract(local.lambda_dirs, local.orchestration_owned)

  # Common environment shared by many functions.
  common_env = {
    ECOSYSTEMS_BUCKET   = var.ecosystems_bucket_name
    TESSERA_PROJECT     = var.project
    TESSERA_ENVIRONMENT = var.environment
  }

  cors_origin = var.frontend_cors_origin

  # Per-function configuration. Anything not listed uses the defaults (10s, 256MB,
  # VPC-attached, common_env only). timeout in seconds, memory in MB.
  functions = {
    "list-ecosystems" = {
      timeout = 30, memory = 1536
      env     = { POLICY_STORE_ID = var.policy_store_id }
    }
    # get-ecosystem serves the eval scorecard/golden-set artifacts (?artifact=...), which
    # it reads from the runs bucket.
    "get-ecosystem" = { timeout = 10, memory = 256, env = { TESSERA_RUNS_BUCKET = var.runs_bucket_name } }
    "save-ecosystem" = {
      timeout = 10, memory = 256
      env = {
        POLICY_STORE_ID        = var.policy_store_id
        REGROUNDING_BUS        = var.regrounding_bus_name
        AGENT_INVOKER_ARN      = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-agent-invoker"
        GROUNDING_CHECK_ARN    = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-grounding-check"
        EXTERNAL_GROUNDING_ARN = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-external-grounding"
        SFN_ROLE_ARN           = aws_iam_role.sfn.arn
        # Evaluation harness: save-ecosystem compiles the per-eco Eval State Machine and
        # substitutes these deterministic eval-Lambda ARNs into the compiled ASL. EVAL_SFN_
        # ROLE_ARN is the role the Eval SM assumes (reuses the platform SFN role).
        EVAL_RUNNER_ARN     = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-eval-runner"
        EVAL_AGGREGATOR_ARN = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-eval-aggregator"
        EVAL_JUDGE_ARN      = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-eval-judge"
        EVAL_TRAJECTORY_ARN = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-eval-trajectory"
        EVAL_SFN_ROLE_ARN   = aws_iam_role.sfn.arn
        TESSERA_RUNS_BUCKET = var.runs_bucket_name
      }
    }
    "suggest-ecosystem" = {
      timeout = 300, memory = 256
      env = {
        BUILDER_MODEL_ID              = var.models["builder"]
        WEBSEARCH_CREDENTIAL_PROVIDER = var.websearch_provider_name
        WORKLOAD_IDENTITY_NAME        = "${var.name_prefix}-platform"
        CONNECTIONS_TABLE             = var.connections_table_name
        # suggest-ecosystem's eval-approve flow writes the curated golden set to the runs bucket.
        TESSERA_RUNS_BUCKET = var.runs_bucket_name
      }
    }
    # Productized export: BoM analyze + Terraform generate + zip + presign. Fetches
    # export-inputs/{eco-lite,agent-runtime,save-ecosystem}.zip from the ecosystems
    # bucket (Option A) and writes the package to the exports bucket.
    "export-ecosystem" = {
      timeout = 120, memory = 1024
      # Fetches + unpacks the prebuilt export inputs (lambdas.zip is ~280MB) into /tmp, then
      # assembles + re-zips the package there. The default 512MB /tmp is far too small, so
      # give it 2GB of ephemeral storage.
      ephemeral_storage = 2048
      env = {
        EXPORT_BUCKET         = var.exports_bucket_name
        EXPORT_REGION         = var.region
        EXPORT_INPUTS_PREFIX  = "export-inputs"
        EXPORT_PRESIGN_EXPIRY = "3600"
      }
    }
    # Standalone mission run-planner (mode=mission) shipped in exported /solo packages.
    # Same async + WebSocket contract as suggest-ecosystem's mission mode.
    "mission-console" = {
      timeout = 300, memory = 256
      env = {
        MISSION_MODEL_ID  = var.models["builder"]
        CONNECTIONS_TABLE = var.connections_table_name
      }
    }
    "suggest-agent" = { timeout = 60, memory = 256, env = {} }
    "list-agents"   = { timeout = 10, memory = 256, env = {} }
    "list-tools"    = { timeout = 10, memory = 256, env = {} }
    "runs-api" = {
      timeout = 30, memory = 256
      env = {
        ATTENTION_TABLE     = var.attention_table_name
        TESSERA_RUNS_BUCKET = var.runs_bucket_name
        ECOSYSTEMS_BUCKET   = var.ecosystems_bucket_name
        # Dev/demo: let any authenticated user read runs/reports of any ecosystem.
        DEMO_OPEN_ACCESS = var.demo_open_access ? "true" : "false"
      }
    }
    "chat" = {
      timeout = 180, memory = 1024
      env = {
        TESSERA_AGENT_RUNTIME_ARN = var.agent_runtime_arn
        TESSERA_RUNS_BUCKET       = var.runs_bucket_name
        TESSERA_MEMORY_ID         = var.memory_id
      }
    }
    "optimize-ecosystem" = {
      timeout = 120, memory = 1024
      env = {
        STATE_MACHINE_PREFIX = var.name_prefix
        OPTIMIZER_ECO_ID     = "platform-optimizer"
        AWS_ACCOUNT_ID       = var.account_id
        TESSERA_RUNS_BUCKET  = var.runs_bucket_name
      }
    }
    "apply-eco-patch" = { timeout = 60, memory = 512, env = {} }
    "domain-seed"     = { timeout = 120, memory = 512, env = {} }
    "domains"         = { timeout = 60, memory = 512, env = {} }
    "gate-decision" = {
      timeout = 30, memory = 256
      vpc     = false
      env = {
        TESSERA_RUNS_BUCKET = var.runs_bucket_name
        ATTENTION_TABLE     = var.attention_table_name
      }
    }
    "run-artifact" = {
      timeout = 60, memory = 1024
      env     = { TESSERA_RUNS_BUCKET = var.runs_bucket_name }
    }
    "presign-upload" = {
      timeout = 10, memory = 256
      env     = { RUNS_BUCKET = var.runs_bucket_name }
    }
    "ecosystem-files" = { timeout = 10, memory = 256, env = {} }
    "file-classify" = {
      timeout = 60, memory = 256
      env     = { CLASSIFY_MODEL_ID = var.models["classify"] }
    }
    "gate-cleanup" = {
      timeout = 60, memory = 256
      vpc     = false
      env = {
        ATTENTION_TABLE     = var.attention_table_name
        TESSERA_RUNS_BUCKET = var.runs_bucket_name
      }
    }
    "agent-invoker" = {
      timeout = 900, memory = 512
      env = {
        TESSERA_AGENT_RUNTIME_ARN = var.agent_runtime_arn
        CONNECTIONS_TABLE         = var.connections_table_name
        ATTENTION_TABLE           = var.attention_table_name
        ATTENTION_TOPIC_ARN       = aws_sns_topic.attention.arn
        TESSERA_RUNS_BUCKET       = var.runs_bucket_name
        CATALOG_CONTENT_BUCKET    = var.ecosystems_bucket_name
        TESSERA_MEMORY_ID         = var.memory_id
      }
    }
    # WebSocket $connect REQUEST authorizer — verifies the Cognito ID token from the
    # ?token= query string at handshake time (CKV_AWS_309). Wired to the WS API in the
    # api module via its deterministic ARN.
    "ws-authorizer" = {
      timeout = 10, memory = 256
      env = {
        # Signature + issuer + expiry are verified (aud check is optional, matching the
        # websocket handler which also sets only the pool id).
        COGNITO_USER_POOL_ID = var.user_pool_id
      }
    }
    "websocket" = {
      timeout = 30, memory = 256
      env = {
        TESSERA_AGENT_RUNTIME_ARN = var.agent_runtime_arn
        CONNECTIONS_TABLE         = var.connections_table_name
        STATE_MACHINE_PREFIX      = var.name_prefix
        AWS_ACCOUNT_ID            = var.account_id
        TESSERA_RUNS_BUCKET       = var.runs_bucket_name
        COGNITO_USER_POOL_ID      = var.user_pool_id
        # Async run "Ask" chat: the WS `chat` route Event-invokes this function, which
        # pushes the answer back over the socket (escapes the 29s REST timeout).
        CHAT_FUNCTION = "${var.name_prefix}-chat"
      }
    }
    "grounding-check" = {
      timeout = 30, memory = 256
      env = {
        TESSERA_GUARDRAIL_ID      = var.guardrail_id
        TESSERA_GUARDRAIL_VERSION = "DRAFT"
        CONNECTIONS_TABLE         = var.connections_table_name
        TESSERA_RUNS_BUCKET       = var.runs_bucket_name
      }
    }
    "external-grounding" = {
      timeout = 120, memory = 512
      env = {
        WEBSEARCH_CREDENTIAL_PROVIDER = var.websearch_provider_name
        WORKLOAD_IDENTITY_NAME        = "${var.name_prefix}-platform"
        CONNECTIONS_TABLE             = var.connections_table_name
        EXTERNAL_EVAL_MODEL_ID        = var.models["external_eval"]
      }
    }
    "tool-catalog" = { timeout = 30, memory = 256, env = {} }
    "sharing" = {
      timeout = 30, memory = 256
      env     = { POLICY_STORE_ID = var.policy_store_id, USER_POOL_ID = var.user_pool_id }
    }
    "admin-users" = {
      timeout = 10, memory = 256
      env     = { USER_POOL_ID = var.user_pool_id }
    }
    "groups" = {
      timeout = 15, memory = 256
      env     = { USER_POOL_ID = var.user_pool_id }
    }
    "post-confirmation" = {
      timeout = 10, memory = 256
      env = {
        TESSERA_SIGNUP_TOPIC_ARN = aws_sns_topic.signup_approvals.arn
        SIGNUP_REQUIRE_APPROVAL  = var.signup_require_approval ? "true" : "false"
      }
    }
  }

  # QUORUM archetype enablement (env only) on the control-plane trio, matching the
  # original. WS handler also learns the quorum-run function name.
  quorum_env = var.enable_quorum ? {
    "save-ecosystem"    = { QUORUM_ENABLED = "true" }
    "suggest-ecosystem" = { QUORUM_ENABLED = "true" }
    "websocket"         = { QUORUM_ENABLED = "true", QUORUM_RUN_FUNCTION = var.quorum_run_function_name }
  } : {}

  # WS_ENDPOINT (WebSocket callback URL) for the functions that push to connections.
  # Sourced from the api module (api → compute), so no dependency cycle.
  ws_consumer_fns = ["agent-invoker", "grounding-check", "external-grounding", "suggest-ecosystem", "mission-console"]
  ws_env = var.websocket_callback_url != "" ? {
    for fn in local.ws_consumer_fns : fn => { WS_ENDPOINT = var.websocket_callback_url }
  } : {}

  # Resolve the full config for each platform-owned function, applying defaults.
  resolved_functions = {
    for name in local.platform_lambda_dirs : name => {
      timeout           = try(local.functions[name].timeout, 10)
      memory            = try(local.functions[name].memory, 256)
      vpc               = try(local.functions[name].vpc, true)
      ephemeral_storage = try(local.functions[name].ephemeral_storage, 512)
      env = merge(
        local.common_env,
        { CORS_ORIGIN = local.cors_origin },
        try(local.functions[name].env, {}),
        try(local.quorum_env[name], {}),
        try(local.ws_env[name], {}),
      )
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.resolved_functions
  name     = "/aws/lambda/${var.name_prefix}-${each.key}"
  # >= 1 year retention (CKV_AWS_338); honors a higher per-env value if set.
  retention_in_days = max(var.log_retention_days, 365)
  kms_key_id        = var.logs_key_arn # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_115:Reserved (function-level) concurrency is intentionally unset — these control-plane functions must scale on demand behind the API/WebSocket; a reserved limit would throttle interactive traffic. Account-level concurrency governs blast radius. Accepted risk.
  #checkov:skip=CKV_AWS_272:Code-signing requires a Signer profile + signed artifacts, which is a pipeline/build change out of scope here. Artifacts are built from source in-repo and integrity-checked via source_code_hash. Accepted risk.
  for_each = local.resolved_functions

  function_name    = "${var.name_prefix}-${each.key}"
  role             = aws_iam_role.lambda[each.key].arn
  runtime          = var.lambda_runtime
  handler          = "handler.handler"
  filename         = local.lambda_package[each.key].filename
  source_code_hash = local.lambda_package[each.key].hash
  timeout          = each.value.timeout
  memory_size      = min(each.value.memory, var.lambda_max_memory)
  layers           = [aws_lambda_layer_version.shared.arn]

  ephemeral_storage {
    size = each.value.ephemeral_storage
  }

  kms_key_arn = var.lambda_env_key_arn

  dead_letter_config {
    target_arn = var.lambda_dlq_arn
  }

  dynamic "vpc_config" {
    for_each = each.value.vpc ? [1] : []
    content {
      subnet_ids         = var.vpc_private_subnet_ids
      security_group_ids = [var.lambda_security_group_id]
    }
  }

  environment {
    variables = each.value.env
  }

  # Active X-Ray tracing (CKV_AWS_50) — request-level tracing for observability.
  tracing_config {
    mode = "Active"
  }

  tags       = var.tags
  depends_on = [aws_cloudwatch_log_group.lambda]
}
