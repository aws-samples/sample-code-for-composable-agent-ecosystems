"""Offline runner + FakeSpeaker for the Quorum archetype.

    python -m tessera_platform.quorum.run --decision "..." [--context ...] \
        [--speaker fake|bedrock] [--eco ecosystems/x.yaml] [--json]

FakeSpeaker is fully deterministic — it needs no AWS and exercises every path (empanelment
with justified exclusions, a completeness addition, multi-round arguments, a blocker, a
dissent, an escalation, and verdict enforcement). It's what makes the engine testable and
lets you drive an end-to-end run before any deployment. `--speaker bedrock` swaps in the
real model.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

import yaml

from .engine import quorum_policy, run_quorum
from .models import Persona, QuorumPlan, Transcript
from . import personas as persona_provider


class FakeSpeaker:
    """Deterministic, offline Speaker. Same inputs → same run."""

    def __init__(self) -> None:
        self._dissent_emitted = False
        self._summoned = False

    def digest(self, decision: str, context: str) -> str:
        return context or ""

    def empanel(self, decision: str, context: str, pool: list[Persona], policy: dict) -> dict:
        required = list(policy.get("select", {}).get("diversity") or [])
        seated, excluded, covered = [], [], set()
        if required:
            # greedily seat a MINIMAL covering panel — each seat must add a required dimension.
            for p in pool:
                if (set(p.dimensions) & set(required)) - covered:
                    seated.append(p.persona_id)
                    covered |= set(p.dimensions)
        else:
            lo = int(policy.get("select", {}).get("min", 3))
            seated = [p.persona_id for p in pool[:max(3, lo)]]
        seated_set = set(seated)
        for p in pool:
            if p.persona_id not in seated_set:
                excluded.append({"persona_id": p.persona_id,
                                 "reason": "required dimensions already covered by seated members"})
        just = ("Minimal panel covering: " + ", ".join(sorted(required))) if required else "Broad panel."
        return {"seated": seated, "excluded": excluded, "justification": just}

    def completeness(self, decision: str, context: str, seated: list[Persona], pool: list[Persona]) -> dict:
        covered = {d for p in seated for d in p.dimensions}
        seated_ids = {p.persona_id for p in seated}
        for p in pool:
            gap = set(p.dimensions) - covered
            if p.persona_id not in seated_ids and gap:
                return {"missing": sorted(gap)[:1], "add": [p.persona_id],
                        "note": f"panel lacked a {sorted(gap)[0]} perspective"}
        return {}

    def speak(self, persona: Persona, decision: str, context: str, view: list[dict], round: int, on_token=None, **kwargs) -> list[dict]:
        g = persona.invokes[:1]
        if round == 0:
            q = persona.key_questions[0] if persona.key_questions else "assessing the decision"
            entries = [{"type": "claim", "text": f"{persona.name}: {q}",
                        "detail": f"Detail from {persona.name}: {q} Specifics and the required change.",
                        "grounding": g, "severity": "note"}]
            if persona.severity_bias == "harsh":
                sev = "blocker" if persona.persona_id in ("regulators_eye", "security_adversary") else "material"
                entries.append({"type": "objection",
                                "text": f"{persona.name} objects: a control assumed effective would not hold.",
                                "grounding": g, "severity": sev})
            return entries
        if round == 1 and not self._dissent_emitted:
            self._dissent_emitted = True
            return [{"type": "dissent", "text": f"{persona.name} dissents: would not sign off as presented.",
                     "severity": "material"}]
        return []

    def escalate(self, decision: str, view: list[dict], available: list[Persona]) -> Optional[str]:
        if self._summoned or not available:
            return None
        self._summoned = True
        return available[0].persona_id

    def next_speaker(self, decision, context, view, seated, recent, **kwargs):
        # deterministic: prefer a seated voice not heard in the last couple of turns
        rec = set(recent or [])
        for p in seated:
            if p.persona_id not in rec:
                return p.persona_id
        return seated[0].persona_id if seated else None

    def verdict(self, decision: str, context: str, transcript: Transcript, plan: QuorumPlan) -> dict:
        # Optimistic on purpose — the engine enforces the real invariants (open blocker /
        # dissent) over this, which is the point.
        return {"grade": "defensible", "summary": "No blocking issue raised by the panel.",
                "against_whom": [], "indefensible_elements": [], "remediation": []}


def _make_speaker(kind: str):
    if kind == "bedrock":
        from .bedrock_speaker import BedrockSpeaker
        return BedrockSpeaker()
    return FakeSpeaker()


def _print_human(result) -> None:
    p, t, v = result.plan, result.transcript, result.verdict
    print("\n" + "=" * 74)
    print(f" QUORUM RUN — {result.decision_ref}")
    print("=" * 74)
    print(f"\nCONVENED ({len(p.seated)}): {', '.join(p.seated)}")
    if p.summoned:
        print(f"SUMMONED: {', '.join(p.summoned)}")
    print(f"EXCLUDED ({len(p.excluded)}): " + "; ".join(f"{e['persona_id']} ({e['reason']})" for e in p.excluded[:6])
          + (" …" if len(p.excluded) > 6 else ""))
    print(f"COVERAGE: {', '.join(p.covered_dimensions)}"
          + (f"  |  GAPS: {', '.join(p.missing_dimensions)}" if p.missing_dimensions else "  |  no gaps"))
    print(f"WHY THIS PANEL: {p.justification}")
    print(f"\nDELIBERATION — {t.rounds} rounds, {len(t.entries)} entries, "
          f"{len(t.open_issues)} open issue(s), {len(t.dissent)} dissent(s)")
    for e in t.entries:
        tag = {"blocker": "■", "material": "▲", "note": "·"}.get(e.severity, "·")
        print(f"  r{e.round} {tag} [{e.member}] {e.type}: {e.text}")
    print(f"\nVERDICT: {v.grade.upper()}")
    if v.against_whom:
        print(f"  fails against: {', '.join(v.against_whom)}")
    for el in v.indefensible_elements:
        print(f"  indefensible: {el.get('element','')} — by {el.get('by','')}")
    for d in v.dissent_preserved:
        print(f"  dissent kept: {d.get('by','')} — {d.get('position','')}")
    print(f"  composition: {v.composition_summary}")
    print("=" * 74 + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a Quorum deliberation offline or via Bedrock.")
    ap.add_argument("--decision", required=True)
    ap.add_argument("--context", default="")
    ap.add_argument("--speaker", choices=["fake", "bedrock"], default="fake")
    ap.add_argument("--eco", help="ecosystem YAML providing the quorum: policy block")
    ap.add_argument("--personas", help="path to scrutineers.json (default: repo seed)")
    ap.add_argument("--source", choices=["seed", "catalog"], default="seed")
    ap.add_argument("--json", action="store_true", help="emit JSON result instead of the human view")
    ap.add_argument("--events", action="store_true", help="stream events to stderr")
    args = ap.parse_args(argv)

    config = {}
    if args.eco:
        config = yaml.safe_load(open(args.eco)) or {}
    if "quorum" not in config:
        config.setdefault("name", "quorum")
        config["quorum"] = {"empanelment": {"select": {"min": 3, "max": 7,
                            "diversity": ["regulatory", "conduct", "customer", "security", "accountability"]}},
                            "deliberation": {"max_rounds": 3}}

    pool = persona_provider.load_pool(source=args.source, path=args.personas)
    emit = (lambda ev: print(json.dumps(ev), file=sys.stderr)) if args.events else None
    result = run_quorum(config, args.decision, args.context, pool, _make_speaker(args.speaker), emit)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
