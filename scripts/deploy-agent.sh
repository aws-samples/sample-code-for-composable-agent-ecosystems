#!/usr/bin/env bash
# scripts/deploy-agent.sh — deploy the Tessera agent to Amazon Bedrock AgentCore Runtime.
#
# The Runtime is container-based and deployed with the `agentcore` CLI (which builds the
# source into a container, creates/updates the runtime + its execution role, and returns
# the runtime ARN). Runtime env vars are sourced from Terraform outputs + Secrets Manager
# — zero static secrets.
#
# On success it saves the runtime ARN so the follow-up `terraform apply` can wire the
# Lambdas to it WITHOUT copy/paste:
#   • infra/environments/<env>/agent.auto.tfvars  — auto-loaded by terraform apply.
#   • .deploy-logs/agent-runtime-arn.env           — `source` it for $AGENT_RUNTIME_ARN.
# It also prints `AGENT_RUNTIME_ARN=<arn>` on stdout (for CI / scripting).
#
# Fresh account: if .bedrock_agentcore.yaml is absent, runs `agentcore configure` first.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

AGENT_DIR="$APP_DIR/agent"
CONFIG=".bedrock_agentcore.yaml"
command -v agentcore >/dev/null 2>&1 || die "agentcore CLI not found (pip install bedrock-agentcore-starter-toolkit)"
[ -d "$AGENT_DIR" ] || die "agent source not found at $AGENT_DIR"
cd "$AGENT_DIR"

step "Resolving runtime config from Terraform outputs"
GATEWAY_URL=$(tf_output gateway_url)
MEMORY_ID=$(tf_output memory_id)
WEBSEARCH_PROVIDER=$(tf_output workload_identity_name) # provider name convention: <prefix>-websearch
ECO_BUCKET=$(tf_output ecosystems_bucket_name)
RUNS_BUCKET=$(tf_output runs_bucket_name)
NAME_PREFIX=$(tf_output name_prefix)
# Gateway M2M (machine-to-machine) OAuth config — the runtime uses these to fetch a JWT
# and discover the AgentCore Gateway's MCP tools. Without them the runtime raises
# "Gateway M2M auth not configured" and every agent invocation 500s.
GW_TOKEN_URL=$(tf_output gateway_m2m_token_url)
GW_CLIENT_ID=$(tf_output gateway_m2m_client_id)
GW_SCOPE=$(tf_output gateway_m2m_scope)
GW_SECRET_ARN=$(tf_output gateway_secret_arn)
[ -n "$GATEWAY_URL" ] || warn "gateway_url output empty — has 'terraform apply' run?"
[ -n "$GW_CLIENT_ID" ] && [ -n "$GW_SECRET_ARN" ] || warn "gateway M2M outputs empty — the runtime will fail Gateway tool discovery. Run 'terraform apply' first."

AGENT_NAME="${AGENT_NAME:-$(echo "${NAME_PREFIX:-tessera_${ENVIRONMENT}}" | tr '-' '_')_agent}"

# ── configure on a fresh account (fully non-interactive) ──
# --non-interactive (-ni) skips every wizard prompt and uses defaults, including the
# request-header-allowlist question. -rha "" pins the allowlist to empty (the [no] default)
# so it can never prompt. This also avoids the PTY/CPR terminal warning entirely.
if [ ! -f "$CONFIG" ]; then
  step "No $CONFIG — running 'agentcore configure' (fresh account, non-interactive)"
  agentcore configure \
    --non-interactive \
    -e src/main.py \
    -n "$AGENT_NAME" \
    -rha "" \
    ${AWS_REGION:+--region "$AWS_REGION"} || die "agentcore configure failed"
fi

step "Deploying agent (agentcore deploy)"
ENV_ARGS=(
  --env "TESSERA_GATEWAY_URL=$GATEWAY_URL"
  --env "WEBSEARCH_CREDENTIAL_PROVIDER=${NAME_PREFIX}-websearch"
  --env "TESSERA_MEMORY_ID=$MEMORY_ID"
  --env "TESSERA_ECOSYSTEMS_BUCKET=$ECO_BUCKET"
  --env "CATALOG_CONTENT_BUCKET=$ECO_BUCKET"
  --env "TESSERA_RUNS_BUCKET=$RUNS_BUCKET"
  # Gateway M2M auth — required for the runtime to fetch a JWT + discover Gateway tools.
  --env "TESSERA_GATEWAY_TOKEN_URL=$GW_TOKEN_URL"
  --env "TESSERA_GATEWAY_CLIENT_ID=$GW_CLIENT_ID"
  --env "TESSERA_GATEWAY_SCOPE=$GW_SCOPE"
  --env "TESSERA_GATEWAY_SECRET_ARN=$GW_SECRET_ARN"
)
# -auc (--auto-update-on-conflict) makes re-deploys idempotent: update the existing
# runtime instead of failing. `deploy` is non-interactive already, so no PTY hack needed.
agentcore deploy --auto-update-on-conflict "${ENV_ARGS[@]}" || die "agentcore deploy failed"

