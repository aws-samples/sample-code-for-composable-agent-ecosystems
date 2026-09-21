"""Quorum Whiteboard — the shared board an invited member (or the submitter) can draw on.

See docs/spec-quorum-whiteboard.md. This module is intentionally substrate-free (no AWS/LLM
imports): it holds the affinity policy helpers and the **append-only op log** that is the
board's persisted, replayable state. `session.py` translates a member's turn-JSON `whiteboard`
block into ops here and emits incremental `WHITEBOARD_OP` events; the frontend animates them.

Isolation contract: the whiteboard is inert unless BOTH the environment flag `WHITEBOARD_ENABLED`
is on AND the eco opts in (`quorum.whiteboard.enabled: true`). With either off, none of this code
changes existing behaviour.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ---- affinity (rules 1 & 2) -----------------------------------------------------------
AFFINITIES = ("none", "low", "medium", "high")
# base per-turn probability that a warranted visual is actually drawn, by affinity (rule 1
# throttle). `none` never draws; higher affinity draws more readily.
AFFINITY_WEIGHT = {"none": 0.0, "low": 0.34, "medium": 0.66, "high": 1.0}

# op kinds the renderer knows how to animate (rule 7)
OP_KINDS = ("box", "arrow", "text", "stroke")


def normalize_affinity(v: Any) -> str:
    v = (str(v or "none")).strip().lower()
    return v if v in AFFINITIES else "none"


def env_enabled() -> bool:
    """Global fail-closed flag (mirrors QUORUM_ENABLED)."""
    return os.environ.get("WHITEBOARD_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def whiteboard_enabled(policy: dict) -> bool:
    """The board is live only when the env flag is on AND the eco opts in."""
    wb = (policy or {}).get("whiteboard", {}) or {}
    return bool(env_enabled() and wb.get("enabled", False))


def apply_affinity(by_id: dict, policy: dict) -> None:
    """Apply per-eco persona-affinity overrides (spec §6) onto the shared persona map, in place.
    Catalog defaults (Persona.whiteboard_affinity) stand unless the eco overrides them. When the
    board is enabled, personas that still resolve to `none` are INFERRED from their nature
    (architects / design / systems personas draw; narrative / policy personas don't) so rule 2
    works even when the panel is SYNTHESIZED at runtime (no fixed catalog ids to override).
    Safe to call always; explicit catalog values and eco overrides always win."""
    wb = (policy or {}).get("whiteboard", {}) or {}
    overrides = wb.get("persona_affinity", {}) or {}
    overridden = set()
    for pid, aff in overrides.items():
        p = by_id.get(pid)
        if p is not None:
            p.whiteboard_affinity = normalize_affinity(aff)
            overridden.add(pid)
    if wb.get("enabled", False) and wb.get("infer_affinity", True):
        for pid, p in by_id.items():
            if pid in overridden:
                continue
            if getattr(p, "whiteboard_affinity", "none") != "none":
                continue   # explicit catalog value stands
            p.whiteboard_affinity = _infer_affinity(p)


# persona-nature keywords → drawing propensity (deterministic rule-2 inference)
_HIGH_HINTS = ("architect", "architecture", "systems", "system design", "solution", "design",
               "topology", "data flow", "dataflow", "integration", "engineer", "technical")
_MED_HINTS = ("process", "workflow", "operational", "operations", "controls", "security", "network")


def _infer_affinity(persona) -> str:
    blob = " ".join([getattr(persona, "stance", "") or "",
                     getattr(persona, "name", "") or "",
                     " ".join(getattr(persona, "dimensions", []) or [])]).lower()
    if any(h in blob for h in _HIGH_HINTS):
        return "high"
    if any(h in blob for h in _MED_HINTS):
        return "medium"
    return "none"


def validate_policy(wb: dict) -> list[str]:
    """Validate a `quorum.whiteboard` policy block. Returns a list of error strings (empty = ok).
    Used by save-ecosystem (fail-closed on bad config)."""
    errs: list[str] = []
    if wb is None:
        return errs
    if not isinstance(wb, dict):
        return ["quorum.whiteboard must be a mapping"]
    if "enabled" in wb and not isinstance(wb["enabled"], bool):
        errs.append("quorum.whiteboard.enabled must be a boolean")
    aff = wb.get("persona_affinity", {})
    if aff is None:
        aff = {}
    if not isinstance(aff, dict):
        errs.append("quorum.whiteboard.persona_affinity must be a mapping of persona_id -> affinity")
    else:
        for pid, v in aff.items():
            if str(v or "").strip().lower() not in AFFINITIES:
                errs.append(f"quorum.whiteboard.persona_affinity[{pid}] must be one of {AFFINITIES}")
    return errs


# ---- board op log (persisted artifact _whiteboard.json) -------------------------------
@dataclass
class WhiteboardOp:
    op_id: str
    author: str                                  # persona_id | "human"
    kind: str                                    # OP_KINDS
    data: dict = field(default_factory=dict)     # geometry per kind (box/arrow/text/stroke)
    refs: list[str] = field(default_factory=list)  # transcript entry_ids this visual supports (rule 6)
    seq: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Whiteboard:
    """Append-only op log + erase tombstones + pending erase-consent requests (rule 3)."""
    ops: list[WhiteboardOp] = field(default_factory=list)
    erased: list[str] = field(default_factory=list)          # op_ids tombstoned
    pending_erase: list[dict] = field(default_factory=list)  # [{op_id, by, reason, owner}]
    _n: int = 0

    # --- mutation ---
    def add_op(self, author: str, kind: str, data: dict, refs: Optional[list[str]] = None) -> WhiteboardOp:
        if kind not in OP_KINDS:
            raise ValueError(f"unknown whiteboard op kind {kind!r}")
        self._n += 1
        op = WhiteboardOp(op_id=f"wb-{self._n}", author=author, kind=kind,
                          data=data or {}, refs=list(refs or []), seq=time.time())
        self.ops.append(op)
        return op

    def owner_of(self, op_id: str) -> Optional[str]:
        for o in self.ops:
            if o.op_id == op_id:
                return o.author
        return None

    def request_erase(self, op_id: str, by: str, reason: str = "") -> Optional[dict]:
        """Rule 3: record a consent request; NEVER erases. Returns the request, or None if the
        op is the requester's own (own work may be amended without asking) or doesn't exist."""
        owner = self.owner_of(op_id)
        if owner is None or owner == by:
            return None
        req = {"op_id": op_id, "by": by, "reason": reason, "owner": owner}
        self.pending_erase.append(req)
        return req

    def apply_erase(self, op_id: str, approved_by: str) -> bool:
        """Apply an erase only after consent (owner or the human submitter). Returns True if applied."""
        if op_id in self.erased:
            return True
        if op_id not in {o.op_id for o in self.ops}:
            return False
        self.erased.append(op_id)
        self.pending_erase = [r for r in self.pending_erase if r["op_id"] != op_id]
        return True

    # --- views ---
    def live_ops(self) -> list[WhiteboardOp]:
        return [o for o in self.ops if o.op_id not in self.erased]

    def inventory(self, limit: int = 24) -> list:
        """The live board as an ADDRESSABLE list — id, label, author, kind.

        Members previously received the board only as prose, so they could not point at anything on
        it: every sketch had to be a brand-new diagram, and "circle those three" or "cross that out"
        were unsayable. A human whiteboard is iterative — people mostly annotate and extend what is
        already there — so the board has to be addressable for that to be possible at all."""
        out = []
        for o in self.live_ops():
            d = o.data or {}
            if o.kind == "box" and not d.get("container"):
                label = str(d.get("label") or "").replace("\n", " ").strip()
            elif o.kind == "text":
                label = str(d.get("text") or "").replace("\n", " ").strip()
            else:
                continue
            if label:
                out.append({"id": o.op_id, "label": label[:60], "author": o.author, "kind": o.kind})
        return out[-limit:]

    def rect_of(self, op_id: str):
        """Bounding box of a live element, so an annotation can be placed against it."""
        for o in self.live_ops():
            if o.op_id != op_id:
                continue
            d = o.data or {}
            try:
                x, y = float(d.get("x", 0)), float(d.get("y", 0))
            except (TypeError, ValueError):
                return None
            if o.kind == "box":
                return (x, y, float(d.get("w", _BOX_W)), float(d.get("h", _BOX_H)))
            if o.kind == "text":
                n = len(str(d.get("text") or "").split("\n"))
                return (x, y, 9.0 * min(60, len(str(d.get("text") or ""))), 18.0 * n)
        return None

    def digest(self, limit: int = 40) -> str:
        """A compact textual description of the live board, fed to members + the verdict (rule 6)."""
        live = self.live_ops()[:limit]
        if not live:
            return ""
        parts = []
        for o in live:
            d = o.data or {}
            if o.kind == "box":
                parts.append(f'[{o.author}] box "{(d.get("label") or "").replace(chr(10)," ")}"')
            elif o.kind == "arrow":
                parts.append(f'[{o.author}] arrow {d.get("from")}→{d.get("to")}')
            elif o.kind == "text":
                parts.append(f'[{o.author}] note "{(d.get("text") or "").replace(chr(10)," ")}"')
            elif o.kind == "stroke":
                parts.append(f'[{o.author}] sketch ({len(d.get("points") or [])} pts)')
        return "board: " + "; ".join(parts)

    def to_dict(self) -> dict:
        return {"ops": [o.to_dict() for o in self.ops], "erased": list(self.erased),
                "pending_erase": list(self.pending_erase)}

    @classmethod
    def from_dict(cls, d: dict) -> "Whiteboard":
        wb = cls()
        for od in (d or {}).get("ops", []):
            op = WhiteboardOp(op_id=od.get("op_id", ""), author=od.get("author", ""),
                              kind=od.get("kind", "text"), data=od.get("data", {}) or {},
                              refs=list(od.get("refs") or []), seq=od.get("seq", 0.0))
            wb.ops.append(op)
        wb.erased = list((d or {}).get("erased") or [])
        wb.pending_erase = list((d or {}).get("pending_erase") or [])
        # restore the counter so new ids don't collide
        mx = 0
        for o in wb.ops:
            try:
                mx = max(mx, int(str(o.op_id).split("-")[-1]))
            except (ValueError, IndexError):
                pass
        wb._n = mx
        return wb


# ---- typed sketch layout (server-owned geometry) --------------------------------------
# Members used to emit raw coordinates. Measured result on a real board: 62 overlapping box pairs
# (every sketch starts at the top-left because each call sees a blank canvas), arrows that land on
# nothing (0 of 4 in one sketch), lists of nouns drawn as boxes, and the same label drawn 3x. So the
# model now describes WHAT the diagram shows and the server decides WHERE everything goes: overlap
# and dangling arrows become structurally impossible, and every sketch carries a stated purpose.
SKETCH_TYPES = ("flow", "failure", "boundary", "decision", "matrix",
                "bowtie", "swimlane", "timeline")

# Points get PARAPHRASED, which a string comparison misses: one live board carried both "Shared
# Postgres failure cascades to all 12 services simultaneously" and "Postgres down kills all 12
# services, no refunds possible" — the same diagram twice. Same trick as topic matching: reduce a
# point to the salient things it is ABOUT and refuse a claim the board already makes.
_POINT_STOP = {
    "the", "and", "for", "with", "that", "this", "into", "from", "when", "then", "than", "under",
    "over", "only", "must", "will", "would", "could", "should", "have", "has", "not", "but", "all",
    "any", "its", "their", "there", "here", "which", "what", "where", "while", "before", "after",
    "every", "each", "some", "more", "most", "less", "than", "also", "still", "again", "means",
    "makes", "make", "made", "creates", "create", "causes", "cause", "leads", "lead", "breaks",
    "break", "kills", "kill", "flows", "flow", "goes", "gets", "gives", "risk", "risks", "issue",
}


def point_subject(point: str) -> frozenset:
    acronyms = {a.lower() for a in re.findall(r"\b[A-Z]{2,5}\b", point or "")}
    words = {w for w in re.findall(r"[a-z]+", (point or "").lower())
             if len(w) >= 5 and w not in _POINT_STOP}
    nums = set(re.findall(r"\b\d{1,4}\b", point or ""))
    return frozenset(acronyms | words | nums)

# A real whiteboard is not just boxes and lines: people circle the thing that matters, underline a
# condition, cross out what is ruled out, and use a red marker for danger. The board was measured as
# "10 boxes, 8 arrows, 3 text" — a flowchart generator. These are expressed with the primitives the
# client already animates (freehand strokes + an explicit colour), so no new client shapes are needed.
NODE_COLORS = {
    "risk": "#e03131", "threat": "#e03131", "impact": "#e03131", "failure": "#e03131",
    "control": "#2f9e44", "mitigation": "#2f9e44", "condition": "#1971c2",
    "store": "#7048e8", "data": "#7048e8", "actor": "#f08c00", "service": "#1971c2",
}
# What each declared kind LOOKS like. Deliberately the vocabulary people actually use at a whiteboard:
# a cylinder for a store, a stick/oval for a person, a diamond for a decision or risk, a jagged burst for
# an impact, a shield for a control. Unknown kinds stay rectangles.
NODE_SHAPES = {
    "risk": "diamond", "threat": "diamond", "failure": "diamond",
    "impact": "burst",
    "control": "shield", "mitigation": "shield",
    "condition": "rect",
    "store": "cylinder", "data": "cylinder",
    "actor": "person",
    "service": "rounded",
}
EMPHASIS = ("circle", "underline", "strike")

# The model was given `kind` and `emphasis` and mostly ignored them: a measured live board came back
# with 21 boxes, 2 colours and ZERO emphasis strokes — still "boxes and lines only". Rather than
# nagging it harder in the prompt (which costs adherence elsewhere), infer from the label.
_KIND_HINTS = (
    ("risk", ("breach", "leak", "leaked", "injection", "attack", "attacker", "abuse", "fraud",
              "exposure", "unauthoris", "unauthoriz", "poison", "exfil", "spike", "runaway",
              "contention", "outage", "full", "stall", "failure", "fails", "loss", "lost",
              "corrupt", "downtime", "breach")),
    ("control", ("gate", "guard", "filter", "validat", "tokenis", "tokeniz", "encrypt", "redact",
                 "review", "approval", "approved", "limit", "cap", "quota", "breaker", "kill",
                 "override", "sanitis", "sanitiz", "consent", "opt-out",
                 "alert", "detect", "backup", "failover", "sla")),
    ("store", ("postgres", "database", "db", "table", "ledger", "store", "bucket", "log", "logs",
               "index", "cache", "queue", "memory")),
    ("actor", ("customer", "user", "engineer", "operator", "agent", "human", "analyst", "officer",
               "regulator", "vendor")),
)


def infer_kind(label: str) -> str:
    low = (label or "").lower()
    for kind, hints in _KIND_HINTS:
        if any(h in low for h in hints):
            return kind
    return ""


def _emphasis_ops(rect: dict, how: str, color: str) -> list[dict]:
    """Hand-drawn emphasis over an existing box, as a freehand stroke."""
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    if how == "underline":
        pts = [[x + 6, y + h + 7], [x + w * 0.45, y + h + 10], [x + w - 6, y + h + 6]]
    elif how == "strike":
        pts = [[x + 4, y + h * 0.62], [x + w * 0.5, y + h * 0.42], [x + w - 4, y + h * 0.58]]
    else:  # circle — a generous, slightly uneven loop, not a tight halo on the box outline
        pts = lasso(x, y, w, h, slack=14.0)
    return [{"kind": "stroke", "points": [[round(p[0], 1), round(p[1], 1)] for p in pts],
             "color": color, "width": 2.6}]

_BOX_W, _BOX_H, _GAP_X, _GAP_Y = 190, 62, 56, 46
_PER_ROW = 4


def fit_label(text: str, per_line: int = 20, max_lines: int = 3) -> str:
    """Wrap on WORD boundaries. Labels used to be hard-cut at a character count, which put things
    like "Tom's right that 99.9% is an avail" on the board. Never break a word; if it still does not
    fit, drop whole trailing words and mark the cut."""
    words = str(text or "").split()
    if not words:
        return ""
    lines, cur = [], ""
    for w in words:
        if len(w) > per_line:                       # a single over-long token: cut it once, visibly
            w = w[:per_line - 1] + "\u2026"
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= per_line:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    out = "\n".join(lines[:max_lines])
    consumed = len(" ".join(out.split()))
    if consumed < len(" ".join(words)) - 1:
        out = out.rstrip(".,;:") + "\u2026"          # signal that words were dropped
    return out



def lasso(x: float, y: float, w: float, h: float, slack: float = 16.0, n: int = 20) -> list:
    """A loose marker loop around a REGION — the thing a person actually does.

    The old emphasis drew a fixed ellipse from a single box's own rectangle, which read as a
    mechanical halo. A human lassos a cluster (or one word), generously and a little unevenly, and the
    size and placement of the loop is what carries the meaning."""
    import math
    cx, cy = x + w / 2.0, y + h / 2.0
    rx, ry = w / 2.0 + slack, h / 2.0 + slack * 0.85
    pts = []
    for i in range(n):
        a = (i / (n - 1)) * 2 * math.pi * 1.08          # overshoot, like closing a loop by hand
        wobble = 1.0 + (0.035 if i % 3 else -0.03)      # deterministic unevenness
        pts.append([round(cx + rx * wobble * math.cos(a), 1),
                    round(cy + ry * wobble * math.sin(a), 1)])
    return pts


def render_annotations(anns: list, board: "Whiteboard") -> list:
    """Marks that refer to what is ALREADY on the board: lasso a group, strike it out, underline it,
    or attach a note with a leader line. Annotating is additive, so unlike erasing it needs no
    consent — a person circling someone else's box is ordinary, and polite."""
    ops: list = []
    for a in (anns or []):
        if not isinstance(a, dict):
            continue
        mark = str(a.get("mark") or "circle").strip().lower()
        targets = a.get("targets") or ([a.get("target")] if a.get("target") else [])
        rects = [r for r in (board.rect_of(str(t)) for t in targets) if r]
        if not rects:
            continue
        colour = str(a.get("color") or "#e03131")
        x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects); y1 = max(r[1] + r[3] for r in rects)
        if mark == "strike":
            for (x, y, w, h) in rects:
                ops.append({"kind": "stroke", "color": colour, "width": 2.6,
                            "points": [[x + 4, y + h * 0.62], [x + w * 0.5, y + h * 0.42],
                                       [x + w - 4, y + h * 0.58]]})
        elif mark == "underline":
            for (x, y, w, h) in rects:
                ops.append({"kind": "stroke", "color": colour, "width": 2.6,
                            "points": [[x + 6, y + h + 7], [x + w * 0.45, y + h + 10],
                                       [x + w - 6, y + h + 6]]})
        elif mark == "note":
            tx, ty = x1 + 34, y0 - 6                    # note sits clear of the thing it points at
            ops.append({"kind": "stroke", "color": colour, "width": 2.0,
                        "points": [[x1 + 4, y0 + (y1 - y0) / 2], [tx - 8, ty + 12]]})
            ops.append({"kind": "text", "x": tx, "y": ty, "size": 15, "color": colour,
                        "text": fit_label(str(a.get("text") or ""), per_line=26, max_lines=3)})
        else:                                            # circle / group — one loop around them all
            ops.append({"kind": "stroke", "color": colour, "width": 2.6,
                        "points": lasso(x0, y0, x1 - x0, y1 - y0,
                                        slack=18.0 if len(rects) > 1 else 14.0)})
            if a.get("text"):
                ops.append({"kind": "text", "x": x0, "y": y0 - 30, "size": 15, "color": colour,
                            "text": fit_label(str(a.get("text")), per_line=30, max_lines=2)})
    return ops


