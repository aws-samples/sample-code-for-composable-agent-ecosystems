# ─────────────────────────────────────────────────────────────────────────────
# Optional federated (OIDC SSO) login — SIDE resources. Created only when
# config.enabled is true. This module owns the two Cognito trigger Lambdas
# (PreSignUp email-linking + auto-confirm, PreTokenGeneration group assignment) + IAM +
# Cognito invoke permission, and the default group federated users are placed into.
#
# The OIDC identity provider, the app-client OAuth settings, and the pool's trigger
# WIRING all live in the identity module (colocated with the pool + client so a single
# apply can order the IdP before the client references it). The identity module wires
# the pool triggers to THIS module's Lambdas by their deterministic ARNs, so there is no
# module dependency cycle (auth-federated depends on identity for the pool id; identity
# references these Lambda ARNs only as strings).
# ─────────────────────────────────────────────────────────────────────────────

locals {
  enabled  = var.config.enabled
  triggers = ["federated-presignup", "federated-pretoken"]

  pool_arn = "arn:${var.partition}:cognito-idp:${var.region}:${var.account_id}:userpool/${var.user_pool_id}"
}

# ── Trigger Lambdas ───────────────────────────────────────────────────────────
data "aws_iam_policy_document" "assume" {
  count = local.enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "archive_file" "trigger" {
  for_each    = local.enabled ? toset(local.triggers) : []
  type        = "zip"
  source_dir  = "${var.lambdas_src_dir}/${each.value}"
  output_path = "${path.module}/.src-archives/${each.value}.zip"
  excludes    = ["__pycache__", "requirements.txt"]
}

resource "aws_iam_role" "trigger" {
  for_each           = local.enabled ? toset(local.triggers) : []
  name               = "${var.name_prefix}-${each.value}"
  assume_role_policy = data.aws_iam_policy_document.assume[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy" "trigger" {
  for_each = local.enabled ? toset(local.triggers) : []
  name     = "grants"
  role     = aws_iam_role.trigger[each.value].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:${var.partition}:logs:${var.region}:${var.account_id}:log-group:/aws/lambda/${var.name_prefix}-${each.value}:*" },
        # KMS: decrypt/encrypt Lambda env vars (CKV_AWS_173).
        { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = var.lambda_env_key_arn },
        # DLQ: deliver failed async invocations (CKV_AWS_116).
        { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = var.lambda_dlq_arn },
      ],
      # federated-presignup: link federated identity to an existing user by email.
      each.value == "federated-presignup" ? [
        { Effect = "Allow", Action = ["cognito-idp:ListUsers", "cognito-idp:AdminLinkProviderForUser"], Resource = local.pool_arn },
      ] : [],
      # federated-pretoken: place federated users into the admin group AND persist a
      # synthesized email/name identity (Federate emits neither) so the profile UI works.
      each.value == "federated-pretoken" ? [
        { Effect = "Allow", Action = ["cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminUpdateUserAttributes"], Resource = local.pool_arn },
      ] : [],
    )
  })
}

resource "aws_lambda_function" "trigger" {
  #checkov:skip=CKV_AWS_115:Reserved (function-level) concurrency is intentionally unset — these are Cognito trigger Lambdas invoked synchronously during sign-up/token flows; a reserved limit would throttle logins. Accepted risk.
  #checkov:skip=CKV_AWS_117:Cognito trigger Lambdas run outside the VPC (they only call the Cognito control plane); VPC attachment would add NAT cost/cold-start with no benefit. Accepted risk.
  #checkov:skip=CKV_AWS_272:Code-signing requires a Signer profile + signed artifacts (pipeline change out of scope). Artifacts are integrity-checked via source hashes. Accepted risk.
  for_each = local.enabled ? toset(local.triggers) : []

  function_name    = "${var.name_prefix}-${each.value}"
  role             = aws_iam_role.trigger[each.value].arn
  runtime          = var.lambda_runtime
  handler          = "handler.handler"
  filename         = fileexists("${var.build_dir}/${each.value}.zip") ? "${var.build_dir}/${each.value}.zip" : data.archive_file.trigger[each.value].output_path
  source_code_hash = fileexists("${var.build_dir}/${each.value}.zip") ? filebase64sha256("${var.build_dir}/${each.value}.zip") : data.archive_file.trigger[each.value].output_base64sha256
  timeout          = 10
  memory_size      = 256
  kms_key_arn      = var.lambda_env_key_arn # CMK env encryption (CKV_AWS_173)

  dead_letter_config {
    target_arn = var.lambda_dlq_arn # (CKV_AWS_116)
  }

  environment {
    variables = (
      each.value == "federated-presignup" ? { FEDERATED_PROVIDER_NAME = var.config.provider_name } :
      { DEFAULT_GROUP = var.config.default_group, FEDERATED_EMAIL_DOMAIN = var.config.email_domain }
    )
  }

  # Active X-Ray tracing (CKV_AWS_50).
  tracing_config {
    mode = "Active"
  }

  tags = var.tags
}

# Allow Cognito to invoke both triggers.
resource "aws_lambda_permission" "trigger" {
  for_each      = local.enabled ? toset(local.triggers) : []
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.trigger[each.value].function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = local.pool_arn
}

# ── Default group for federated users ─────────────────────────────────────────
# The pre-token trigger places every federated user into var.config.default_group. For
# this deployment that group is "admin" (all Amazon Federate users are administrators), so
# we ensure the group exists here. (Kept generic via default_group so the same module can
# default federated users into any group.)
resource "aws_cognito_user_group" "users" {
  count        = local.enabled && var.config.create_users_group ? 1 : 0
  name         = var.config.default_group
  user_pool_id = var.user_pool_id
  description  = "Default group assigned to all federated (SSO) users."
}

# NOTE: the OIDC identity provider itself is created in the IDENTITY module (colocated
# with the user pool + frontend app client) so the client can reference it as a supported
# provider within a single apply. This module owns only the trigger Lambdas + optional
# group; the identity module owns the pool, client, IdP, and trigger wiring.
