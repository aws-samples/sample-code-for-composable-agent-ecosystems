# Phase 4.5 Regression Tests

## Introduction

## Prerequisites

- Deployed Tessera infrastructure (see main README deployment steps)
- AWS CLI configured with `example-profile` profile
- Python 3.12+ with project virtualenv activated
- Bash shell

This suite validates that all Tessera platform features from Phases 1 through 4.5 remain functional after changes.

Live-platform end-to-end smoke test that exercises every Phase 1-4.5 feature
in ~2 minutes with a single web search provider call.

## What it tests

| Layer | Feature |
|---|---|
| **Compiler** | YAML validation (0 warnings on sharp YAML); role_contract injection per agent; `read: "all"` scalar acceptance |
| **ASL** | Parallel layers; ResultSelector; JsonMerge; per-agent build_task; `TimeoutSeconds: 300` |
| **Runtime** | Model tiering (Opus synthesizers, Sonnet leaves); role_contract → prompt section rendering; explicit `max_tokens` |
| **agent-invoker** | S3 staging of output + tool_results; citation validator (raises on missing `[per <id>:`) |
| **grounding-check** | Bedrock Guardrail contextual grounding; chunking ≤4500c; S3-fetch helpers |
| **memory** | `memory_write` (single-segment + cross-eco paths); `memory_read` prefix-mode walking; ACL positive cases |
| **identity** | `@requires_api_key` decorator → AgentCore Identity → web search provider Bearer auth |
| **gateway** | (implicit — runtime discovers gateway tools at startup) |
| **frontend** | (out-of-band — verify in UI by viewing the run live) |

## Files

- `phase-test.yaml` — the test ecosystem definition (4 agents: writer, cross_writer, reader, composer)
- `run-phase-test.sh` — deploy + run + verify wrapper
- `check-features.py` — feature coverage checker (parses SFN history + S3 outputs)

## Cost & time

- ~1 web search provider search (~$0.001 paid plan, free on free tier)
- ~$0.03 in Bedrock token spend (Opus on 2 synthesizers, Sonnet on 2 leaves)
- ~2 minutes wall time

## Run it

```bash
bash tests/regression/run-phase-test.sh

The script deploys infrastructure, runs all ecosystem tests, and verifies outputs against expected patterns. On success it exits with code 0 and prints a summary. On failure, it exits non-zero and lists which features failed with details.
```

Exits 0 if every feature passes. Run after any platform change to catch
regressions across the whole Phase 4.5 stack in one shot.

## Why a dedicated test ecosystem

Real ecosystems (hedge-fund-trading-desk etc.) take 10+ minutes per run and
burn ~30 web search provider searches. They're great for production demos but terrible
feedback loops during platform development. This ecosystem is designed for
the opposite — **maximum feature coverage per minute**.

If you discover a new platform bug, add a corresponding agent or claim to
`phase-test.yaml` so the next regression run catches it.

## Conclusion

These tests provide confidence that platform features remain stable across deploys. Run them before any release to catch regressions early.
