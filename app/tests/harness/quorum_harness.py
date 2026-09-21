"""Regression harness for the Quorum deliberation room.

WHY THIS EXISTS
Every prompt-level fix to the room so far broke something else, because the room's
behaviour was never measured. Instruction-following degrades non-linearly as rules are
added (arXiv 2607.19257, 2608.02639), so "add a rule to fix X" silently costs Y. This
harness makes the room's behaviour and its prompt budget MEASURABLE and deterministic, so
a change can be proven not to regress before it is deployed.

It provides:
  * ScriptedSpeaker — a deterministic Speaker that REPRODUCES the pathologies observed in
    real sessions (human addressed via objections rather than questions; the same demand
    re-raised in new wording; a member promising a sketch without emitting one; one member
    dominating). No AWS, same result every run.
  * run_room()     — drives convene -> accept -> step... capturing emitted events and the
    pause reasons the server returned.
  * metrics()      — the behavioural numbers we gate on.
  * prompt_metrics() — the PROMPT BUDGET (chars, imperative-rule count, max_tokens) of a
    real member turn, captured without calling Bedrock.

Metric definitions are in metrics(); the thresholds we hold ourselves to live in
tests/unit/test_quorum_regression.py.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from tessera_platform.quorum import session as S
from tessera_platform.quorum.models import Persona, QuorumPlan, Transcript
from tessera_platform.quorum.run import FakeSpeaker

# ---- the room under test --------------------------------------------------------------
CFG = {
    "name": "architecture-review-board",
    "quorum": {
        "empanelment": {"select": {"min": 4, "max": 6}},
        "deliberation": {"max_rounds": 2, "escalation": False, "preserve_dissent": True},
        "whiteboard": {"enabled": True, "infer_affinity": True},
    },
}
DECISION = "Adopt a single shared Postgres database across all 12 microservices to hit a deadline."
CONTEXT = "Fintech ops platform, no DBA team, autonomous refunds up to $1000."

# The scripted panel: a facilitator/architect who would draw, and challengers who won't.
def pool() -> list[Persona]:
    return [
        Persona.from_record({"persona_id": "chair", "name": "Sarah Chen — ARB Chair",
                             "stance": "runs the review, synthesises", "dimensions": ["governance"],
                             "role": "facilitator", "whiteboard_affinity": "high"}),
        Persona.from_record({"persona_id": "architect", "name": "Elena Vasquez — Principal Architect",
                             "stance": "systems design authority", "dimensions": ["architecture"],
                             "role": "synthesiser", "whiteboard_affinity": "high"}),
        Persona.from_record({"persona_id": "sre", "name": "Tom Nguyen — Principal SRE",
                             "stance": "availability and failure modes", "dimensions": ["operational-resilience"],
                             "role": "challenger", "whiteboard_affinity": "medium"}),
        Persona.from_record({"persona_id": "risk", "name": "Elena Kowalski — Financial Risk",
                             "stance": "money-moving controls", "dimensions": ["financial-risk"],
                             "role": "challenger", "whiteboard_affinity": "none"}),
        Persona.from_record({"persona_id": "privacy", "name": "Fiona Grant — Privacy Counsel",
                             "stance": "APPs and PII flows", "dimensions": ["privacy"],
                             "role": "challenger", "whiteboard_affinity": "none"}),
    ]


class ScriptedSpeaker(FakeSpeaker):
    """Deterministic Speaker that reproduces the observed real-session pathologies.

    Knobs (all default to the OBSERVED-BAD behaviour so the harness measures the real bug):
      address_human_as        — entry type used when a member speaks TO the submitter.
                                Real sessions used 'objection' 13x / 'rebuttal' 8x and
                                'question' only once, which is why the room never paused.
      repeat_same_demand      — re-raise ONE demand in fresh wording every turn (the
                                'lack of progress' failure: 35% of drift cases, Becker et al.).
      promise_sketch_no_draw  — say "let me sketch it" and emit no draw op (observed).
      dominator               — persona_id that speaks whenever it can (observed 31% share).
    """

    # ONE demand, reworded with DIFFERENT vocabulary each time — lexically distinct enough to
    # slip past a token-overlap novelty gate, semantically identical. This is what real
    # sessions did ("saga" 16x, "circuit" 11x across 26 turns) and why lexical dedupe fails.
    # genuinely DIFFERENT points (used when repeat_same_demand=False) — each survives the novelty
    # gate, so speaker-selection fairness can be measured without the anti-repetition path firing
    _DISTINCT = [
        "the single-region posture leaves no failover path for the ledger",
        "per-tenant encryption keys are not rotated on offboarding",
        "the refund cap is enforced client-side, not in the state machine",
        "there is no reconciliation between approvals and the payment ledger",
        "prompt injection through order notes reaches the tool arguments",
        "the 99.9% availability claim has no measured error budget behind it",
        "tenant isolation relies on a partition key with no policy test",
        "the human gate has no service-level target for pickup time",
    ]
    # distinct agenda labels pairing with _DISTINCT (semantic topic matching collapses "topic-1"
    # and "topic-2" into ONE item, which would trip saturation and mask speaker fairness)
    _TOPICS = ["regional failover", "key rotation on offboarding", "refund cap enforcement",
               "ledger reconciliation", "prompt injection surface", "availability error budget",
               "tenant isolation policy", "human gate pickup SLO"]
    _REWORDINGS = [
        "I need circuit breakers around the Bedrock reasoning loop",
        "what stops a slow model from cascading into a pile-up",
        "there is no ceiling on how long an invocation may run",
        "tail latency on the inference path has no guardrail",
        "if the model hangs, nothing cuts the call off",
    ]

    def __init__(self, address_human_as: str = "objection", repeat_same_demand: bool = True,
                 promise_sketch_no_draw: bool = True, dominator: Optional[str] = "sre",
                 draw: bool = False, address_human: bool = True,
                 address_in_prose: bool = False, never_sketch: bool = False,
                 stuck_member: Optional[str] = None) -> None:
        super().__init__()
        self.address_human_as = address_human_as
        self.repeat_same_demand = repeat_same_demand
        self.promise_sketch_no_draw = promise_sketch_no_draw
        self.dominator = dominator
        self.draw = draw
        self.address_human = address_human      # False = members talk only among themselves
        # True = reproduce the OBSERVED failure: the member speaks TO the submitter in words but
        # tags `target` at a colleague's entry (real run: "submitter" in prose 13x, target=human 0),
        # so any server rule keyed on `target` never fires.
        self.address_in_prose = address_in_prose
        # Real members almost never set the optional sketch field: a measured live session had ZERO
        # member sketches across 17 turns. This reproduces that.
        self.never_sketch = never_sketch
        # This member only ever has ONE point, so once that topic is settled every turn it takes is
        # refused and it yields — the condition that deadlocked a real room.
        self.stuck_member = stuck_member
        self._n = 0

    # seat the scripted panel as-is
    def empanel(self, decision, context, p, policy) -> dict:
        return {"seated": [x.persona_id for x in p], "excluded": [],
                "justification": "scripted panel", "meeting_frame": "Architecture Review Board"}

    def completeness(self, decision, context, seated, p) -> dict:
        return {}

    def next_speaker(self, decision, context, view, seated, recent, **kw) -> Optional[str]:
        if self.dominator and (not recent or recent[-1] != self.dominator):
            return self.dominator                     # dominates whenever allowed
        for x in seated:
            if not recent or x.persona_id != recent[-1]:
                return x.persona_id
        return seated[0].persona_id if seated else None

    def speak(self, persona, decision, context, view, round, on_token=None, **kw) -> list[dict]:
        self._n += 1
        text = (self._REWORDINGS[self._n % len(self._REWORDINGS)] if self.repeat_same_demand
                else self._DISTINCT[self._n % len(self._DISTINCT)])
        raw = {
            "type": self.address_human_as,            # aimed AT the submitter, but not a question
            "text": f"Marc, {text}.",                 # note: addresses an invented name
            # no shared boilerplate: a common suffix alone can trip the novelty gate
            "detail": (f"{text}; I need to see it before I can own this in production."
                       if self.repeat_same_demand else f"Specifically: {text}."),
            "target": "human" if self.address_human else None,
            "severity": "material",
            "topic": ("anomaly breaker timing" if persona.persona_id == self.stuck_member
                      else "Bedrock timeout & circuit breakers" if self.repeat_same_demand
                      else self._TOPICS[self._n % len(self._TOPICS)]),
        }
        if self.address_in_prose:
            # the real failure: addressed in WORDS, tagged at a colleague
            raw["text"] = f"I need the submitter to confirm {text}."
            raw["target"] = "e0"
        if self.never_sketch:
            raw.pop("whiteboard", None)
            raw.pop("sketch", None)
        elif self.promise_sketch_no_draw and self._n == 3:
            raw["text"] = "Sure — let me sketch the data flow so we can all see the boundary."
        if self.draw and persona.whiteboard_affinity in ("high", "medium") and self._n == 3:
            raw["whiteboard"] = {"why": "the flow is clearer drawn",
                                 "draw": [{"kind": "box", "x": 100, "y": 100, "w": 180, "h": 60,
                                           "label": "API"}]}
        return [raw]

    def sketch(self, persona, purpose, decision, board_digest="", agenda=None) -> dict:
        """A typed SPEC (the server owns geometry). Mirrors the real contract, including the model's
        habit of repeating a label — which the renderer must de-duplicate."""
        return {"point": f"where {purpose[:40]} actually breaks", "type": "flow",
                "nodes": [{"label": "Order note"}, {"label": "Retriever"}, {"label": "Retriever"},
                          {"label": "Prompt"}, {"label": "Bedrock log"}],
                "edges": [{"from": 0, "to": 1}, {"from": 1, "to": 2}, {"from": 2, "to": 3}]}

    def verdict(self, decision, context, transcript, plan, agenda=None) -> dict:
        return {"grade": "defensible_with_conditions", "summary": "scripted",
                "against_whom": [], "indefensible_elements": [], "remediation": []}


def run_room(speaker: Optional[ScriptedSpeaker] = None, steps: int = 24,
             human_says: Optional[str] = None, cfg: Optional[dict] = None):
    """Drive a full room deterministically. Returns (state, events, pause_reasons)."""
    sp = speaker or ScriptedSpeaker()
    events: list[dict] = []
    emit = events.append
    st = S.convene("harness", cfg or CFG, DECISION, CONTEXT, pool(), sp, emit)
    S.apply_empanel(st, {"accept": True}, sp, emit)
    pauses: list[str] = []
    for _ in range(steps):
        if st.phase in (S.VERDICT, S.DONE):
            break
        reason = S.step(st, sp, emit)
        if reason:
            pauses.append(reason)
            if reason == S.AWAIT_HUMAN:
                # the human answers so the room can continue (mirrors real use)
                S.inject_human(st, human_says or "yes, accepted", None, "claim", emit)
            elif reason == S.CONVERGENCE_OFFER:
                break
    return st, events, pauses


# ---- metrics --------------------------------------------------------------------------
_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")
_STOP = {"the", "and", "for", "with", "that", "this", "you", "your", "not", "but", "are", "was",
         "have", "has", "need", "see", "can", "our", "any", "all", "from", "into", "before",
         "marc", "production", "own"}
_SUBMITTER_PROSE = re.compile(r"\b(the submitter|submitter'?s)\b", re.I)
_DRAW_WORDS = re.compile(r"\b(sketch|draw|drawing|diagram|whiteboard|on the board|map it)\b", re.I)


def _tokens(s: str) -> set:
    return {w for w in _WORD.findall((s or "").lower()) if w not in _STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def metrics(st, events: list[dict], pauses: list[str]) -> dict:
    """The numbers we gate on. Each maps to a diagnosed defect."""
    es = [e for e in st.transcript.entries if e.member != S.HUMAN]
    human_targeted = [e for e in es if e.target == S.HUMAN]
    human_questions = [e for e in human_targeted if e.type == "question"]
    await_pauses = [p for p in pauses if p == S.AWAIT_HUMAN]

    # repetition: how many entry PAIRS are near-duplicates in substance (whole session,
    # not a sliding window — same-model agents reword the same point, arXiv 2605.01704)
    subs = [_tokens((e.detail or "") + " " + (e.text or "")) for e in es]
    redundant_pairs = sum(1 for i in range(len(subs)) for j in range(i + 1, len(subs))
                          if _jaccard(subs[i], subs[j]) >= 0.5)

    # dominance: largest share of member turns held by one member
    counts: dict = {}
    for e in es:
        counts[e.member] = counts.get(e.member, 0) + 1
    dominance = (max(counts.values()) / len(es)) if es else 0.0

    ops = (st.whiteboard.ops if getattr(st, "whiteboard", None) else [])
    drawers = {o.author for o in ops}
    promises = [e for e in es if _DRAW_WORDS.search(e.text or "")]
    unfulfilled = [e for e in promises if e.member not in drawers]

    # Wording-INDEPENDENT repetition: how many remarks the room piled onto its single most
    # revisited agenda topic. This is the server's own bookkeeping, so it catches "same point,
    # new words" that a token-overlap gate cannot — and it is exactly what Fix 3 will enforce.
    agenda = st.agenda or []
    max_topic_remarks = max([int(a.get("remarks", 1)) for a in agenda], default=0)

    # Turns that speak TO the submitter in words (regardless of how `target` was tagged). The
    # model tags this field unreliably, so this is the honest denominator for "did they reach me".
    prose_directed = [e for e in es if _SUBMITTER_PROSE.search((e.text or "") + " " + (e.detail or ""))]

    # --- board QUALITY (the failures a screenshot would show) ---
    live = [o for o in ops if o.op_id not in (erased_ids := set(st.whiteboard.erased))]
    # a boundary container encloses its nodes by design — not an overlap defect
    boxes = [o for o in live if o.kind == "box" and not (o.data or {}).get("container")]
    rects = []
    for o in boxes:
        d = o.data or {}
        try:
            rects.append((float(d.get("x", 0)), float(d.get("y", 0)),
                          float(d.get("w", 0)), float(d.get("h", 0)), o))
        except (TypeError, ValueError):
            continue
    overlaps = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            x1, y1, w1, h1, _ = rects[i]
            x2, y2, w2, h2, _ = rects[j]
            if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                overlaps += 1

    def _lands_on_box(pt) -> bool:
        try:
            px, py = float(pt[0]), float(pt[1])
        except (TypeError, ValueError, IndexError):
            return False
        return any(x - 30 <= px <= x + w + 30 and y - 30 <= py <= y + h + 30
                   for x, y, w, h, _ in rects)

    arrows = [o for o in live if o.kind == "arrow"]

    def _ends(a):
        d = a.data or {}
        pts = d.get("points")
        if isinstance(pts, list) and len(pts) >= 2:
            return pts[0], pts[-1]          # routed polyline (orthogonal layout)
        return d.get("from"), d.get("to")

    dangling = 0
    for a in arrows:
        f, t = _ends(a)
        if not (_lands_on_box(f) and _lands_on_box(t)):
            dangling += 1
    # Duplicates are counted WITHIN a sketch, not across the board: two different diagrams may each
    # legitimately name the same entity ("Shared Postgres" in both a flow and a failure sketch). The
    # defect being guarded is one sketch repeating a label, which is what a real board showed
    # ('Review Scope & Process' three times, twice inside one map).
    groups, cur, last_n = [], [], None
    for o in boxes:
        try:
            n = int(str(o.op_id).split("-")[-1])
        except (ValueError, IndexError):
            n = None
        if cur and (last_n is None or n is None or n != last_n + 1 or o.author != cur[-1].author):
            groups.append(cur)
            cur = []
        cur.append(o)
        last_n = n
    if cur:
        groups.append(cur)
    dup_labels = 0
    for g in groups:
        ls = [str((o.data or {}).get("label") or "").strip().lower() for o in g]
        dup_labels += sum(c - 1 for c in Counter(l for l in ls if l).values() if c > 1)

    # D1 relabelling: distinct SUBJECTS vs recorded topics (6 topics for 2 subjects in a real run)
    from tessera_platform.quorum.session import _subject_key
    subjects = set()
    for a in (st.agenda or []):
        k = _subject_key(a.get("topic", ""))
        if k and not any(k & prev for prev in subjects):
            subjects.add(k)
    # D4 broken text: a label whose visible text was cut inside a word
    def _midword(o) -> bool:
        d = o.data or {}
        lbl = str(d.get("label") or "")
        if not lbl or lbl.endswith("\u2026"):
            return False                      # an ellipsis means words were dropped deliberately
        try:
            cap = max(12, int(float(d.get("w", 190)) / 9.5))
        except (TypeError, ValueError):
            cap = 20
        # a genuine mid-word cut leaves the FINAL line at capacity, ending in a word character
        last = lbl.split("\n")[-1]
        return len(last) > cap and last[-1].isalnum()
    midword = sum(1 for o in boxes if _midword(o))
    # D5 the same diagram VISIBLE twice. Counted over LIVE ops only: a superseded map is erased
    # before the new one is drawn, and redrawing after the decision actually changed is correct.
    points = [str((o.data or {}).get("text") or "") for o in live if o.kind == "text"]
    repeated_points = sum(c - 1 for c in Counter(p for p in points if p).values() if c > 1)
    # D6 vocabulary + who owns the board
    kinds = Counter(o.kind for o in live)
    authors = Counter(o.author for o in live)

    return {
        "agenda_subjects": len(subjects),
        "midword_labels": midword,
        "repeated_sketch_points": repeated_points,
        "op_kinds": len(kinds),
        "emphasis_ops": kinds.get("stroke", 0),
        "coloured_ops": sum(1 for o in live if (o.data or {}).get("color")),
        "board_author_share": round(max(authors.values()) / sum(authors.values()), 2) if authors else 0.0,
        "overlapping_box_pairs": overlaps,
        "dangling_arrows": dangling,
        "duplicate_box_labels": dup_labels,
        "board_titles": sum(1 for o in live if o.kind == "text"),
        "member_turns": len(es),
        "human_targeted": len(human_targeted),
        "human_questions": len(human_questions),
        "prose_directed": len(prose_directed),
        # of the turns that spoke TO the submitter in words, how many actually reached them
        "prose_reachability": (len(await_pauses) / len(prose_directed)) if prose_directed else 1.0,
        "pauses_for_human": len(await_pauses),
        # Fix 1 target: a turn aimed at the submitter should reach them.
        "floor_reachability": (len(await_pauses) / len(human_targeted)) if human_targeted else 1.0,
        "redundant_pairs": redundant_pairs,
        "max_topic_remarks": max_topic_remarks,
        "agenda_topics": len(agenda),
        "dominance": round(dominance, 3),
        "board_ops": len(ops),
        "sketch_promises": len(promises),
        "unfulfilled_sketch_promises": len(unfulfilled),
        "invented_name_mentions": sum(1 for e in es if "marc" in (e.text or "").lower()),
        "turns": st.turns,
    }


# ---- prompt budget lint ---------------------------------------------------------------
_IMPERATIVE = re.compile(r"\b(MUST|NEVER|ALWAYS|DO NOT|MUST NOT|HARD MAX|CRITICAL|ONLY)\b")


def prompt_metrics(**speak_kwargs) -> dict:
    """Capture the REAL system/user prompt of a member turn without calling Bedrock, and
    measure its budget. Rule count and size are the resource every behavioural prompt fix
    spends; this is the ratchet that stops it growing."""
    from tessera_platform.quorum.bedrock_speaker import BedrockSpeaker

    sp = object.__new__(BedrockSpeaker)        # no boto3 client, no creds
    sp.model_id, sp.region = "test", "us-east-1"
    captured: dict = {}

    def _fake_ask(system: str, user: str, max_tokens: int = 1200) -> str:
        captured["system"], captured["user"], captured["max_tokens"] = system, user, max_tokens
        return ('Spoken line.\n===DETAIL===\nDetail body.\n===META===\n'
                '{"type":"claim","severity":"note","target":null,"topic":"t"}')

    sp._ask = _fake_ask                        # type: ignore[method-assign]
    persona = pool()[1]                        # the architect (a drawing persona)
    kwargs = {"whiteboard": True, "board_digest": "", "meeting_frame": "Architecture Review Board"}
    kwargs.update(speak_kwargs)
    sp.speak(persona, DECISION, CONTEXT, [], 1, **kwargs)

    sys_p = captured.get("system", "")
    return {
        "system": sys_p,
        "user": captured.get("user", ""),
        "system_chars": len(sys_p),
        "user_chars": len(captured.get("user", "")),
        "imperatives": len(_IMPERATIVE.findall(sys_p)),
        "max_tokens": captured.get("max_tokens", 0),
    }

# ---- diagram aesthetics (empirical priority order) ------------------------------------
# Purchase's experiments: "reducing the number of edge crosses is BY FAR the most important
# aesthetic, while minimising the number of bends and maximising symmetry have a lesser effect";
# later work adds that aligning nodes to an underlying grid matters. So we measure crossings first,
# then bends, then grid alignment — and treat them as the definition of "ugly".
def _segments(op) -> list:
    d = op.data or {}
    pts = d.get("points")
    if isinstance(pts, list) and len(pts) >= 2:
        seq = pts
    else:
        f, t = d.get("from"), d.get("to")
        if not (isinstance(f, list) and isinstance(t, list)):
            return []
        seq = [f, t]
    out = []
    for i in range(len(seq) - 1):
        try:
            out.append(((float(seq[i][0]), float(seq[i][1])),
                        (float(seq[i + 1][0]), float(seq[i + 1][1]))))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _crosses(a, b) -> bool:
    (x1, y1), (x2, y2) = a
    (x3, y3), (x4, y4) = b
    if {(x1, y1), (x2, y2)} & {(x3, y3), (x4, y4)}:
        return False                      # shared endpoint is not a crossing
    def o(p, q, r):
        v = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    o1, o2 = o((x1, y1), (x2, y2), (x3, y3)), o((x1, y1), (x2, y2), (x4, y4))
    o3, o4 = o((x3, y3), (x4, y4), (x1, y1)), o((x3, y3), (x4, y4), (x2, y2))
    return o1 != o2 and o3 != o4


def diagram_aesthetics(ops: list) -> dict:
    """Crossings / bends / grid alignment for a rendered sketch (list of op dicts or WhiteboardOps)."""
    class _O:
        def __init__(self, d): self.kind = d.get("kind"); self.data = d
    items = [_O(o) if isinstance(o, dict) else o for o in ops]
    arrows = [o for o in items if o.kind == "arrow"]
    segs = []
    for a in arrows:
        segs.extend(_segments(a))
    # Edges leaving or entering the SAME node overlap near that node — a bus, not a crossing. Only
    # count pairs of edges with no shared endpoint node (the standard graph-drawing definition).
    tagged = []
    for a in arrows:
        d = a.data or {}
        ends = (d.get("a"), d.get("b"))
        for sg in _segments(a):
            tagged.append((ends, sg))
    crossings = 0
    for i in range(len(tagged)):
        for j in range(i + 1, len(tagged)):
            (e1, s1), (e2, s2) = tagged[i], tagged[j]
            if e1 == e2:
                continue                       # same edge
            if e1[0] is not None and set(e1) & set(e2):
                continue                       # share a node
            if _crosses(s1, s2):
                crossings += 1
    bends = 0
    for a in arrows:
        pts = (a.data or {}).get("points")
        if isinstance(pts, list) and len(pts) > 2:
            bends += len(pts) - 2
    boxes = [o for o in items if o.kind == "box" and not (o.data or {}).get("container")]
    xs = Counter(round(float((o.data or {}).get("x", 0))) for o in boxes)
    ys = Counter(round(float((o.data or {}).get("y", 0))) for o in boxes)
    aligned = sum(1 for o in boxes
                  if xs[round(float((o.data or {}).get("x", 0)))] > 1
                  or ys[round(float((o.data or {}).get("y", 0)))] > 1)
    return {"crossings": crossings, "bends": bends,
            "grid_aligned": round(aligned / len(boxes), 2) if boxes else 1.0,
            "nodes": len(boxes), "arrows": len(arrows)}