# Derive the runtime ARN from the generated config.
RUNTIME_ARN=$(grep -E '^\s*agent_arn:' "$CONFIG" | head -1 | awk '{print $2}')
[ -n "$RUNTIME_ARN" ] || die "could not derive runtime ARN from $CONFIG"

ok "Agent deployed."

# ── Grant the runtime role permission to read the Gateway M2M secret ─────────
# The agentcore toolkit creates/owns the runtime execution role (outside Terraform), and
# its default policy does NOT include Secrets Manager. The runtime must read the M2M
# client secret to fetch a Gateway JWT, so grant secretsmanager:GetSecretValue on that
# secret. Idempotent (put-role-policy overwrites); scoped to the tessera M2M secret only.
EXEC_ROLE_ARN=$(grep -E '^\s*execution_role:' "$CONFIG" | grep -v null | head -1 | awk '{print $2}')
EXEC_ROLE_NAME="${EXEC_ROLE_ARN##*/}"
if [ -n "$EXEC_ROLE_NAME" ] && [ -n "$GW_SECRET_ARN" ]; then
  step "Granting runtime role '$EXEC_ROLE_NAME' read on the Gateway M2M secret"
  # Wildcard the Secrets Manager 6-char suffix so the grant survives secret rotation.
  _secret_res="${GW_SECRET_ARN%-*}-*"
  awscli iam put-role-policy \
    --role-name "$EXEC_ROLE_NAME" \
    --policy-name "TesseraGatewayM2MSecretRead" \
    --policy-document "$(printf '{"Version":"2012-10-17","Statement":[{"Sid":"TesseraGatewayM2MSecretRead","Effect":"Allow","Action":"secretsmanager:GetSecretValue","Resource":"%s"}]}' "$_secret_res")" \
    >/dev/null 2>&1 && ok "secret-read policy attached" || warn "could not attach secret-read policy — the runtime may 500 on Gateway auth"
else
  warn "could not resolve runtime execution role or M2M secret ARN — skipping secret-read grant"
fi

# ── Grant the runtime role kms:Decrypt on the data CMK (encrypts the M2M secret) ──
# The M2M secret is encrypted with the customer-managed 'data' KMS key, so reading it
# requires kms:Decrypt on that key IN ADDITION to secretsmanager:GetSecretValue. Without
# this the runtime 500s on Gateway tool discovery with "Access to KMS is not allowed".
# Scoped by kms:ViaService = secretsmanager so it only permits decrypt through Secrets Manager.
DATA_KEY_ARN=$(tf_output data_key_arn)
if [ -n "$EXEC_ROLE_NAME" ] && [ -n "$DATA_KEY_ARN" ]; then
  step "Granting runtime role '$EXEC_ROLE_NAME' kms:Decrypt on the data key (for the M2M secret)"
  awscli iam put-role-policy \
    --role-name "$EXEC_ROLE_NAME" \
    --policy-name "TesseraDataKeyDecrypt" \
    --policy-document "$(printf '{"Version":"2012-10-17","Statement":[{"Sid":"TesseraDataKeyDecrypt","Effect":"Allow","Action":["kms:Decrypt","kms:DescribeKey"],"Resource":"%s","Condition":{"StringEquals":{"kms:ViaService":"secretsmanager.%s.amazonaws.com"}}}]}' "$DATA_KEY_ARN" "$AWS_REGION")" \
    >/dev/null 2>&1 && ok "data-key decrypt policy attached" || warn "could not attach data-key decrypt policy — the runtime may 500 on Gateway auth"
else
  warn "could not resolve runtime execution role or data key ARN — skipping data-key grant"
fi

