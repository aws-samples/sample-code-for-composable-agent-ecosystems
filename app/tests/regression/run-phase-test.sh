#!/usr/bin/env bash
# tests/regression/run-phase-test.sh
#
# End-to-end Phase 5.3 regression test. Deploys the phase-test ecosystem,
# triggers a run, waits for completion, and runs the feature-coverage
# checker.
#
# Cost: ~1 web search provider search, ~$0.03 in Bedrock token spend, ~2 minutes wall time.
# Exits non-zero if any feature regressed.
#
# Usage:
#   bash tests/regression/run-phase-test.sh
#
set -euo pipefail

PROFILE="${AWS_PROFILE:-example-profile}"
REGION="${AWS_REGION:-us-east-1}"
ECO_ID="phase-test"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
YAML_PATH="$ROOT/tests/regression/phase-test.yaml"
CHECKER="$ROOT/tests/regression/check-features.py"

if [[ ! -f "$YAML_PATH" ]]; then
  echo "ERROR: $YAML_PATH not found." >&2
  exit 1
fi

echo "──────────────────────────────────────────────────────────────"
echo " Phase 5.3 regression test — phase-test ecosystem"
echo " Profile: $PROFILE  Region: $REGION"
echo "──────────────────────────────────────────────────────────────"

# 1. Deploy the ecosystem (save-ecosystem reads the YAML, compiles ASL, deploys SFN)
echo "[1/4] Deploying phase-test ecosystem..."
YAML_CONTENT=$(cat "$YAML_PATH")
PAYLOAD=$(mktemp -t phase-test-payload.XXXX.json)
RESP=$(mktemp -t phase-test-resp.XXXX.json)
trap 'rm -f "$PAYLOAD" "$RESP"' EXIT

python3 - "$YAML_CONTENT" "$ECO_ID" "$PAYLOAD" <<'PY'
import json, sys
yaml_content, eco_id, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
event = {
    "pathParameters": {"id": eco_id},
    "path": f"/ecosystems/{eco_id}",
    "body": json.dumps({"yaml": yaml_content}),
}
json.dump(event, open(out_path, "w"))
PY

aws lambda invoke \
  --function-name tessera-dev-save-ecosystem \
  --cli-binary-format raw-in-base64-out \
  --payload "fileb://$PAYLOAD" \
  --profile "$PROFILE" --region "$REGION" \
  "$RESP" >/dev/null

DEPLOY_STATUS=$(python3 -c "
import json
d = json.load(open('$RESP'))
b = json.loads(d.get('body','{}'))
print('OK' if b.get('deployed') else 'ERR: ' + b.get('error','?')[:120])
warnings = b.get('warnings', [])
if warnings:
    print(f'  warnings: {len(warnings)}')
    for w in warnings:
        print(f\"  - [{w.get('agent')}/{w.get('kind')}] {w.get('message')[:100]}\")
")
echo "  $DEPLOY_STATUS"
if [[ "$DEPLOY_STATUS" != OK* ]]; then
  exit 2
fi

# 2. Trigger run
echo ""
echo "[2/4] Starting Step Functions run..."
ACCOUNT=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
SM_ARN="arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:tessera-dev-${ECO_ID}"
EXEC_NAME="${ECO_ID}-$(date +%s | tail -c 6)"
INPUT="{\"mission\":\"Run all platform feature tests\",\"session_id\":\"${EXEC_NAME}\",\"input_context\":\"\",\"dependency_context\":\"\",\"results\":{}}"

EXEC_ARN=$(aws stepfunctions start-execution \
  --state-machine-arn "$SM_ARN" \
  --name "$EXEC_NAME" \
  --input "$INPUT" \
  --profile "$PROFILE" --region "$REGION" \
  --query executionArn --output text)
echo "  Execution: $EXEC_NAME"

# 3. Poll until complete (max 5 min)
echo ""
echo "[3/4] Waiting for completion (typically ~2 min)..."
START=$(date +%s)
for i in $(seq 1 20); do
  STATUS=$(aws stepfunctions describe-execution \
    --execution-arn "$EXEC_ARN" \
    --profile "$PROFILE" --region "$REGION" \
    --query status --output text)
  ELAPSED=$(( $(date +%s) - START ))
  printf "  t=%4ds  status=%s\n" "$ELAPSED" "$STATUS"
  if [[ "$STATUS" != "RUNNING" ]]; then break; fi
  sleep 15
done

if [[ "$STATUS" != "SUCCEEDED" ]]; then
  echo ""
  echo "❌ Execution did not succeed (status=$STATUS)"
  aws stepfunctions describe-execution --execution-arn "$EXEC_ARN" --profile "$PROFILE" --region "$REGION" --query 'cause' --output text | head -10
  exit 3
fi

# 4. Verify feature coverage
echo ""
echo "[4/4] Verifying feature coverage..."
# Activate venv if available (checker needs boto3 + pyyaml)
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  source "$ROOT/.venv/bin/activate"
fi
python3 "$CHECKER" --execution-arn "$EXEC_ARN" --yaml "$YAML_PATH" --profile "$PROFILE" --region "$REGION"
RESULT=$?

echo ""
if [[ $RESULT -eq 0 ]]; then
  echo "✅ Phase 5.3 regression test PASSED"
else
  echo "❌ Phase 5.3 regression test FAILED — see feature table above"
fi
exit $RESULT
