"""Builder eval-proposal pass (spec §8, Phase 5).

After an ecosystem CONVERGES in the conversational builder, this pass drafts an
eval suite for SME curation:
  1. metric bindings — which OUTPUT fields matter + the fitting primitive;
  2. candidate golden cases — inferred archetypes (happy path, edge, adversarial,
     insufficient-evidence);
  3. custom judge rubrics for narrative outputs.

The heavy lifting (reading the just-designed agents/contracts and drafting
domain-specific bindings/cases/rubrics) is an LLM call in suggest-ecosystem; this
module provides the GENERIC scaffolding: (a) a deterministic heuristic proposal
from the eco's declared structure (works with no LLM, and seeds the LLM prompt),
and (b) validation/normalisation of an approved proposal into the `evaluation:`
block + a golden-set JSONL. All eco-agnostic — field names come from the eco.
"""

from __future__ import annotations

import json
from typing import Any

from .config import parse_evaluation_block, EvaluationConfigError


# Archetypes we always propose a golden case for (generic labels; the CONTENT is
# author/LLM-supplied). "insufficient-evidence" maps to the abstention metric.
CANDIDATE_ARCHETYPES = [
    {"archetype": "happy_path", "should_abstain": False,
     "hint": "a clear, well-evidenced case with an unambiguous correct outcome"},
    {"archetype": "edge", "should_abstain": False,
     "hint": "a boundary case near a threshold or a rare-but-valid combination"},
    {"archetype": "adversarial", "should_abstain": False,
     "hint": "a case crafted to trip the pipeline (misleading framing, injection, conflict)"},
    {"archetype": "insufficient_evidence", "should_abstain": True,
     "hint": "a case where the eco should ABSTAIN/REFER rather than decide"},
]


def _leaf_output_fields(config: dict) -> list[str]:
    """Best-effort: infer candidate output fields from a `decision:` block if the
    eco declares one (spec-decision-engine), else return []. Domain-blind: we read
    the author's declared dimension names, we do not invent any."""
    dec = config.get("decision") or {}
    dims = dec.get("dimensions") or {}
    fields = list(dims.keys())
    # a decision block also produces a label + outcome — expose those as fields
    if dec.get("labels"):
        fields.append("label")
    if (dec.get("decision") or {}).get("outcomes"):
        fields.append("outcome")
    return fields


# Field-name hints (domain-BLIND — we match the SHAPE of an author's declared
# field name, never inject a specific domain term). Used only to route a
# best-guess primitive in the heuristic seed; the SME/LLM confirms/rewrites.
_DECISION_FIELD_HINTS = ("decision", "verdict", "outcome", "recommendation",
                         "disposition", "determination", "rating", "label",
                         "classification", "action")
_REGIME_FIELD_HINTS = ("regime", "applicable", "as_of", "as-of", "effective",
                       "temporal", "in_force", "version")


def _declared_output_fields(config: dict) -> list[str]:
    """Best-effort, DOMAIN-BLIND inference of candidate output field names from an
    eco with NO `decision:` block. Scans agent prompts for the author's OWN
    'output: field, field, ...' / 'Output: {a, b}' phrasing and snake_case tokens
    that look like structured-finding fields. Returns a de-duped shortlist; empty
    is fine (the seed still proposes the reference-free metrics). Never invents a
    domain term — every candidate is a token the author wrote."""
    import re
    fields: list[str] = []
    seen: set[str] = set()
    for a in (config.get("agents") or []):
        prompt = str((a or {}).get("prompt") or "")
        # snake_case identifiers the author used as output field names
        for tok in re.findall(r"\b([a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b", prompt):
            if tok in seen:
                continue
            low = tok.lower()
            if any(h in low for h in _DECISION_FIELD_HINTS) or any(h in low for h in _REGIME_FIELD_HINTS):
                seen.add(tok)
                fields.append(tok)
    return fields[:8]