def audit_ops(ops: list, stype: str = "") -> dict:
    """Inspect a rendered sketch the way a reader would, in code.

    The pipeline was blind: a spec went in, ops came out, and nobody ever looked. This is the
    auditor half of the planner–auditor loop (DiagrammerGPT, COLM 2024). Geometry is checked here
    because code does it exactly — crossings, bends and overflow are arithmetic, not opinion — and
    the numbers are ordered by the aesthetics that measurably affect comprehension: crossings first
    (Purchase), then bends, then alignment."""
    boxes = [o for o in ops if o.get("kind") == "box" and not o.get("container")]
    arrows = [o for o in ops if o.get("kind") == "arrow"]
    texts = [o for o in ops if o.get("kind") == "text"]

    def segs(a):
        pts = a.get("points")
        if isinstance(pts, list) and len(pts) >= 2:
            seq = pts
        else:
            f, t = a.get("from"), a.get("to")
            if not (isinstance(f, list) and isinstance(t, list)):
                return []
            seq = [f, t]
        return [((float(seq[i][0]), float(seq[i][1])), (float(seq[i + 1][0]), float(seq[i + 1][1])))
                for i in range(len(seq) - 1)]

    def crosses(p, q):
        (x1, y1), (x2, y2) = p
        (x3, y3), (x4, y4) = q
        if {(x1, y1), (x2, y2)} & {(x3, y3), (x4, y4)}:
            return False
        def o(a, b, c):
            v = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
            return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
        return (o((x1, y1), (x2, y2), (x3, y3)) != o((x1, y1), (x2, y2), (x4, y4))
                and o((x3, y3), (x4, y4), (x1, y1)) != o((x3, y3), (x4, y4), (x2, y2)))

    tagged = [((a.get("a"), a.get("b")), sg) for a in arrows for sg in segs(a)]
    crossings = 0
    for i in range(len(tagged)):
        for j in range(i + 1, len(tagged)):
            (e1, s1), (e2, s2) = tagged[i], tagged[j]
            if e1 == e2 or (e1[0] is not None and set(e1) & set(e2)):
                continue
            if crosses(s1, s2):
                crossings += 1
    bends = sum(max(0, len(a.get("points", [])) - 2) for a in arrows if a.get("points"))

    overlaps = 0
    rects = [(float(b["x"]), float(b["y"]), float(b.get("w", 0)), float(b.get("h", 0))) for b in boxes]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            x1, y1, w1, h1 = rects[i]
            x2, y2, w2, h2 = rects[j]
            if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                overlaps += 1

    truncated = [str(b.get("label") or "") for b in boxes if str(b.get("label") or "").endswith("\u2026")]
    problems = []
    if crossings:
        problems.append(f"{crossings} edge crossing(s) — the hardest thing for a reader to follow")
    if overlaps:
        problems.append(f"{overlaps} overlapping box pair(s)")
    if truncated:
        problems.append(f"{len(truncated)} label(s) too long to fit: {truncated[:3]}")
    if len(boxes) >= 7:
        # 7 is the renderer's hard cap, so hitting it means nodes beyond the seventh were DROPPED —
        # the drawing no longer says what was asked for. Better to split the idea than to truncate it.
        problems.append(f"{len(boxes)} boxes — at the limit and too many to read; nodes past the "
                        f"seventh were dropped, so split this into two sharper sketches")
    if arrows and not any(a.get("points") or a.get("from") for a in arrows):
        problems.append("arrows are not attached to anything")
    if len(boxes) >= 3 and not arrows and str(stype).lower() not in ("swimlane", "timeline", "matrix"):
        # lanes and axes carry the structure themselves, so those genres need no arrows
        problems.append("boxes with no arrows — that is a list, not a diagram; show the relationships")

    return {"boxes": len(boxes), "arrows": len(arrows), "titles": len(texts),
            "crossings": crossings, "bends": bends, "overlaps": overlaps,
            "truncated_labels": len(truncated), "problems": problems}


