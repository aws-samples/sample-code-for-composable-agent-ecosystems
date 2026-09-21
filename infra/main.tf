# ─────────────────────────────────────────────────────────────────────────────
# Tessera — root module. Wires the nine infrastructure modules together.
#
# Dependency order (top → bottom):
#   foundation → data → frontend → identity → agentcore → compute → api → orchestration
#              → auth-federated
# (identity depends on frontend for the OAuth callback origin when federated login is on;
#  auth-federated depends on identity for the pool id. No cycles.)
#
# Modules are added incrementally as they are implemented. Cross-module values are
# passed explicitly (no implicit global state); every module takes name_prefix,
# tags, and the runtime-resolved account/region it needs.
# ─────────────────────────────────────────────────────────────────────────────

module "foundation" {
  source = "./modules/foundation"

  name_prefix        = local.name_prefix
  tags               = local.common_tags
  vpc_max_azs        = var.vpc_max_azs
  vpc_nat_gateways   = var.vpc_nat_gateways
  log_retention_days = var.log_retention_days
}

module "data" {
  source = "./modules/data"

  name_prefix  = local.name_prefix
  tags         = local.common_tags
  data_key_arn = module.foundation.data_key_arn
}

module "identity" {
  source = "./modules/identity"

  name_prefix = local.name_prefix
  project     = var.project
  environment = var.environment
  tags        = local.common_tags
  account_id  = local.account_id
  region      = local.region
  partition   = local.partition

  # Post-confirmation trigger wired by deterministic function-name convention to avoid
  # a module dependency cycle (compute depends on identity for the pool id). The
  # function is created by the compute module with this exact name.
  post_confirmation_lambda_arn = "arn:${local.partition}:lambda:${local.region}:${local.account_id}:function:${local.name_prefix}-post-confirmation"

  # Federated login (OIDC SSO). When enabled, the identity module creates the OIDC IdP,
  # configures the frontend app client for the hosted-UI code flow, and wires the pool's
  # PreSignUp/PreTokenGeneration triggers to the auth-federated module's Lambdas (by their
  # deterministic ARNs). frontend_url provides the OAuth callback/logout origin.
  federated_login = var.federated_login
  frontend_url    = module.frontend.frontend_url

  # Self-registration ("Create account" on the login screen). Off by default.
  allow_self_signup = var.allow_self_signup

  # Demo open access: all users can view/run every ecosystem (AVP). Off by default.
  demo_open_access = var.demo_open_access

  # CMK for the gateway M2M secret (Secrets Manager).
  data_key_arn = module.foundation.data_key_arn
}

module "compute" {
  source = "./modules/compute"

  name_prefix = local.name_prefix
  project     = var.project
  environment = var.environment
  tags        = local.common_tags
  account_id  = local.account_id
  region      = local.region
  partition   = local.partition

  lambda_runtime     = var.lambda_runtime
  log_retention_days = var.log_retention_days
  lambda_max_memory  = var.lambda_max_memory

  lambdas_src_dir = "${local.app_dir}/lambdas"
  build_dir       = abspath("${path.root}/../build/lambdas")

  # Foundation
  lambda_env_key_arn       = module.foundation.lambda_env_key_arn
  logs_key_arn             = module.foundation.logs_key_arn
  data_key_arn             = module.foundation.data_key_arn
  lambda_dlq_arn           = module.foundation.lambda_dlq_arn
  vpc_private_subnet_ids   = module.foundation.private_subnet_ids
  lambda_security_group_id = module.foundation.lambda_security_group_id
  ecosystems_bucket_name   = module.foundation.ecosystems_bucket_name
  ecosystems_bucket_arn    = module.foundation.ecosystems_bucket_arn
  runs_bucket_name         = module.foundation.runs_bucket_name
  runs_bucket_arn          = module.foundation.runs_bucket_arn
  exports_bucket_name      = module.foundation.exports_bucket_name
  exports_bucket_arn       = module.foundation.exports_bucket_arn

