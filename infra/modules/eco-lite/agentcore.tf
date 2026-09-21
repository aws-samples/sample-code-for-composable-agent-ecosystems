// terraform-tessera-eco :: AgentCore (Runtime + Gateway + Memory)
//
// AgentCore resources are provisioned via the AWS Cloud Control provider (awscc_*),
// generated from the CloudFormation registry
// (AWS::BedrockAgentCore::Runtime | Gateway | Memory). Argument shapes follow the
// documented CFN property names (snake_cased by awscc).

// Execution role AgentCore Runtime assumes (invokes Bedrock models, reads runs bucket).
data "aws_iam_policy_document" "runtime_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${local.name}-runtime"
  assume_role_policy = data.aws_iam_policy_document.runtime_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "runtime" {
  name = "${local.name}-runtime"
  role = aws_iam_role.runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # InvokeModel — the AgentCore runtime invokes foundation models / inference
        # profiles chosen at run time (the agent, not just the pinned SFN model_ids),
        # so the specific model name isn't known at deploy time. Scope to the Bedrock
        # model resource TYPES (region-wildcarded for cross-region inference fan-out)
        # rather than a bare "*": foundation models + system-defined inference profiles
        # carry an empty account field; application/account inference profiles carry this
        # account. This satisfies CKV_AWS_355 while still covering any model the runtime
        # legitimately selects.
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*::inference-profile/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:application-inference-profile/*",
        ]
      },
      {
        # AgentCore Memory DATA PLANE — event + record CRUD used by the runtime's
        # memory_read/write/search tools and auto-persist namespaces (against
        # TESSERA_MEMORY_ID). Resource-scoped to this eco's memory arn (+ /* for its
        # records/events).
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:DeleteEvent",
          "bedrock-agentcore:ListMemoryRecords",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:BatchCreateMemoryRecords",
          "bedrock-agentcore:BatchUpdateMemoryRecords",
          "bedrock-agentcore:BatchDeleteMemoryRecords",
        ]
        Resource = [awscc_bedrockagentcore_memory.eco.memory_arn, "${awscc_bedrockagentcore_memory.eco.memory_arn}/*"]
      },
      {
        # AgentCore Memory CONTROL PLANE — GetMemory/ListMemories are describe/list
        # operations the runtime uses to resolve the self-managed catalog strategy id.
        # These are denied when constrained to the memory arn (the service's control
        # actions authorize against "*"); the resource-scoped grant above produced the
        # observed "[catalog] strategy id resolve failed: AccessDeniedException ...
        # GetMemory". Kept as a separate, minimal wildcard statement.
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:GetMemory", "bedrock-agentcore:ListMemories"]
        Resource = "*"
      },
      {
        # AgentCore Code Interpreter — a drafter agent (e.g. portfolio_manager) builds its
        # declared file artifact (trade-decision-memo.html) by running Python in the managed
        # Code Interpreter sandbox (agent/src/main.py code_interpreter_tool). The Terraform-
        # created runtime role has none of these actions (the platform's runtime role is made
        # by the agentcore CLI toolkit, whose default policy includes them) — so the sandbox
        # call failed, no file was written, and the run finished DEGRADED with the deliverable
        # missing. Actions per AWS docs (code-interpreter resource management).
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:CreateCodeInterpreter",
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:DeleteCodeInterpreter",
          "bedrock-agentcore:ListCodeInterpreters",
          "bedrock-agentcore:GetCodeInterpreter",
          "bedrock-agentcore:GetCodeInterpreterSession",
          "bedrock-agentcore:ListCodeInterpreterSessions",
        ]
        # "*" covers BOTH a custom code-interpreter in this account AND the AWS-managed
        # built-in (aws.codeinterpreter.v1) the SDK uses by default — their resource ARNs
        # live in different namespaces, and an account-scoped ARN misses the built-in.
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.runs.arn}/*"
      },
      {
        # AgentCore Runtime assumes this role to pull the runtime image from ECR.
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = var.create_ecr ? aws_ecr_repository.runtime[0].arn : "*"
      },
      {
        # Observability (the runtime emits traces/logs).
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # Gateway M2M client secret (+ any tool credential secrets).
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = concat([aws_secretsmanager_secret.gateway_m2m.arn], values(var.tool_secret_arns))
      },
      {
        # Decrypt the CMK-encrypted secret.
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey"]
        Resource = local.kms_key_arn
      }
    ]
  })
}

// Shared cross-run memory namespaces (episodic/semantic strategies configured per eco).
resource "awscc_bedrockagentcore_memory" "eco" {
  name                  = replace("${local.name}-memory", "-", "_")
  description           = "Shared memory for ${var.eco_id}"
  event_expiry_duration = 90 # days to retain events
}

// MCP tool front door. Business tools (spec §5.4) are registered as targets against this
// gateway by a follow-on step; demo backends for v1 (O1).
resource "awscc_bedrockagentcore_gateway" "eco" {
  # Gateway name allows alphanumerics + single hyphens only (^([0-9a-zA-Z][-]?){1,48}$) —
  # NOT underscores (unlike Runtime/Memory). Keep hyphens, cap at 48, drop any trailing hyphen.
  name            = trimsuffix(substr("${local.name}-gw", 0, 48), "-")
  role_arn        = aws_iam_role.runtime.arn
  authorizer_type = "CUSTOM_JWT"
  protocol_type   = "MCP"

  authorizer_configuration = {
    custom_jwt_authorizer = {
      discovery_url = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.eco.id}/.well-known/openid-configuration"
      allowed_clients = [
        aws_cognito_user_pool_client.eco.id,
        aws_cognito_user_pool_client.m2m.id,
      ]
    }
  }
}

// The single, generic agent runtime container (pinned by digest), configured per request.
resource "awscc_bedrockagentcore_runtime" "eco" {
  agent_runtime_name = replace(local.name, "-", "_")
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact = {
    container_configuration = {
      container_uri = var.runtime_image_uri
    }
  }

  network_configuration = {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    TESSERA_MEMORY_ID             = awscc_bedrockagentcore_memory.eco.id
    TESSERA_ECO_ID                = var.eco_id
    TESSERA_RUNS_BUCKET           = aws_s3_bucket.runs.bucket
    SYNTHESIZER_MODEL_ID          = lookup(var.model_ids, "synthesizer", "us.anthropic.claude-opus-4-7")
    LEAF_MODEL_ID                 = lookup(var.model_ids, "leaf", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    TESSERA_GATEWAY_URL           = awscc_bedrockagentcore_gateway.eco.gateway_url
    TESSERA_GATEWAY_CLIENT_ID     = aws_cognito_user_pool_client.m2m.id
    TESSERA_GATEWAY_TOKEN_URL     = "https://${aws_cognito_user_pool_domain.eco.domain}.auth.${var.region}.amazoncognito.com/oauth2/token"
    TESSERA_GATEWAY_SCOPE         = "tessera-gateway/invoke"
    TESSERA_GATEWAY_SECRET_ARN    = aws_secretsmanager_secret.gateway_m2m.arn
    WEBSEARCH_CREDENTIAL_PROVIDER = var.provision_web_search ? awscc_bedrockagentcore_api_key_credential_provider.websearch[0].name : ""
    WEBSEARCH_API_URL             = var.websearch_api_url
  }

  # The runtime references only aws_iam_role.runtime (the role), so Terraform does NOT
  # infer a dependency on the SEPARATE inline policy that grants ECR pull. AgentCore
  # validates the container image against the execution role AT CREATE time, so if the
  # runtime is created before the policy attaches/propagates it fails with "Access denied
  # while validating ECR URI ... requires ecr:GetAuthorizationToken/BatchGetImage/
  # GetDownloadUrlForLayer". Force the policy (and the ECR repo/image) to exist first.
  depends_on = [aws_iam_role_policy.runtime, aws_ecr_repository.runtime]
}
