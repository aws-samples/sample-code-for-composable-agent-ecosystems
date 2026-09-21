# ─────────────────────────────────────────────────────────────────────────────
# Cognito User Pool
#
# Self-registration is DISABLED (admin-create-only). The post-confirmation trigger
# is wired only if its Lambda ARN is supplied (it stays relevant only if self-signup
# is ever re-enabled). Cognito domain prefix is suffixed with the account id to avoid
# global-uniqueness collisions across regions/orgs.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_cognito_user_pool" "main" {
  name = var.name_prefix

  # By default self-registration is DISABLED (admin-create-only). Set allow_self_signup
  # = true (dev/demo) to let people create their own accounts from the login screen.
  admin_create_user_config {
    allow_admin_create_user_only = !var.allow_self_signup
  }

  # Sign-in configuration. We use ALIAS attributes (not username_attributes) so that
  # federated users get an opaque, IdP-prefixed username (e.g. AmazonFederate_<sub>) while
  # still being able to sign in / be looked up by email or preferred_username. This is what
  # makes Amazon Federate SSO work: Federate's OIDC token has no `email` claim and its `sub`
  # is a bare alias (e.g. "alias") — an email-keyed pool (username_attributes=["email"])
  # rejects that derived username with "username attribute mapping required", but an alias
  # pool accepts the opaque username and treats email as a (verified) sign-in alias.
  # NOTE: username_attributes vs alias_attributes is IMMUTABLE after pool creation — changing
  # it forces the pool (and everything keyed on its id) to be recreated.
  alias_attributes         = ["email", "preferred_username"]
  auto_verified_attributes = ["email"]

  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  # Trigger wiring. post_confirmation is wired whenever its ARN is supplied (self-signup
  # path). When federated login is enabled, the PreSignUp (email account-linking) and
  # PreTokenGeneration (default-to-Users) triggers are ALSO wired — to the auth-federated
  # module's Lambdas, referenced by their deterministic ARNs so there is no module cycle
  # (identity does not depend on auth-federated; auth-federated depends on identity's pool id).
  dynamic "lambda_config" {
    for_each = (var.post_confirmation_lambda_arn != "" || local.federated_enabled) ? [1] : []
    content {
      post_confirmation    = var.post_confirmation_lambda_arn != "" ? var.post_confirmation_lambda_arn : null
      pre_sign_up          = local.federated_enabled ? local.federated_presignup_arn : null
      pre_token_generation = local.federated_enabled ? local.federated_pretoken_arn : null
    }
  }

  tags = var.tags
}

locals {
  federated_enabled = var.federated_login.enabled

  # Deterministic ARNs for the auth-federated module's trigger Lambdas
  # (${name_prefix}-federated-presignup / -federated-pretoken). Referencing by ARN string
  # (not module output) keeps identity independent of auth-federated → no dependency cycle.
  federated_presignup_arn = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-federated-presignup"
  federated_pretoken_arn  = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-federated-pretoken"

  # OAuth callback/logout URLs (hosted-UI code flow returns to the app origin).
  oauth_callback_urls = var.frontend_url != "" ? ["${var.frontend_url}/"] : []
  oauth_logout_urls   = var.frontend_url != "" ? ["${var.frontend_url}/"] : []
}

# NOTE: Cognito's permission to invoke the post-confirmation trigger is granted in the
# compute module (where the function resource lives), scoped to userpool/* to avoid a
# module dependency cycle — mirroring the least-cross-coupling approach of the original.

# Frontend app client (public SPA — no secret, SRP auth). Callback/logout URLs are
# set by the frontend module after CloudFront exists (via a separate update path),
# so they are not hardcoded here.
resource "aws_cognito_user_pool_client" "frontend" {
  name         = "${var.project}-frontend"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret     = false
  explicit_auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  # OAuth / federated settings. When federated login is disabled the client stays purely
  # Cognito-local (SRP), with no OAuth flows or external IdPs. When enabled, the client
  # gains the authorization-code flow + the federated IdP so the hosted UI can complete
  # `signInWithRedirect`. The SAME client is used for native and federated sign-in, so all
  # backend token verifiers (which pin this client id as the audience) keep working.
  callback_urls                        = local.oauth_callback_urls
  logout_urls                          = local.oauth_logout_urls
  supported_identity_providers         = local.federated_enabled ? ["COGNITO", var.federated_login.provider_name] : ["COGNITO"]
  allowed_oauth_flows                  = local.federated_enabled ? ["code"] : []
  allowed_oauth_scopes                 = local.federated_enabled ? ["phone", "email", "openid", "profile"] : []
  allowed_oauth_flows_user_pool_client = local.federated_enabled

  # Shorter refresh window when SSO is on (matches the original reference solution).
  refresh_token_validity = 10
  access_token_validity  = 60
  id_token_validity      = 60
  token_validity_units {
    refresh_token = "hours"
    access_token  = "minutes"
    id_token      = "minutes"
  }

  # The federated IdP (below, same module) must exist before the client can list it as a
  # supported provider. Colocating both here lets Terraform order them within one apply.
  depends_on = [aws_cognito_identity_provider.federated]
}