  # Data
  connections_table_name = module.data.connections_table_name
  connections_table_arn  = module.data.connections_table_arn
  attention_table_name   = module.data.attention_table_name
  attention_table_arn    = module.data.attention_table_arn
  regrounding_bus_name   = module.data.regrounding_bus_name

  # Identity
  user_pool_id     = module.identity.user_pool_id
  user_pool_arn    = module.identity.user_pool_arn
  policy_store_id  = module.identity.policy_store_id
  policy_store_arn = module.identity.policy_store_arn

  # AgentCore
  agent_runtime_arn = var.agent_runtime_arn
  memory_id         = module.agentcore.memory_id
  memory_arn        = module.agentcore.memory_arn
  guardrail_id      = module.agentcore.guardrail_id

  # API (api → compute): invoke permissions + WebSocket callback URL.
  rest_api_id             = module.api.rest_api_id
  websocket_api_id        = module.api.websocket_api_id
  websocket_authorizer_id = module.api.websocket_authorizer_id
  websocket_callback_url  = module.api.websocket_callback_url
  rest_function_keys      = module.api.rest_functions

  # Config-driven values
  models                   = var.models
  admin_notify_emails      = var.admin_notify_emails
  attention_email          = var.attention_email
  signup_require_approval  = var.signup_require_approval
  demo_open_access         = var.demo_open_access
  websearch_provider_name  = local.websearch_provider_name
  quorum_run_function_name = "${local.name_prefix}-quorum-run"
  enable_quorum            = var.enable_quorum

  # Restrict user-facing Lambda CORS to the CloudFront origin.
  frontend_cors_origin = module.frontend.frontend_url
}

module "api" {
  source = "./modules/api"

  name_prefix        = local.name_prefix
  environment        = var.environment
  tags               = local.common_tags
  account_id         = local.account_id
  region             = local.region
  partition          = local.partition
  logs_key_arn       = module.foundation.logs_key_arn
  log_retention_days = var.log_retention_days
  user_pool_arn      = module.identity.user_pool_arn
}

module "orchestration" {
  source = "./modules/orchestration"

  name_prefix        = local.name_prefix
  project            = var.project
  environment        = var.environment
  tags               = local.common_tags
  account_id         = local.account_id
  region             = local.region
  partition          = local.partition
  lambda_runtime     = var.lambda_runtime
  log_retention_days = var.log_retention_days
  lambda_max_memory  = var.lambda_max_memory

  lambdas_src_dir  = "${local.app_dir}/lambdas"
  build_dir        = abspath("${path.root}/../build/lambdas")
  shared_layer_arn = module.compute.shared_layer_arn

  # Foundation
  lambda_env_key_arn       = module.foundation.lambda_env_key_arn
  logs_key_arn             = module.foundation.logs_key_arn
  data_key_arn             = module.foundation.data_key_arn
  lambda_dlq_arn           = module.foundation.lambda_dlq_arn
  vpc_private_subnet_ids   = module.foundation.private_subnet_ids
  lambda_security_group_id = module.foundation.lambda_security_group_id
  ecosystems_bucket_name   = module.foundation.ecosystems_bucket_name
  ecosystems_bucket_arn    = module.foundation.ecosystems_bucket_arn
  runs_bucket_name         = module.foundation.runs_bucket_name
  runs_bucket_arn          = module.foundation.runs_bucket_arn

  # Data
  catalog_refresh_table_name = module.data.catalog_refresh_table_name
  catalog_refresh_table_arn  = module.data.catalog_refresh_table_arn
  connections_table_name     = module.data.connections_table_name
  connections_table_arn      = module.data.connections_table_arn
  regrounding_bus_name       = module.data.regrounding_bus_name
  regrounding_bus_arn        = module.data.regrounding_bus_arn

  # Identity
  cognito_user_pool_id = module.identity.user_pool_id
  cognito_client_id    = module.identity.frontend_client_id
  cognito_issuer_url   = module.identity.cognito_issuer_url
  policy_store_id      = module.identity.policy_store_id