def describe_ops(ops: list) -> str:
    """A compact, faithful account of what was actually drawn — the layout as a reader receives it.
    Used alongside the rendered image so the critique can reference concrete elements."""
    boxes = [o for o in ops if o.get("kind") == "box" and not o.get("container")]
    cols: dict = {}
    for b in boxes:
        cols.setdefault(round(float(b.get("x", 0))), []).append(b)
    lines = []
    for ci, x in enumerate(sorted(cols)):
        labels = [str(b.get("label") or "").replace("\n", " ") for b in
                  sorted(cols[x], key=lambda b: float(b.get("y", 0)))]
        lines.append(f"  column {ci + 1}: " + " | ".join(labels))
    arrows = [o for o in ops if o.get("kind") == "arrow"]
    routed = sum(1 for a in arrows if len(a.get("points", [])) > 2)
    lines.append(f"  {len(arrows)} arrow(s), {routed} of them routed around the diagram")
    return "\n".join(lines)


def render_png(ops: list, scale: float = 0.6) -> Optional[bytes]:
    """Rasterise the board so a multimodal model can LOOK at it.

    Whiteboard-of-Thought (EMNLP 2024) hands the rendered image back to the model, and Visual
    Sketchpad has it reason "according to the visual artifacts it has drawn". This is not
    pixel-identical to the client's sketchy rendering — it reproduces geometry, labels and colour,
    which is what a critique needs to judge. Returns None if the imaging library is unavailable, so
    the loop degrades to the geometry audit rather than breaking the meeting."""
    try:
        from PIL import Image, ImageDraw          # noqa: PLC0415 - optional, resolved at runtime
    except Exception:                              # noqa: BLE001
        return None
    items = [o for o in ops if o.get("kind") in OP_KINDS]
    if not items:
        return None
    xs, ys = [0.0], [0.0]
    for o in items:
        if o.get("kind") in ("box", "text"):
            xs += [float(o.get("x", 0)), float(o.get("x", 0)) + float(o.get("w", 200))]
            ys += [float(o.get("y", 0)), float(o.get("y", 0)) + float(o.get("h", 40))]
        for p in (o.get("points") or []):
            xs.append(float(p[0])); ys.append(float(p[1]))
        for k in ("from", "to"):
            if isinstance(o.get(k), list):
                xs.append(float(o[k][0])); ys.append(float(o[k][1]))
    pad = 24
    minx, miny, maxx, maxy = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    W = max(64, int((maxx - minx) * scale)); H = max(64, int((maxy - miny) * scale))
    if W * H > 4_000_000:
        return None                                # refuse to build an absurd image
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    def T(x, y):
        return ((float(x) - minx) * scale, (float(y) - miny) * scale)
    for o in items:
        col = o.get("color") or "#1e1e1e"
        if o["kind"] == "box":
            x0, y0 = T(o.get("x", 0), o.get("y", 0))
            x1, y1 = T(float(o.get("x", 0)) + float(o.get("w", 190)),
                       float(o.get("y", 0)) + float(o.get("h", 62)))
            d.rectangle([x0, y0, x1, y1], outline=col, width=2)
            for i, line in enumerate(str(o.get("label") or "").split("\n")):
                d.text((x0 + 6, y0 + 6 + i * 12), line[:40], fill=col)
        elif o["kind"] in ("arrow", "stroke"):
            pts = o.get("points")
            if not pts:
                f, t = o.get("from"), o.get("to")
                pts = [f, t] if isinstance(f, list) and isinstance(t, list) else None
            if pts and len(pts) >= 2:
                d.line([T(p[0], p[1]) for p in pts], fill=(col if o["kind"] == "stroke" else "#868e96"),
                       width=2)
        elif o["kind"] == "text":
            x0, y0 = T(o.get("x", 0), o.get("y", 0))
            for i, line in enumerate(str(o.get("text") or "").split("\n")):
                d.text((x0, y0 + i * 13), line[:90], fill="#111111")
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _break_cycles(n: int, edges: list) -> tuple:
    """Split edges into forward edges and back edges (DFS). Layering needs an acyclic graph; the
    back edges are drawn separately, routed around the diagram."""
    adj = {i: [] for i in range(n)}
    for a, b, _ in edges:
        adj[a].append(b)
    colour, back = {}, set()
    def visit(u):
        colour[u] = 1                       # on the stack
        for v in adj[u]:
            if colour.get(v) == 1:
                back.add((u, v))
            elif colour.get(v) is None:
                visit(v)
        colour[u] = 2
    for i in range(n):
        if colour.get(i) is None:
            visit(i)
    fwd = [(a, b, l) for (a, b, l) in edges if (a, b) not in back]
    bck = [(a, b, l) for (a, b, l) in edges if (a, b) in back]
    return fwd, bck


