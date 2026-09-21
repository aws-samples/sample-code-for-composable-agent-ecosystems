#!/usr/bin/env python3
"""tests/regression/check-features.py

Verifies that a Phase 4.5 regression test execution exercised every platform
feature it was designed to test. Reads the SFN execution history, fetches per-
agent outputs from S3, and prints a feature-coverage table.

Exit code 0 if every feature passed, 1 otherwise.

Usage:
    python3 tests/regression/check-features.py \
        --execution-arn arn:aws:states:us-east-1:...:execution:tessera-dev-phase-test:phase-test-NNN \
        --yaml tests/regression/phase-test.yaml \
        [--profile example-profile] [--region us-east-1]
"""
import argparse
import json
import re
import sys

import boto3
import yaml as yamlmod


def fetch_s3(s3, uri: str) -> str:
    if not uri or not uri.startswith("s3://"):
        return ""
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8", errors="replace")


def find_state_for_succeeded(by_id, succ):
    cur = succ
    for _ in range(20):
        pid = cur.get("previousEventId")
        if not pid:
            return None
        prev = by_id.get(pid)
        if not prev:
            return None
        if prev["type"] == "TaskStateEntered":
            return prev.get("stateEnteredEventDetails", {}).get("name")
        cur = prev
    return None


def collect_agent_outputs(events):
    by_id = {e["id"]: e for e in events}
    out = {}
    for ev in events:
        if ev["type"] != "TaskSucceeded":
            continue
        sn = find_state_for_succeeded(by_id, ev)
        if not sn:
            continue
        try:
            d = json.loads(ev["taskSucceededEventDetails"]["output"])
        except Exception:
            continue
        p = d.get("Payload", d) if isinstance(d, dict) else d
        if isinstance(p, dict) and "output_s3_uri" in p:
            aid = p.get("agent_id") or sn
            out.setdefault(aid, p)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-arn", required=True)
    parser.add_argument("--yaml", required=True)
    parser.add_argument("--profile", default="example-profile")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    sfn = sess.client("stepfunctions")
    s3 = sess.client("s3")

    # Load YAML for depends_on graph
    y = yamlmod.safe_load(open(args.yaml))
    deps = {a["id"]: a.get("depends_on", []) or [] for a in y.get("agents", [])}

    # Pull execution history (paginated)
    paginator = sfn.get_paginator("get_execution_history")
    events = []
    for page in paginator.paginate(executionArn=args.execution_arn):
        events.extend(page["events"])

    agent_outs = collect_agent_outputs(events)

    # Pull tool calls from S3 per agent
    tool_calls = {}
    for aid, p in agent_outs.items():
        try:
            raw = fetch_s3(s3, p.get("tool_results_s3_uri", ""))
            tool_calls[aid] = json.loads(raw) if raw else []
        except Exception:
            tool_calls[aid] = []

    # State names that ran
    states_seen = set()
    for ev in events:
        if ev["type"] == "TaskStateEntered":
            states_seen.add(ev.get("stateEnteredEventDetails", {}).get("name", ""))

    # Were role_contracts visible in scheduled task params?
    role_contract_found = False
    for ev in events:
        if ev["type"] == "TaskScheduled":
            try:
                params = json.loads(ev.get("taskScheduledEventDetails", {}).get("parameters", "{}"))
                payload = params.get("Payload", {})
                if "role_contract" in payload.get("agent_config", {}):
                    role_contract_found = True
                    break
            except Exception:
                pass

    results = []

    def check(name, cond, detail=""):
        results.append((name, cond, detail))

    # 1. ASL compiler — parallel layers ran
    check("ASL compiler — parallel layers ran",
          any("Layer" in s or s in deps for s in states_seen))

    # 2. Amazon S3 staging
    check("S3 staging — output_s3_uri returned by every agent",
          all(p.get("output_s3_uri", "").startswith("s3://") for p in agent_outs.values()))

    # 3. memory_write single-segment
    writer_writes = [tr for tr in tool_calls.get("writer", []) if tr.get("name") == "memory_write"]
    check("memory_write (single-segment path /tessera/test)",
          bool(writer_writes) and "eventId" in str(writer_writes[0].get("result", "")))

    # 4. memory_write cross-eco
    cross_writes = [tr for tr in tool_calls.get("cross_writer", []) if tr.get("name") == "memory_write"]
    check("memory_write (cross-eco path /tessera/catalog/phase-test)",
          bool(cross_writes) and "eventId" in str(cross_writes[0].get("result", "")))

    # 5. memory_read prefix-mode (the just-fixed bug)
    reader_reads = [tr for tr in tool_calls.get("reader", []) if tr.get("name") == "memory_read"]
    found_data = 0
    for r in reader_reads:
        result = r.get("result", "")
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            if isinstance(parsed, list) and len(parsed) > 0:
                found_data = len(parsed)
                break
        except Exception:
            pass
    check("memory_read prefix-mode (single-segment returns content)",
          found_data > 0, f"reader saw {found_data} entries")

    # 6. memory_read cross-eco
    composer_reads = [tr for tr in tool_calls.get("composer", []) if tr.get("name") == "memory_read"]
    cross_read_count = 0
    for r in composer_reads:
        result = r.get("result", "")
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            if isinstance(parsed, list) and len(parsed) > 0:
                cross_read_count = len(parsed)
                break
        except Exception:
            pass
    check("memory_read cross-eco path /tessera/catalog/phase-test",
          cross_read_count > 0, f"composer saw {cross_read_count} entries")

    # 7. web search provider Bearer auth
    cross_searches = [tr for tr in tool_calls.get("cross_writer", []) if tr.get("name") == "web_search"]
    websearch_ok = False
    if cross_searches:
        s = str(cross_searches[0].get("result", ""))
        # Check for structural errors (JSON error field), not substring in web content
        try:
            parsed = json.loads(cross_searches[0].get("result", "[]"))
            if isinstance(parsed, list) and len(parsed) > 0 and "url" in str(parsed[0]):
                websearch_ok = True
            elif isinstance(parsed, dict) and "error" in parsed:
                websearch_ok = False
            else:
                websearch_ok = bool(parsed)
        except (json.JSONDecodeError, ValueError):
            websearch_ok = "error" not in s[:100].lower()
    check("web search provider web_search Bearer auth",
          websearch_ok, f"{len(cross_searches)} call(s)")

    # 8. role_contract injection
    check("Compiler role_contract injection (in agent_config)",
          role_contract_found)

    # 9 & 10. Citation contract per synthesizer
    for aid in ("reader", "composer"):
        if aid not in agent_outs:
            continue
        findings = (agent_outs[aid].get("structured") or {}).get("findings", "") or ""
        deps_list = deps.get(aid, [])
        has_novel = bool(re.search(r"\[novel\s+[-—]", findings, re.IGNORECASE))
        all_cite = all(
            re.search(rf"\[per\s+{re.escape(u)}\b", findings, re.IGNORECASE)
            for u in deps_list
        )
        check(f"Citation contract — {aid} cites all upstream",
              all_cite or has_novel,
              f"deps={deps_list}, has_novel={has_novel}")

    # 11. Citation validator (run not failed by it)
    failed = any(ev["type"] == "ExecutionFailed" for ev in events)
    check("Citation validator (agent-invoker) — run did not fail on missing cites",
          not failed)

    # 12. Internal grounding ran
    check("Internal grounding (Bedrock Guardrail) — Grounding_* state ran",
          any("Grounding_" in s for s in states_seen))

    # 13. generateUI tool calls
    gen_ui = sum(len([t for t in tcs if t.get("name") == "generateUI"]) for tcs in tool_calls.values())
    check("generateUI tool calls", gen_ui > 0, f"{gen_ui} widget renders")

    # 14. Tool budget enforcement — budget_tester agent should have hit the cap
    budget_tester_calls = tool_calls.get("budget_tester", [])
    saw_exhausted = False
    saw_budget_meta = False
    for tr in budget_tester_calls:
        result = tr.get("result", "")
        s = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
        if "tool_budget_exhausted" in s:
            saw_exhausted = True
        if '"_budget"' in s or "'_budget'" in s:
            saw_budget_meta = True
    check("Tool budget exhaustion fires structured error",
          saw_exhausted, "budget_tester saw tool_budget_exhausted on 3rd memory_write")
    check("Tool result _budget metadata present",
          saw_budget_meta, "successful calls include {used, cap, remaining}")

    # 15. agent_config.tool_budget injected by compiler
    tool_budget_found = False
    for ev in events:
        if ev["type"] == "TaskScheduled":
            try:
                params = json.loads(ev.get("taskScheduledEventDetails", {}).get("parameters", "{}"))
                payload = params.get("Payload", {})
                if payload.get("agent_config", {}).get("tool_budget"):
                    tool_budget_found = True
                    break
            except Exception:
                pass
    check("Compiler tool_budget injection (in agent_config)", tool_budget_found)

    # 17-20. Phase 5.3 platform tools — platform_tools_tester agent
    pt_calls = tool_calls.get("platform_tools_tester", [])

    def _pt_tool_ok(tool_name):
        calls = [tr for tr in pt_calls if tr.get("name") == tool_name]
        if not calls:
            return False, "no calls"
        s = json.dumps(calls[0].get("result", "")) if isinstance(calls[0].get("result"), (dict, list)) else str(calls[0].get("result", ""))
        ok = "error" not in s.lower() and "failed" not in s.lower() and "budget_exhausted" not in s
        return ok, f"{len(calls)} call(s)"

    calc_ok, calc_detail = _pt_tool_ok("calculator")
    check("Platform tool: calculator", calc_ok, calc_detail)

    ct_ok, ct_detail = _pt_tool_ok("current_time")
    check("Platform tool: current_time", ct_ok, ct_detail)

    http_ok, http_detail = _pt_tool_ok("http_request")
    check("Platform tool: http_request", http_ok, http_detail)

    ci_ok, ci_detail = _pt_tool_ok("code_interpreter")
    check("Platform tool: code_interpreter", ci_ok, ci_detail)

    # Render
    print("=" * 78)
    print(" PHASE 5.3 FEATURE COVERAGE")
    print("=" * 78)
    passed = 0
    for name, ok, detail in results:
        sym = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {sym}  {name}" + (f"\n          {detail}" if detail else ""))
        if ok:
            passed += 1
    total = len(results)
    print()
    print("=" * 78)
    print(f" {passed}/{total} features verified")
    print("=" * 78)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
