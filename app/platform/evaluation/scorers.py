"""Generic, domain-blind scorer primitives (spec §4).

Each primitive scores a DECLARED field/dimension of an ecosystem's output
against a golden `expected`. None contains domain knowledge — the field names
and expected values are author-supplied. Deterministic primitives NEVER delegate
to a judge (spec §4 rule): objective checks stay auditable in-platform.

A scorer returns a `MetricScore` carrying a per-case binary `hit` (used for the
per-issue miss-rate, spec §6.2), a continuous `value` (accuracy/recall/…), and
enough detail for the scorecard drill-down (spec §9.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .config import MetricBinding


@dataclass
class MetricScore:
    metric_id: str
    scorer: str
    value: float                 # continuous score in [0, 1]
    hit: bool                    # binary per-case pass (feeds miss-rate)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "metric_id": self.metric_id,
            "scorer": self.scorer,
            "value": round(self.value, 6),
            "hit": self.hit,
            "detail": self.detail,
        }


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_field(output: dict, path: str) -> Any:
    """Resolve a possibly-dotted field path from a case's structured output.

    `output` is the ecosystem's own STRUCTURED-FINDINGS map (§5.3). The path is
    an author-declared key; we do NOT interpret its meaning."""
    if path in (None, "", "*"):
        return output
    cur: Any = output
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip().lower()
    return v


def _as_set(v: Any) -> set:
    if v is None:
        return set()
    if isinstance(v, (list, tuple, set)):
        return {_norm(x) for x in v}
    return {_norm(v)}


# ── deterministic primitives ──────────────────────────────────────────────────
# Signature: (binding, output, expected) -> MetricScore

def score_categorical(mb: MetricBinding, output: dict, expected: dict) -> MetricScore:
    """Labelled field vs expected. Binary hit = exact (normalised) match.
    `cost_weights` (asymmetric error costs) are recorded for the aggregator to
    apply a cost-weighted view without baking any domain policy in here."""
    got = _norm(_get_field(output, mb.field))
    want = _norm(expected.get(mb.field))
    hit = got == want
    detail = {"field": mb.field, "expected": expected.get(mb.field),
              "got": _get_field(output, mb.field)}
    if mb.cost_weights and not hit:
        # classify error direction only when both are booleans/binary-ish; else
        # leave generic. Fully domain-blind: we don't know what "positive" means,
        # so we just record which weight applies by presence.
        detail["cost_weights"] = mb.cost_weights
    return MetricScore(mb.id, mb.scorer, 1.0 if hit else 0.0, hit, detail)


def score_numeric_tolerance(mb: MetricBinding, output: dict, expected: dict) -> MetricScore:
    got = _get_field(output, mb.field)
    want = expected.get(mb.field)
    ok = False
    try:
        ok = abs(float(got) - float(want)) <= float(mb.tol)
    except (TypeError, ValueError):
        ok = False
    return MetricScore(mb.id, mb.scorer, 1.0 if ok else 0.0, ok,
                       {"field": mb.field, "expected": want, "got": got, "tol": mb.tol})


def score_set_recall(mb: MetricBinding, output: dict, expected: dict) -> MetricScore:
    """Required-points coverage. Reuses the `any_of` shape from
    scripts/quorum-rubric-atlas.json: each required item may be a plain value OR
    a `{any_of: [...]}` (satisfied if any synonym is present). Value = recall;
    hit = full recall (all required points present)."""
    required = expected.get(mb.field)
    if required is None:
        # nothing required → vacuously satisfied
        return MetricScore(mb.id, mb.scorer, 1.0, True,
                           {"field": mb.field, "required": [], "recall": 1.0})
    got = _get_field(output, mb.field)
    got_set = _as_set(got)
    got_blob = " ".join(str(x) for x in (got if isinstance(got, (list, tuple, set)) else [got])).lower()

    req_items = required if isinstance(required, (list, tuple)) else [required]
    matched, missed = [], []
    for item in req_items:
        if isinstance(item, dict) and "any_of" in item:
            syns = [str(s).lower() for s in item["any_of"]]
            present = any(_norm(s) in got_set or s in got_blob for s in syns)
            label = item.get("issue") or item["any_of"][0]
        else:
            present = _norm(item) in got_set or str(item).lower() in got_blob
            label = item
        (matched if present else missed).append(label)

    recall = len(matched) / len(req_items) if req_items else 1.0
    hit = not missed
    return MetricScore(mb.id, mb.scorer, recall, hit,
                       {"field": mb.field, "matched": matched, "missed": missed,
                        "recall": round(recall, 4)})


def score_citation_resolves(mb: MetricBinding, output: dict, expected: dict) -> MetricScore:
    """Every cited claim must resolve to provided evidence — no fabrication
    (spec §4). Reference-free: needs no golden `expected`. Grounding is already
    domain-blind. The case's output is expected to carry two generic sidecars
    the platform emits: `citations` (list of claim->source refs) and `evidence`
    (the set of source ids/uris actually provided to the run). A citation
    resolves iff its source id is in `evidence`."""
    citations = output.get("citations") or output.get("_citations") or []
    evidence = _as_set(output.get("evidence") or output.get("_evidence") or [])
    if not isinstance(citations, list):
        citations = [citations]
    total = 0
    resolved = 0
    unresolved: list[Any] = []
    for c in citations:
        src = c.get("source") if isinstance(c, dict) else c
        if src is None:
            continue
        total += 1
        # a citation resolves if ANY of its source tokens is in provided evidence
        srcs = _as_set(src)
        if evidence and (srcs & evidence):
            resolved += 1
        elif not evidence:
            # no evidence set provided → cannot verify; treat as unresolved (loud)
            unresolved.append(src)
        else:
            unresolved.append(src)
    value = 1.0 if total == 0 else resolved / total
    hit = not unresolved
    return MetricScore(mb.id, mb.scorer, value, hit,
                       {"cited": total, "resolved": resolved, "unresolved": unresolved})


def score_abstention(mb: MetricBinding, output: dict, expected: dict) -> MetricScore:
    """Safety-critical: did the eco correctly ABSTAIN (e.g. REFER/refuse) when
    the golden case marks `should_abstain`? `expected_value` is the author's
    abstain label (e.g. "REFER"). Domain-blind: we compare the declared field
    to the declared abstain value only when `should_abstain` is true."""
    should_abstain = bool(expected.get("should_abstain"))
    got = _norm(_get_field(output, mb.field))
    abstain_value = _norm(mb.expected_value)
    if should_abstain:
        hit = got == abstain_value
    else:
        # should NOT abstain → correct iff it did not emit the abstain value
        hit = got != abstain_value
    return MetricScore(mb.id, mb.scorer, 1.0 if hit else 0.0, hit,
                       {"field": mb.field, "should_abstain": should_abstain,
                        "abstain_value": mb.expected_value,
                        "got": _get_field(output, mb.field)})


DETERMINISTIC_SCORERS: dict[str, Callable[[MetricBinding, dict, dict], MetricScore]] = {
    "categorical": score_categorical,
    "exact_match": score_categorical,
    "numeric_tolerance": score_numeric_tolerance,
    "set_recall": score_set_recall,
    "coverage": score_set_recall,
    "citation_resolves": score_citation_resolves,
    "abstention": score_abstention,
}

# Judge scorers are NOT run here — they are delegated to Bedrock (judge.py).
# Listed so callers can branch, and so the scorer catalog is complete/introspectable.
JUDGE_SCORERS = {"bedrock_judge"}

SCORER_PRIMITIVES = set(DETERMINISTIC_SCORERS) | JUDGE_SCORERS


def score_metric(mb: MetricBinding, output: dict, expected: dict) -> MetricScore:
    """Score one deterministic metric for one case. Raises on a judge metric
    (those are delegated) or an unknown scorer — fail loudly (no silent skip)."""
    fn = DETERMINISTIC_SCORERS.get(mb.scorer)
    if fn is None:
        if mb.scorer in JUDGE_SCORERS:
            raise ValueError(
                f"metric {mb.id!r}: {mb.scorer} is judge-delegated; call the judge "
                "adapter, not score_metric()")
        raise ValueError(f"metric {mb.id!r}: unknown scorer {mb.scorer!r}")
    return fn(mb, output or {}, expected or {})