def _assign_layers(n: int, fwd: list) -> list:
    """Longest-path layering: a node sits one layer right of its deepest predecessor."""
    layer = [0] * n
    for _ in range(n):                      # relax until stable (n passes is ample for n<=~12)
        changed = False
        for a, b, _ in fwd:
            if layer[b] < layer[a] + 1:
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    return layer


def _count_crossings(order_a: list, order_b: list, pairs: list) -> int:
    """Crossings between two adjacent layers = inversions in the edge endpoint ordering."""
    pos_a = {v: i for i, v in enumerate(order_a)}
    pos_b = {v: i for i, v in enumerate(order_b)}
    es = [(pos_a[a], pos_b[b]) for a, b in pairs if a in pos_a and b in pos_b]
    es.sort()
    return sum(1 for i in range(len(es)) for j in range(i + 1, len(es)) if es[j][1] < es[i][1])


def _order_layers(layers: list, fwd: list) -> list:
    """Reduce crossings: barycentre sweeps, then accept any adjacent swap that measurably helps.
    Crossings are the aesthetic that dominates comprehension, so this is the part worth doing."""
    orders = [list(l) for l in layers]
    pairs_between = []
    for i in range(len(orders) - 1):
        pairs_between.append([(a, b) for a, b, _ in fwd
                              if a in orders[i] and b in orders[i + 1]])

    def total() -> int:
        return sum(_count_crossings(orders[i], orders[i + 1], pairs_between[i])
                   for i in range(len(orders) - 1))

    for sweep in range(4):
        rng = range(1, len(orders)) if sweep % 2 == 0 else range(len(orders) - 2, -1, -1)
        for i in rng:
            ref = orders[i - 1] if sweep % 2 == 0 else orders[i + 1]
            refpos = {v: k for k, v in enumerate(ref)}
            def bary(v):
                if sweep % 2 == 0:
                    nb = [refpos[a] for a, b, _ in fwd if b == v and a in refpos]
                else:
                    nb = [refpos[b] for a, b, _ in fwd if a == v and b in refpos]
                return sum(nb) / len(nb) if nb else 1e6
            orders[i] = sorted(orders[i], key=lambda v: (bary(v), v))
    improved = True
    while improved:                          # exact local improvement
        improved = False
        for i, layer in enumerate(orders):
            for k in range(len(layer) - 1):
                before = total()
                layer[k], layer[k + 1] = layer[k + 1], layer[k]
                if total() < before:
                    improved = True
                else:
                    layer[k], layer[k + 1] = layer[k + 1], layer[k]
    return orders