  # AgentCore
  memory_id  = module.agentcore.memory_id
  memory_arn = module.agentcore.memory_arn

  # API
  websocket_api_id       = module.api.websocket_api_id
  websocket_callback_url = module.api.websocket_callback_url

  # Config
  models                 = var.models
  allowed_origin         = module.frontend.frontend_url
  default_catalog_domain = var.default_catalog_domain
  enable_quorum          = var.enable_quorum
}

module "agentcore" {
  source = "./modules/agentcore"

  name_prefix = local.name_prefix
  project     = var.project
  environment = var.environment
  tags        = local.common_tags
  account_id  = local.account_id
  region      = local.region
  partition   = local.partition

  memory_payloads_bucket_name = module.foundation.memory_payloads_bucket_name
  memory_payloads_bucket_arn  = module.foundation.memory_payloads_bucket_arn

  cognito_issuer_url    = module.identity.cognito_issuer_url
  gateway_m2m_client_id = module.identity.gateway_m2m_client_id

  models              = var.models
  event_expiry_days   = 30
  grounding_threshold = 0.7
  relevance_threshold = 0.7
}

module "frontend" {
  source = "./modules/frontend"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  name_prefix            = local.name_prefix
  tags                   = local.common_tags
  access_logs_bucket_id  = module.foundation.access_logs_bucket_id
  access_logs_bucket_arn = module.foundation.access_logs_bucket_arn
}

module "auth_federated" {
  source = "./modules/auth-federated"

  name_prefix     = local.name_prefix
  project         = var.project
  environment     = var.environment
  tags            = local.common_tags
  account_id      = local.account_id
  region          = local.region
  partition       = local.partition
  lambda_runtime  = var.lambda_runtime
  lambdas_src_dir = "${local.app_dir}/lambdas"
  build_dir       = abspath("${path.root}/../build/lambdas")

  user_pool_id = module.identity.user_pool_id
  config       = var.federated_login

  # CMK env encryption (CKV_AWS_173) + DLQ (CKV_AWS_116).
  lambda_env_key_arn = module.foundation.lambda_env_key_arn
  lambda_dlq_arn     = module.foundation.lambda_dlq_arn
}

# NOTE: Federated login no longer needs an InboundFederation email-synthesis trigger. The
# Cognito pool now uses alias attributes, so Amazon Federate's email-less OIDC identity is
# accepted directly (opaque IdP-prefixed username; email is a sign-in alias). Only the
# standard PreSignUp + PreTokenGeneration triggers (auth-federated module) are used.

# ─────────────────────────────────────────────────────────────────────────────
# Ecosystem Evaluation Harness — the eval Lambdas + Bedrock eval role + online-eval
# trigger. The per-eco Eval State Machine is compiled at runtime by save-ecosystem when
# an ecosystem declares an `evaluation:` block. See docs/spec-ecosystem-evaluation.md.
# ─────────────────────────────────────────────────────────────────────────────
module "eval" {
  source = "./modules/eval"

  name_prefix       = local.name_prefix
  project           = var.project
  environment       = var.environment
  tags              = local.common_tags
  account_id        = local.account_id
  region            = local.region
  partition         = local.partition
  lambda_runtime    = var.lambda_runtime
  lambda_max_memory = var.lambda_max_memory
  lambdas_src_dir   = "${local.app_dir}/lambdas"
  build_dir         = abspath("${path.root}/../build/lambdas")

  shared_layer_arn       = module.compute.shared_layer_arn
  ecosystems_bucket_name = module.foundation.ecosystems_bucket_name
  ecosystems_bucket_arn  = module.foundation.ecosystems_bucket_arn
  runs_bucket_name       = module.foundation.runs_bucket_name
  runs_bucket_arn        = module.foundation.runs_bucket_arn

  # CMK env encryption (CKV_AWS_173) + DLQ (CKV_AWS_116).
  lambda_env_key_arn = module.foundation.lambda_env_key_arn
  lambda_dlq_arn     = module.foundation.lambda_dlq_arn
}