def propose_metric_bindings(config: dict) -> list[dict]:
    """Heuristic metric bindings from the eco's declared structure. This is a
    STARTING POINT for SME curation / the LLM proposal — never the final word.

    Rich by construction: even with no `decision:` block we propose a real suite
    (categorical on any decision/verdict-shaped field, temporal-regime
    correctness, citation faithfulness, abstention, and a narrative judge), so the
    fallback is never a single thin metric."""
    bindings: list[dict] = []
    seen_fields: set[str] = set()

    def _add_categorical(field: str, mid: str | None = None):
        if not field or field in seen_fields:
            return
        seen_fields.add(field)
        bindings.append({"id": mid or f"{field}_acc", "field": field, "scorer": "categorical"})

    dec = config.get("decision") or {}
    dims = dec.get("dimensions") or {}
    for name, spec in dims.items():
        t = (spec or {}).get("type")
        if t in ("ordinal", "category", "boolean"):
            _add_categorical(name)
        elif t in ("number", "numeric"):
            if name not in seen_fields:
                seen_fields.add(name)
                bindings.append({"id": f"{name}_val", "field": name,
                                 "scorer": "numeric_tolerance", "tol": 0})
    if dec.get("labels"):
        _add_categorical("label", "label_acc")
    outcomes = [str(o).upper() for o in ((dec.get("decision") or {}).get("outcomes") or [])]

    # No decision block → infer decision/verdict/regime-shaped fields from the
    # agents' own declared vocabulary (domain-blind) so the seed is still rich.
    if not dims:
        inferred = _declared_output_fields(config)
        for f in inferred:
            low = f.lower()
            # a temporal/regime field is still a categorical-correctness check
            _add_categorical(f, f"{f}_correct" if any(h in low for h in _REGIME_FIELD_HINTS) else f"{f}_acc")

    # Citation faithfulness — always relevant (reference-free, grounding-blind).
    bindings.append({"id": "evidence_faithful", "field": "*", "scorer": "citation_resolves"})

    # Abstention — the safety-critical "did it correctly REFER/HOLD instead of
    # deciding" check. MUST carry expected_value (spec §4 / config validation).
    abstain_like = next((o for o in outcomes if o in
                         ("REFER", "REFUSE", "ABSTAIN", "NO_GO", "ESCALATE", "HOLD",
                          "HOLD-FOR-INFO", "HOLD_FOR_INFO")), None)
    if abstain_like:
        field = "outcome" if dec.get("labels") else (bindings[0]["field"] if bindings and bindings[0].get("field") not in (None, "*") else "outcome")
        bindings.append({"id": "abstains_when_thin", "field": field,
                         "scorer": "abstention", "expected_value": abstain_like})
    else:
        # generic abstention on the first decision-shaped field, if any
        dfield = next((b["field"] for b in bindings
                       if b["scorer"] == "categorical" and b.get("field")), None)
        if dfield:
            bindings.append({"id": "abstains_when_thin", "field": dfield,
                             "scorer": "abstention", "expected_value": "REFER"})

    # Narrative judge — a reference-free faithfulness/completeness check for the
    # eco's narrative/deliverable output (independent Bedrock judge, spec §7.2).
    bindings.append({"id": "narrative_quality", "scorer": "bedrock_judge",
                     "builtin": ["faithfulness", "completeness"]})
    # A regulator/reviewer-readiness custom rubric for narrative outputs — a
    # generic pipe the SME rewrites for their audience (spec §4/§7.2).
    bindings.append({
        "id": "reviewer_ready", "scorer": "bedrock_judge",
        "custom_rubric": ("Rate whether the deliverable is ready for its intended "
                          "reviewer/audience: are all conclusions supported by cited "
                          "evidence, is the reasoning traceable, and would a diligent "
                          "reviewer be able to sign it off without rework? Penalise "
                          "uncited claims, internal contradictions, and unverifiable "
                          "assertions."),
    })
    return bindings


def propose_golden_cases(config: dict) -> list[dict]:
    """Candidate (unlabelled) golden cases, one per archetype. The SME/LLM fills
    `inputs` (to match the eco's declared `inputs` schema) and `expected`."""
    input_ids = [i.get("id") for i in (config.get("ecosystem", {}).get("inputs") or []) if i.get("id")]
    cases = []
    for arch in CANDIDATE_ARCHETYPES:
        cases.append({
            "name": f"{arch['archetype']}-candidate",
            "inputs": {iid: "" for iid in input_ids},   # SME fills
            "expected": {"should_abstain": arch["should_abstain"]},
            "labels": {"archetype": arch["archetype"], "reviewed": False,
                       "hint": arch["hint"]},
        })
    return cases


def propose_evaluation(config: dict) -> dict:
    """Assemble a full DRAFT `evaluation:` proposal from the eco structure. The
    builder streams this to the SME (CONVERSE-style); nothing is applied until
    the SME approves (§8)."""
    bindings = propose_metric_bindings(config)
    thresholds = {}
    for b in bindings:
        if b["scorer"] == "citation_resolves":
            thresholds[b["id"]] = 1.0
        elif b["scorer"] == "categorical":
            thresholds[b["id"]] = 0.9
        elif b["scorer"] == "abstention":
            thresholds[b["id"]] = 1.0
        elif b["scorer"] == "bedrock_judge":
            thresholds[b["id"]] = 0.8
        elif b["scorer"] == "numeric_tolerance":
            thresholds[b["id"]] = 0.9
    proposal = {
        "evaluation": {
            "replicates": 5,
            "run_mode": "headless",
            "trajectory": True,
            "metrics": bindings,
            "thresholds": thresholds,
            "gate": "warn",         # SME upgrades to block after curating cases
            "online": {"enabled": False, "baseline_sample_rate": 0.1,
                       "always_eval_when": ["gate_fired", "human_override"]},
        },
        "golden_cases": propose_golden_cases(config),
    }
    return proposal


def compile_approved_proposal(config: dict, approved: dict) -> tuple[dict, str]:
    """Given an SME-approved proposal, return (evaluation_block, golden_jsonl).

    Validates the evaluation block via the platform parser (fail loudly on a
    malformed approval). `golden_jsonl` is the cases.jsonl body to write to S3
    (§5.3). Eco-agnostic — values are author/SME-supplied."""
    eval_block = approved.get("evaluation")
    if not isinstance(eval_block, dict):
        raise EvaluationConfigError("approved proposal missing `evaluation` block")
    # validate by parsing a synthetic config carrying just the block + agents
    parse_evaluation_block({"evaluation": eval_block, "agents": config.get("agents", [])})
    cases = approved.get("golden_cases") or []
    if not isinstance(cases, list):
        raise EvaluationConfigError("approved proposal `golden_cases` must be a list")
    lines = [json.dumps(c, default=str) for c in cases]
    return eval_block, "\n".join(lines) + ("\n" if lines else "")