def layered_layout(n: int, edges: list, box_w: int = _BOX_W, box_h: int = _BOX_H,
                   gap_x: int = 96, gap_y: int = 46, x0: int = 60, y0: int = 0) -> tuple:
    """Place nodes in layers left-to-right and route edges orthogonally.

    Hand-rolled row/column packing produced crossings as soon as a graph had a skip or back edge
    (measured: 2 crossings on a 6-node flow). Empirically, "reducing the number of edge crosses is by
    far the most important aesthetic" (Purchase), followed by bends, with grid alignment also
    mattering — so we layer, minimise crossings, snap to a uniform grid, and keep routes orthogonal.
    Graphs here are tiny (<=7 nodes); if they ever grow past ~12, swap this for ELK or Graphviz.

    Returns (positions, routes) where routes[i] is a polyline for edges[i]."""
    edges = [(int(a), int(b), l) for a, b, l in edges if 0 <= int(a) < n and 0 <= int(b) < n and a != b]
    fwd, bck = _break_cycles(n, edges)
    layer = _assign_layers(n, fwd)
    by_layer: dict = {}
    for i in range(n):
        by_layer.setdefault(layer[i], []).append(i)
    orders = _order_layers([by_layer[k] for k in sorted(by_layer)], fwd)

    pos = [(0.0, 0.0)] * n
    for li, order in enumerate(orders):
        for oi, v in enumerate(order):
            pos[v] = (x0 + li * (box_w + gap_x), y0 + oi * (box_h + gap_y))
    bottom = max((y for _, y in pos), default=y0) + box_h

    def right(v): return (pos[v][0] + box_w, pos[v][1] + box_h / 2)
    def left(v):  return (pos[v][0], pos[v][1] + box_h / 2)
    def bot(v):   return (pos[v][0] + box_w / 2, pos[v][1] + box_h)
    def top(v):   return (pos[v][0] + box_w / 2, pos[v][1])

    # Each inter-layer edge gets its OWN vertical channel inside the gap between layers: sharing a
    # single midline made a 1->4 fan-out cross itself six times.
    gap_edges: dict = {}
    for idx, (a, b, _l) in enumerate(edges):
        if layer[b] == layer[a] + 1:
            gap_edges.setdefault(layer[a], []).append(idx)
    channel = {}
    for lay, idxs in gap_edges.items():
        for k, idx in enumerate(idxs):
            channel[idx] = (k + 1) / (len(idxs) + 1)

    # Lanes for skip/back edges: longest span furthest from the diagram so routes nest instead of
    # interleaving, and each lane's verticals are nudged sideways so two routes never share an x
    # (two nodes in the same column gave both lanes the identical vertical, guaranteeing a crossing).
    routed_ids = [i for i, (a, b, _l) in enumerate(edges)
                  if (a, b) in {(x, y) for x, y, _ in bck} or layer[b] <= layer[a]
                  or layer[b] > layer[a] + 1]
    span = {i: abs(pos[edges[i][1]][0] - pos[edges[i][0]][0]) for i in routed_ids}
    lane_of = {i: k + 1 for k, i in enumerate(sorted(routed_ids, key=lambda i: -span[i]))}

    routes, lane = [], 0
    for edge_i, (a, b, _l) in enumerate(edges):
        la, lb = layer[a], layer[b]
        if (a, b) in {(x, y) for x, y, _ in bck} or lb <= la:
            lane = lane_of[edge_i]           # a back edge gets its own lane under the diagram
            ly = bottom + 26 * lane
            nx = 9 * lane
            # approach from BENEATH, so enter the bottom edge: aiming at the top would draw the
            # vertical straight through the target box
            routes.append([list(bot(a)), [bot(a)[0] + nx, bot(a)[1]], [bot(a)[0] + nx, ly],
                           [bot(b)[0] - nx, ly], [bot(b)[0] - nx, bot(b)[1]], list(bot(b))])
        elif lb == la + 1:
            sa, sb = right(a), left(b)
            if abs(sa[1] - sb[1]) < 2:
                routes.append([list(sa), list(sb)])                      # straight, no bends
            else:
                frac = channel.get(edge_i, 0.5)
                midx = sa[0] + (sb[0] - sa[0]) * frac
                routes.append([list(sa), [midx, sa[1]], [midx, sb[1]], list(sb)])
        else:
            lane = lane_of[edge_i]           # a skip edge also gets its own lane
            ly = bottom + 26 * lane
            nx = 9 * lane
            routes.append([list(bot(a)), [bot(a)[0] + nx, bot(a)[1]], [bot(a)[0] + nx, ly],
                           [bot(b)[0] - nx, ly], [bot(b)[0] - nx, bot(b)[1]], list(bot(b))])
    return pos, routes


