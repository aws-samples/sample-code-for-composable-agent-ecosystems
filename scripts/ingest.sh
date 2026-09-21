#!/usr/bin/env bash
# scripts/ingest.sh — (OPTIONAL) build + populate the catalog for one or more domains.
#
# Runs the per-domain versioned BUILD state machine end to end:
#   new_version -> ingest_all -> [plan_synthesis -> Map(per cluster: get_mission ->
#   <name_prefix>-<synthesis_method> ecosystem SM)] -> validate_version
# then PROMOTES the freshly built version (so catalog readers/UI see it) when the
# validation coverage gate passes. This is the complete catalog build — it produces BOTH
# the deterministically-ingested types AND the SYNTHESIZED types (e.g. `risk`).
#
# Why a state machine (not a direct ingest_all): risk-style types are synthesized by a
# per-cluster fan-out that invokes a Bedrock-backed ecosystem SM. Only the build SM runs
# that stage; a bare ingest_all yields a catalog with no synthesized records.
#
# LONG-RUNNING: synthesis invokes Bedrock agents per tactic-cluster — expect several
# minutes to tens of minutes per synthesis-enabled domain. Idempotent / safely re-runnable.
#
# Prerequisites:
#   • `make seed` first (publishes content + compiles the synthesis ecosystem SM), and
#   • Bedrock model access enabled for the region (synthesis agents call Bedrock).
#
#   bash scripts/ingest.sh                       # default domains
#   bash scripts/ingest.sh --domains "a b c"     # specific domains
#   POLL_TIMEOUT_MIN=90 bash scripts/ingest.sh   # override the wait cap (default 60)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

DOMAINS_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domains) shift; DOMAINS_OVERRIDE="${1:-}" ;;
    -*) die "unknown flag: $1" ;;
  esac
  shift
done

NAME_PREFIX=$(tf_output name_prefix)
[ -n "$NAME_PREFIX" ] || die "no name_prefix output — is the platform deployed? (run terraform apply)"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="$(awscli sts get-caller-identity --query Account --output text 2>/dev/null)" || die "AWS creds invalid/expired"
GENERIC_FN="${NAME_PREFIX}-catalog-ingest-generic"
POLL_TIMEOUT_MIN="${POLL_TIMEOUT_MIN:-60}"

DOMAINS="${DOMAINS_OVERRIDE:-cyber-security-risk au-financial-advice scrutineer}"
failed=0

for d in $DOMAINS; do
  SM_ARN="arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:${NAME_PREFIX}-catalog-build-${d}"
  step "Building catalog for '$d' (ingest + synthesis + validate, then promote)"

  exec_arn="$(awscli stepfunctions start-execution --state-machine-arn "$SM_ARN" \
      --name "build-$(date +%s)-${RANDOM}" --input '{}' \
      --query executionArn --output text 2>/dev/null)" \
    || { err "could not start build SM for '$d' — does '$SM_ARN' exist? (terraform apply?)"; failed=1; continue; }
  log "  execution started; polling up to ${POLL_TIMEOUT_MIN}m (synthesis runs Bedrock agents)…"

  deadline=$(( $(date +%s) + POLL_TIMEOUT_MIN * 60 ))
  status="RUNNING"
  while [ "$status" = "RUNNING" ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      warn "  '$d' build still RUNNING after ${POLL_TIMEOUT_MIN}m — it continues in AWS."
      warn "  Check Step Functions console; re-run 'make ingest DOMAINS=\"$d\"' later (idempotent) to finish + promote."
      failed=1; break
    fi
    sleep 15
    status="$(awscli stepfunctions describe-execution --execution-arn "$exec_arn" --query status --output text 2>/dev/null || echo RUNNING)"
  done
  [ "$status" = "RUNNING" ] && continue

  if [ "$status" != "SUCCEEDED" ]; then
    err "  build for '$d' ended: $status"
    awscli stepfunctions describe-execution --execution-arn "$exec_arn" \
      --query '{error:error,cause:cause}' --output json 2>&1 | sed 's/^/      /' >&2
    failed=1; continue
  fi

  # Parse the execution output for the built version + validation result. NOTE: the output
  # is written to a temp FILE and passed as argv — `python3 - <<'PY'` already takes its
  # script from stdin, so stdin can't also carry the JSON (that was a real bug: it parsed
  # the script as JSON and silently skipped promotion).
  out_file="$(mktemp)"
  awscli stepfunctions describe-execution --execution-arn "$exec_arn" --query output --output text > "$out_file" 2>/dev/null
  eval "$(python3 - "$out_file" <<'PY'
import json, sys
try:
    o = json.load(open(sys.argv[1]))
except Exception:
    print('VERSION=""; COVERAGE_OK=""; SUMMARY="could not parse build output"'); sys.exit(0)
ver = (((o.get("ver") or {}).get("Payload") or {}).get("version")) or ""
val = ((o.get("validate") or {}).get("Payload")) or {}
cov = val.get("coverage_ok")
cov_s = "yes" if cov is True else ("no" if cov is False else "n/a")
covd = val.get("coverage") or {}
parts = ",".join(f'{t}={c.get("total")}(ok={c.get("ok")})' for t, c in covd.items()) or "none"
print(f'VERSION="{ver}"')
print(f'COVERAGE_OK="{cov_s}"')
print(f'SUMMARY="version={ver} coverage_ok={cov_s} synthesized[{parts}] identical={val.get("identical")}"')
PY
)"
  rm -f "$out_file"
  ok "  build SUCCEEDED ($SUMMARY)"

  [ -n "$VERSION" ] || { warn "  no version in build output for '$d' — skipping promote"; failed=1; continue; }

  # Promote when the coverage gate passed, OR when there is no active version yet (first
  # build — nothing to lose, and readers otherwise see an empty legacy namespace).
  active="$(awscli lambda invoke --function-name "$GENERIC_FN" \
    --payload "$(printf '{"action":"active","domain":"%s"}' "$d")" \
    --cli-binary-format raw-in-base64-out --cli-read-timeout 60 /dev/stdout 2>/dev/null \
    | python3 -c 'import json,sys
try: print((json.load(sys.stdin) or {}).get("active") or "")
except Exception: print("")' 2>/dev/null)"

  if [ "$COVERAGE_OK" = "yes" ] || [ -z "$active" ]; then
    reason=$([ "$COVERAGE_OK" = "yes" ] && echo "coverage gate passed" || echo "first build; no active version yet")
    prom="$(awscli lambda invoke --function-name "$GENERIC_FN" \
      --payload "$(printf '{"action":"promote","domain":"%s","version":"%s"}' "$d" "$VERSION")" \
      --cli-binary-format raw-in-base64-out --cli-read-timeout 120 /dev/stdout 2>/dev/null)"
    if printf '%s' "$prom" | grep -q '"active"'; then
      ok "  promoted '$d' -> $VERSION ($reason). Catalog is now live."
    else
      err "  promote of '$d' $VERSION did not confirm: $prom"; failed=1
    fi
  else
    warn "  '$d' built version $VERSION but coverage gate FAILED (synthesized types below threshold)."
    warn "  NOT promoting (would publish an incomplete catalog). Likely cause: synthesis produced too few records"
    warn "  (check Bedrock model access + the ${NAME_PREFIX}-${d} synthesis runs). Re-run when fixed — idempotent."
    failed=1
  fi
done

[ "$failed" -eq 0 ] || die "catalog build/ingest reported problems (see above). Safely re-runnable."
ok "Catalog build complete."