# ── Grant the runtime role the Memory data-plane on the PLATFORM memory ──────
# The agentcore toolkit's default role grants memory actions only on the toolkit's own
# auto-created memory, but Tessera agents use the Terraform-created platform memory
# (TESSERA_MEMORY_ID). Without this, every memory read/write fails with AccessDenied
# (bedrock-agentcore:RetrieveMemoryRecords / BatchCreateMemoryRecords / CreateEvent),
# silently degrading cross-agent grounding + durable catalog knowledge. Idempotent.
if [ -n "$EXEC_ROLE_NAME" ] && [ -n "$MEMORY_ID" ]; then
  step "Granting runtime role '$EXEC_ROLE_NAME' memory data-plane on '$MEMORY_ID'"
  _mem_arn="arn:aws:bedrock-agentcore:${AWS_REGION}:${AWS_ACCOUNT_ID:-$(awscli sts get-caller-identity --query Account --output text 2>/dev/null)}:memory/${MEMORY_ID}"
  awscli iam put-role-policy \
    --role-name "$EXEC_ROLE_NAME" \
    --policy-name "TesseraPlatformMemoryDataPlane" \
    --policy-document "$(printf '{"Version":"2012-10-17","Statement":[{"Sid":"TesseraPlatformMemoryDataPlane","Effect":"Allow","Action":["bedrock-agentcore:CreateEvent","bedrock-agentcore:GetEvent","bedrock-agentcore:ListEvents","bedrock-agentcore:DeleteEvent","bedrock-agentcore:GetMemory","bedrock-agentcore:GetMemoryRecord","bedrock-agentcore:ListMemoryRecords","bedrock-agentcore:RetrieveMemoryRecords","bedrock-agentcore:BatchCreateMemoryRecords","bedrock-agentcore:BatchUpdateMemoryRecords","bedrock-agentcore:BatchDeleteMemoryRecords","bedrock-agentcore:ListActors","bedrock-agentcore:ListSessions","bedrock-agentcore:DeleteMemoryRecord"],"Resource":["%s","%s/*"]}]}' "$_mem_arn" "$_mem_arn")" \
    >/dev/null 2>&1 && ok "memory data-plane policy attached" || warn "could not attach memory policy — agents will hit AccessDenied on memory read/write"
else
  warn "could not resolve runtime execution role or MEMORY_ID — skipping memory grant"
fi

# ── Persist the runtime ARN so the terraform apply can consume it ────────────
# A child process can't set an env var in your parent shell, so we write the ARN to two
# places instead:
#
#   1) .deploy-logs/agent-runtime-arn.env  — a source-able KEY=value file. Run:
#        source .deploy-logs/agent-runtime-arn.env
#      to get $AGENT_RUNTIME_ARN in your current shell, then:
#        terraform -chdir=infra apply $TF_VARS -var agent_runtime_arn="$AGENT_RUNTIME_ARN"
#
#   2) infra/environments/<env>/agent.auto.tfvars  — Terraform auto-loads *.auto.tfvars,
#      so a plain `terraform apply` (from infra/, that env) picks up agent_runtime_arn
#      with NO extra flag. This is the simplest path.
ARN_ENV_FILE="$ROOT_DIR/.deploy-logs/agent-runtime-arn.env"
mkdir -p "$ROOT_DIR/.deploy-logs"
printf 'AGENT_RUNTIME_ARN=%s\n' "$RUNTIME_ARN" > "$ARN_ENV_FILE"

AUTO_TFVARS="$INFRA_DIR/environments/${ENVIRONMENT}/agent.auto.tfvars"
printf '# Written by scripts/deploy-agent.sh — the deployed AgentCore Runtime ARN.\nagent_runtime_arn = "%s"\n' \
  "$RUNTIME_ARN" > "$AUTO_TFVARS"

ok "Runtime ARN saved:"
log "  $ARN_ENV_FILE            (source it to get \$AGENT_RUNTIME_ARN)"
log "  $AUTO_TFVARS   (auto-loaded by terraform apply for '$ENVIRONMENT')"
log ""
log "Wire it into Terraform with either:"
log "  A) plain apply (auto-loaded):   cd infra && terraform apply \$TF_VARS"
log "  B) explicit var:                source .deploy-logs/agent-runtime-arn.env"
log "                                  cd infra && terraform apply \$TF_VARS -var agent_runtime_arn=\"\$AGENT_RUNTIME_ARN\""
log ""
# Machine-readable line (kept for CI / scripting that greps stdout).
echo "AGENT_RUNTIME_ARN=${RUNTIME_ARN}"