def _node_label(n) -> str:
    if isinstance(n, dict):
        return str(n.get("label") or n.get("text") or n.get("id") or "").strip()
    return str(n or "").strip()


def next_free_y(board: "Whiteboard") -> float:
    """Stack each new sketch BELOW everything already on the board. The canvas is infinite and the
    client fits the view to content, so a vertical stack can never collide (unlike model-chosen
    coordinates, which always clustered in the same corner)."""
    bottom = 0.0
    for o in board.live_ops():
        d = o.data or {}
        try:
            if o.kind in ("box", "text"):
                bottom = max(bottom, float(d.get("y", 0)) + float(d.get("h", 0) or 34))
            elif o.kind == "arrow":
                bottom = max(bottom, float((d.get("from") or [0, 0])[1]), float((d.get("to") or [0, 0])[1]))
            elif o.kind == "stroke":
                for p in (d.get("points") or []):
                    bottom = max(bottom, float(p[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return (bottom + 90) if bottom else 40.0




# What each discipline draws. This is a persona attribute assigned when the panel is convened — the
# equivalent of `voice` or `severity_bias` — NOT a per-drawing guess. Selection within a repertoire is
# the author's judgement about the topic, and is left to them.
_REPERTOIRE_BY_DISCIPLINE = (
    # order matters: the most specific discipline first. "resilience" reads as SRE, not safety.
    (("sre", "reliability", "resilience", "operations", "incident", "on-call", "availability"),
     ["failure", "timeline", "flow"]),
    (("safety", "hazard", "regulator", "compliance", "audit", "risk"),
     ["bowtie", "failure", "matrix"]),
    (("architect", "architecture", "engineer", "technical", "systems", "security", "data", "privacy"),
     ["flow", "boundary", "failure"]),
    (("chair", "facilitator", "governance", "board", "executive", "cto", "vp"),
     ["decision", "matrix", "swimlane"]),
    (("finance", "cost", "commercial", "product", "sponsor", "customer", "conduct"),
     ["matrix", "timeline", "decision"]),
)


def repertoire_for(persona) -> list:
    """The shapes this persona draws, from their discipline. Assigned once, at convening."""
    blob = " ".join([getattr(persona, "stance", "") or "", getattr(persona, "name", "") or "",
                     getattr(persona, "role", "") or "",
                     " ".join(getattr(persona, "dimensions", []) or [])]).lower()
    for hints, genres in _REPERTOIRE_BY_DISCIPLINE:
        if any(h in blob for h in hints):
            return list(genres)
    return ["flow", "decision"]



def resolve_board_label(board: "Whiteboard", label: str):
    """Find a live board element by (part of) its written label -> (op_id, rect) or None.

    This is what makes an EXTENSION possible. The sketch contract lets an edge name an element that is
    already on the board (`from_label`), so a member can add one box and connect it to what is up there
    instead of redrawing the whole diagram. Matched on the written text because that is what the member
    can see; ids are for annotations, which the model addresses differently.
    """
    want = " ".join(str(label or "").lower().split())
    if len(want) < 3:
        return None
    best = None
    for o in board.live_ops():
        if o.kind not in ("box", "text"):
            continue
        d = o.data or {}
        have = " ".join(str(d.get("label") or d.get("text") or "").lower().split())
        if not have:
            continue
        if have == want or want in have or have in want:
            rect = board.rect_of(o.op_id)
            if rect:
                # prefer the closest match in length, so "Postgres" does not bind to a long sentence
                score = abs(len(have) - len(want))
                if best is None or score < best[0]:
                    best = (score, o.op_id, rect)
    return (best[1], best[2]) if best else None


def _link_anchors(ops: list, anchors: list) -> list:
    """Draw the arrow that ties an extension to what is already on the board.

    A one-box extension is only a whiteboard move if it is CONNECTED — otherwise it is a box floating
    in space, which is how the first version of this shipped: the anchor resolved, the node drew, and
    nothing joined them.
    """
    if not anchors or not ops:
        return ops
    newest = [o for o in ops if o.get("kind") == "box" and not o.get("container")]
    if not newest:
        return ops
    tgt = newest[0]
    for _op_id, rect in anchors[:2]:
        ax, ay, aw, ah = rect
        # leave the existing element from the side facing the new node, so the line does not cross it
        from_x = ax + aw if (tgt.get("x", 0) >= ax) else ax
        ops.append({"kind": "arrow",
                    "from": [from_x, ay + ah / 2],
                    "to": [tgt.get("x", 0) + (0 if from_x <= ax else tgt.get("w", 160)) if False
                           else tgt.get("x", 0), tgt.get("y", 0) + tgt.get("h", 60) / 2],
                    "width": 2.0})
    return ops


def render_sketch(spec: dict, board: "Whiteboard",
                  repertoire: Optional[list] = None) -> list[dict]:
    """Turn a typed sketch spec into concrete draw ops, laid out deterministically.

    spec = {"type": one of SKETCH_TYPES, "point": "<what this shows>",
            "nodes": [{"label": str, "kind"?: "risk|control|impact", "quadrant"?: 1..4}, …],
            "edges": [[from_index, to_index, "label"?], …]}

    Returns [] when the spec has no stated point or fewer than 2 nodes — a drawing with nothing to
    say is not worth the room's attention.
    """
    if not isinstance(spec, dict):
        return []
    point = str(spec.get("point") or "").strip()
    stype = str(spec.get("type") or "").strip().lower()
    rep = [g for g in (repertoire or []) if g in SKETCH_TYPES]

    # de-dupe labels while preserving order (the same label was drawn 3x in a real sketch)
    seen, nodes = set(), []
    for n in (spec.get("nodes") or []):
        lbl = _node_label(n)[:38]
        if not lbl or lbl.lower() in seen:
            continue
        seen.add(lbl.lower())
        nodes.append(n if isinstance(n, dict) else {"label": lbl})
        nodes[-1]["label"] = lbl
    anns = render_annotations(spec.get("annotations"), board)
    # EXTENSION anchors: an edge may name an element already on the board rather than a new node index.
    # Without this the two rules I wrote collided - the sketch prompt asks for "AT MOST 3 new nodes" when
    # the board is in use, while the renderer refused anything with "fewer than 2 nodes", so a single box
    # attached to an existing element (a perfectly ordinary whiteboard move) was thrown away. Measured:
    # 3 of 4 sketch calls in one live run produced nothing.
    anchors: list = []
    for e in (spec.get("edges") or []):
        if not isinstance(e, dict):
            continue
        for key in ("from_label", "to_label"):
            hit = resolve_board_label(board, e.get(key) or "")
            if hit and hit[0] not in [a[0] for a in anchors]:
                anchors.append(hit)
    # The author chooses the shape. A missing or out-of-repertoire choice is a FAILURE, surfaced — a
    # substituted default would hide the fact that nobody decided how to make the point. (Annotations
    # are exempt: marking up what is already there is not a diagram.)
    endpoints = len(nodes) + len(anchors)
    if endpoints >= 2:
        if stype not in SKETCH_TYPES:
            print(f"[whiteboard] refusing to draw: no valid shape chosen (got {stype!r})")
            return anns
        if rep and stype not in rep:
            print(f"[whiteboard] refusing to draw: {stype!r} is outside this member's repertoire {rep}")
            return anns
    if endpoints < 2 or not nodes:
        # Annotating what is already on the board is a complete contribution on its own — it does not
        # need a fresh diagram wrapped round it.
        return anns
    if not point:
        return anns
    # Never draw a point the board already makes — for ANY author. The operator's complaint that
    # "the whiteboard repeats the same thing" was the chair redrawing its map, but two members can
    # just as easily sketch the same claim twice.
    norm = " ".join(point.lower().split())
    subj = point_subject(point)
    for o in board.live_ops():
        if o.kind != "text":
            continue
        raw_existing = str((o.data or {}).get("text") or "")
        existing = " ".join(raw_existing.lower().split())
        if existing and (existing == norm or existing.startswith(norm[:48])
                         or norm.startswith(existing[:48])):
            return []
        # …and reject a PARAPHRASE of a claim already on the board
        other = point_subject(raw_existing)
        if subj and other:
            overlap = len(subj & other) / max(1, min(len(subj), len(other)))
            if overlap >= 0.6:
                return []
    nodes = nodes[:7]

    y0 = next_free_y(board)
    trailing_annotations = anns
    ops: list[dict] = [{"kind": "text", "x": 40, "y": y0,
                        "text": fit_label(point, per_line=64, max_lines=2), "size": 18}]
    top = y0 + 40
    placed: list[dict] = []          # rects in draw order, so edges can bind to real boxes

    def box(x, y, label, w=_BOX_W, h=_BOX_H, node=None):
        # W3 visual weight: what matters is drawn bigger. Every node used to be the same size, so a
        # catastrophic impact and a passing remark carried identical visual weight.
        _k = str((node or {}).get("kind") or "").strip().lower() or infer_kind(label)
        _imp = str((node or {}).get("importance") or "").strip().lower()
        if _imp == "high" or _k in ("impact", "risk", "threat", "failure"):
            w, h = int(w * 1.18), int(h * 1.12)
        elif _imp == "low":
            w = int(w * 0.86)
        lbl = fit_label(label, per_line=max(12, int(w / 9.5)), max_lines=3)
        h = max(h, 30 + 18 * max(1, lbl.count("\n") + 1))       # grow the box to fit its label
        r = {"kind": "box", "x": x, "y": y, "w": w, "h": h, "label": lbl}
        kind = str((node or {}).get("kind") or "").strip().lower()
        if kind not in NODE_COLORS:
            kind = infer_kind(label)          # the model rarely sets it; the label usually says
        if kind in NODE_COLORS:
            r["color"] = NODE_COLORS[kind]                      # red for danger, green for control…
        if kind:
            # SHAPE, not just colour. `kind` has been declared (or inferred from the label) all along and
            # only ever changed the colour — a datastore, an actor and a risk were three identical
            # rectangles in three different colours. A silhouette is what makes a board readable at a
            # glance, and it costs nothing on the model side because the data is already here.
            r["shape"] = NODE_SHAPES.get(kind, "rect")
        ops.append(r)
        placed.append(r)
        how = str((node or {}).get("emphasis") or "").strip().lower()
        if how in EMPHASIS:
            ops.extend(_emphasis_ops(r, how, r.get("color", "#e03131")))
        return r

    if stype == "matrix":
        # 2x2 with axes labelled — items land in their quadrant (1=TL 2=TR 3=BL 4=BR)
        ops.append({"kind": "text", "x": 40, "y": top, "text": "impact ↑ / likelihood →", "size": 13})
        gx, gy = 90, top + 26
        for qi in range(4):
            cx = gx + (qi % 2) * (_BOX_W + _GAP_X)
            cy = gy + (qi // 2) * (_BOX_H + _GAP_Y)
            items = [n for n in nodes if int(n.get("quadrant", 0) or 0) == qi + 1]
            box(cx, cy, "\n".join(_node_label(i) for i in items) or "—")
        return _link_anchors(ops, anchors)

    if stype == "boundary":
        inside = [n for n in nodes if n.get("side", "in") != "out"]
        outside = [n for n in nodes if n.get("side") == "out"]
        inner_w = max(1, len(inside)) * (_BOX_W + _GAP_X) + 40
        cont = {"kind": "box", "x": 60, "y": top, "w": inner_w, "h": _BOX_H + 90,
                "label": str(spec.get("boundary") or "trust boundary"),
                "container": True}   # encloses its nodes ON PURPOSE (not an overlap defect)
        ops.append(cont)
        for i, n in enumerate(inside):
            box(100 + i * (_BOX_W + _GAP_X), top + 56, _node_label(n))
        oy = top + _BOX_H + 130
        for i, n in enumerate(outside):
            r = box(100 + i * (_BOX_W + _GAP_X), oy, _node_label(n))
            # the POINT of a boundary diagram: something crosses the line
            ops.append({"kind": "arrow", "from": [r["x"] + _BOX_W / 2, r["y"]],
                        "to": [r["x"] + _BOX_W / 2, top + _BOX_H + 56]})
        return _link_anchors(ops, anchors)

    if stype == "decision":
        verdict = _node_label(nodes[0])
        box(60, top, verdict, w=260, h=_BOX_H)
        for i, n in enumerate(nodes[1:]):
            r = box(400, top + i * (_BOX_H + 18), _node_label(n), w=300, h=_BOX_H)
            ops.append({"kind": "arrow", "from": [320, top + _BOX_H / 2],
                        "to": [r["x"], r["y"] + r["h"] / 2]})
        return _link_anchors(ops, anchors)

    if stype == "bowtie":
        # threat(s) -> top event -> consequence(s), with barriers on each leg. The standard risk
        # picture, and it says something a flowchart cannot: where the controls sit.
        mid = len(nodes) // 2
        threats = [n for n in nodes[:mid]] or nodes[:1]
        consequences = [n for n in nodes[mid + 1:]] or nodes[-1:]
        top_event = nodes[mid] if len(nodes) > 2 else {"label": "top event"}
        centre = box(430, top + 70, _node_label(top_event), w=210, node={"kind": "risk"})
        for i, n in enumerate(threats):
            r = box(60, top + i * (_BOX_H + 28), _node_label(n), w=190, node=n)
            bx = (r["x"] + r["w"] + centre["x"]) / 2
            ops.append({"kind": "arrow", "a": i, "b": -1,
                        "points": [[r["x"] + r["w"], r["y"] + r["h"] / 2], [bx, r["y"] + r["h"] / 2],
                                   [bx, centre["y"] + centre["h"] / 2],
                                   [centre["x"], centre["y"] + centre["h"] / 2]]})
            ops.append({"kind": "stroke", "color": "#2f9e44", "width": 3.0,
                        "points": [[bx, r["y"] + 6], [bx, r["y"] + r["h"] - 6]]})   # the barrier
        for i, n in enumerate(consequences):
            r = box(760, top + i * (_BOX_H + 28), _node_label(n), w=190, node={"kind": "impact"})
            bx = (centre["x"] + centre["w"] + r["x"]) / 2
            ops.append({"kind": "arrow", "a": -1, "b": i,
                        "points": [[centre["x"] + centre["w"], centre["y"] + centre["h"] / 2],
                                   [bx, centre["y"] + centre["h"] / 2], [bx, r["y"] + r["h"] / 2],
                                   [r["x"], r["y"] + r["h"] / 2]]})
            ops.append({"kind": "stroke", "color": "#2f9e44", "width": 3.0,
                        "points": [[bx, r["y"] + 6], [bx, r["y"] + r["h"] - 6]]})
        return _link_anchors(ops, anchors)

    if stype == "swimlane":
        # who does what: each `lane` is a horizontal band, steps ordered left to right within it.
        lanes: dict = {}
        for n in nodes:
            lanes.setdefault(str(n.get("lane") or "unassigned"), []).append(n)
        y = top
        for lane, items in lanes.items():
            ops.append({"kind": "text", "x": 40, "y": y + 6, "size": 14, "text": lane[:26]})
            for i, n in enumerate(items):
                box(210 + i * (_BOX_W + 40), y, _node_label(n), node=n)
            y += _BOX_H + 54
            ops.append({"kind": "stroke", "color": "#adb5bd", "width": 1.6,
                        "points": [[40, y - 22], [40 + 260 + len(items) * (_BOX_W + 40), y - 22]]})
        return _link_anchors(ops, anchors)

    if stype == "timeline":
        # when things happen, on one axis — the shape for sequencing and deadlines.
        n_it = len(nodes)
        x_end = 90 + n_it * 210
        axis_y = top + 78
        ops.append({"kind": "stroke", "color": "#495057", "width": 2.4,
                    "points": [[60, axis_y], [x_end, axis_y]]})
        for i, n in enumerate(nodes):
            x = 90 + i * 210
            ops.append({"kind": "stroke", "color": "#495057", "width": 2.0,
                        "points": [[x + 60, axis_y - 9], [x + 60, axis_y + 9]]})   # a tick
            box(x, axis_y + 22 if i % 2 else top, _node_label(n), w=180, node=n)
        return _link_anchors(ops, anchors)

    # flow / failure — a layered graph: layers left-to-right, crossings minimised, orthogonal routes
    edges_in = spec.get("edges")
    pairs: list = []
    if isinstance(edges_in, list) and edges_in:
        for e in edges_in:
            try:
                if isinstance(e, dict):
                    a, b, lbl = int(e.get("from")), int(e.get("to")), str(e.get("label") or "")
                else:
                    a, b = int(e[0]), int(e[1])
                    lbl = str(e[2]) if len(e) > 2 else ""
                if 0 <= a < len(nodes) and 0 <= b < len(nodes) and a != b:
                    pairs.append((a, b, lbl))
            except (TypeError, ValueError, IndexError):
                continue
    if not pairs:
        pairs = [(i, i + 1, "") for i in range(len(nodes) - 1)]   # a chain is the default meaning

    positions, routes = layered_layout(len(nodes), pairs, y0=top)
    for i, n in enumerate(nodes):
        nd = dict(n)
        # the POINT of a failure diagram is where it ends up — circle it if nothing else is marked
        if (i == len(nodes) - 1 and stype in ("failure", "flow")
                and infer_kind(_node_label(n)) == "risk"
                and not any(str(x.get("emphasis", "")).lower() in EMPHASIS for x in nodes)):
            nd.setdefault("emphasis", "circle")
            nd.setdefault("kind", "impact")
        x, y = positions[i]
        box(x, y, _node_label(n), node=nd)
    for (a, b, lbl), route in zip(pairs, routes):
        op = {"kind": "arrow", "a": a, "b": b,
              "points": [[round(p[0], 1), round(p[1], 1)] for p in route]}
        # W5 arrow semantics: optional/async reads dashed, unreliable reads wavy, and a route into an
        # impact node is drawn heavier. "Optional" was previously unsayable.
        _lbl = (lbl or "").lower()
        if any(t in _lbl for t in ("optional", "async", "maybe", "fallback", "if ")):
            op["style"] = "dashed"
        elif any(t in _lbl for t in ("unreliable", "flaky", "best effort", "eventual")):
            op["style"] = "wavy"
        if infer_kind(_node_label(nodes[b])) in ("risk", "impact"):
            op["width"] = 3.0
        if lbl:
            op["label"] = lbl[:24]
        ops.append(op)
    return _link_anchors(ops, anchors)