# ── OIDC identity provider (colocated with the pool so the frontend client can reference
#    it in the SAME apply). Created only when federated login is enabled. The trigger
#    Lambdas + optional Users group remain in the auth-federated module. ──
data "aws_secretsmanager_secret_version" "federated" {
  count     = local.federated_enabled && var.federated_login.client_secret_arn != "" ? 1 : 0
  secret_id = var.federated_login.client_secret_arn
}

resource "aws_cognito_identity_provider" "federated" {
  count         = local.federated_enabled ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = var.federated_login.provider_name
  provider_type = "OIDC"

  provider_details = {
    client_id                     = var.federated_login.client_id != "" ? var.federated_login.client_id : "REPLACE_ME"
    client_secret                 = var.federated_login.client_secret_arn != "" ? data.aws_secretsmanager_secret_version.federated[0].secret_string : (var.federated_login.client_id != "" ? "REPLACE_ME" : "REPLACE_ME")
    oidc_issuer                   = var.federated_login.issuer
    authorize_scopes              = "openid"
    attributes_request_method     = "GET"
    attributes_url_add_attributes = "false"
  }

  # This pool uses ALIAS attributes, so the federated username is auto-derived from the OIDC
  # `sub` (IdP-prefixed, e.g. AmazonFederate_<sub>) and NO email claim is required for sign-in
  # to succeed. We still map email->email so that IdPs which DO emit an email populate the
  # user's email alias; for Amazon Federate (no email claim) this simply maps nothing, which
  # is fine — federated users sign in by their opaque username and are treated as admins by
  # the pre-token trigger. `username` is not mappable (Cognito locks it to `sub`).
  attribute_mapping = {
    email = "email"
  }
}

# NOTE: Federated login works purely through the standard PreSignUp (email-linking +
# auto-confirm) and PreTokenGeneration (group assignment) triggers wired in lambda_config
# above. There is no email-synthesis / InboundFederation hack — the alias-attributes pool
# accepts Amazon Federate's email-less identity directly. See docs/operations/federated-login.md.

# Cognito hosted-UI domain — required for the OAuth token endpoint (M2M) and any
# hosted-UI / federated flows. Suffixed with account id for global uniqueness.
resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.name_prefix}-${var.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}

# ── Gateway M2M auth (agent → AgentCore Gateway) ──────────────────────────────
# Resource server + custom scope the Gateway validates JWTs against.
resource "aws_cognito_resource_server" "gateway" {
  name         = "${var.project}-gateway"
  identifier   = "${var.project}-gateway"
  user_pool_id = aws_cognito_user_pool.main.id

  scope {
    scope_name        = "invoke"
    scope_description = "Invoke gateway tools"
  }
}

# M2M app client used by the agent runtime to fetch JWTs via client_credentials.
resource "aws_cognito_user_pool_client" "gateway_m2m" {
  name         = "${var.project}-gateway-m2m"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["${aws_cognito_resource_server.gateway.identifier}/invoke"]
  supported_identity_providers         = ["COGNITO"]

  # No user-flow auth — client_credentials only.
  explicit_auth_flows = []

  depends_on = [aws_cognito_resource_server.gateway]
}

# Store the M2M client secret in Secrets Manager so the agent runtime can retrieve
# it without env vars (zero static secrets). Unlike the original, no custom resource
# is needed — the client secret is available directly as a resource attribute.
resource "aws_secretsmanager_secret" "gateway_m2m" {
  #checkov:skip=CKV2_AWS_57:This is a Cognito app-client secret whose lifecycle is owned by the Cognito user-pool client, not an independently rotatable credential — automatic Secrets Manager rotation would desync it from Cognito. Rotation is handled by rotating the Cognito client. Accepted risk.
  name        = "${var.project}/gateway-m2m-client-secret"
  description = "Cognito M2M client secret for AgentCore Gateway auth"
  kms_key_id  = var.data_key_arn # Customer-managed KMS (CKV_AWS_149).
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "gateway_m2m" {
  secret_id     = aws_secretsmanager_secret.gateway_m2m.id
  secret_string = aws_cognito_user_pool_client.gateway_m2m.client_secret
}
