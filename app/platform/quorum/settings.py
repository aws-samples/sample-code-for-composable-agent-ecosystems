"""Quorum RUN SETTINGS — one object, resolved at launch, recorded with the run.

Everything that governs how a quorum behaves used to be a literal in Python: thresholds, retry
policy, drawing triggers, token budgets. That made three things impossible, all of which we hit:
experimenting (a change meant editing code), comparing two runs (nobody knew what either was
configured to do), and attributing an outcome to a setting (the Attempt 14 deadlock was two
individually-correct constraints meeting at "nobody may speak", and nothing recorded that both were
on).

Design rules (docs: quorum/index.html §12):
  * DEFAULTS reproduce today's behaviour exactly, so adding this changes nothing.
  * Nothing behavioural stays hidden — if a value changes how the room acts, it belongs here.
  * The RESOLVED settings are recorded, not the overrides: defaults will move, and a run that stored
    only "what I changed" becomes uninterpretable later.
  * Fail closed. An unknown key or out-of-range value is refused at launch rather than silently
    defaulted, because fallbacks hide failures.
  * Config is not prompt. Prompt text is out of scope here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Optional

# --- the full surface, with today's behaviour as the default -----------------------------
DEFAULTS: dict = {
    "room": {
        "chair_mode": "facilitator",        # facilitator | moderator | none
        "agenda_ownership": "emergent",     # emergent | chaired | preset
        "preset_agenda": [],
        # DEFAULT CHANGED (E1): a deterministic allocator, not a model call. Measured against a
        # contemporaneous control: model calls per turn 3.95 -> 1.95 and yields 7 -> 2 at unchanged
        # coverage, because the removed call was itself causing the waste — it nominated members with
        # no live item they were permitted to raise. Set "moderated" to restore the model call.
        "turn_allocation": "fairness_first",   # fairness_first | moderated | round_robin
        "human_role": "design_owner",       # design_owner | observer | chair
        "seat_proponent": False,            # the human owns the design in an interactive review
        "disposition_mix": {"supportive": 1, "balanced": 3, "critical": 2},
    },
    "personas": {
        # DEFAULT CHANGED (E5): the only prompt-side control in this project measured to work.
        # objection share 0.6 -> 0.9 and coverage 0.6 -> 0.9 across accommodating/balanced/contrarian,
        # and contrarian was also the CHEAPEST arm (2 faults, 1 yield, 11% wasted vs 4/4/38%) because
        # agreeable rooms drift into restating agreement, which the gates then kill. Costs 162 prompt
        # chars, leaving the member-turn prompt at ~4400 against a 4900 ceiling.
        "agreeableness": "contrarian",      # contrarian | balanced | accommodating
        "infer_whiteboard_affinity": True,
        "repertoire_from_discipline": True,
        "overrides": {},                    # persona_id -> {agreeableness, whiteboard_affinity, ...}
    },
    "gates": {
        # mode is the latency/quality dial: every rejection costs a whole extra model call
        "mode": "reject_and_regenerate",    # prevent | single_retry | reject_and_regenerate | warn_only | off
        "max_retries_per_turn": 1,
        "novelty": {"enabled": True, "threshold": 0.55, "window": 12},
        "saturation": {"remarks": 3, "member_cap": 2, "count_concessions": True},
        "answered_questions": {"enforce": True},
    },
    "floor": {
        "pause_on": "explicit_address",     # question_only | explicit_address | any_directed | never
        "pause_rate_limit_turns": 2,
        "grant": "immediate",               # immediate | end_of_turn
        "chair_offers_floor_every": 0,      # 0 = never
    },
    "whiteboard": {
        "enabled": False,                   # opt-in per ecosystem, as today
        "infer_affinity": True,
        "sketch_calls_per_turn": 1,
        "triggers": {"on_intent": True, "on_promise": True,
                     "on_worked_topic": "saturation",   # saturation | remarks:N | off
                     "chair_map": "on_change"},         # on_change | every_round | off
        # DEFAULT CHANGED (E6): `critique_kept` is 0 in every run ever recorded — the board's
        # self-review has never once produced a revision that audited better — and disabling it cut
        # turn p90 by 3.6s at unchanged coverage. The slowest call in the system, deleted on evidence.
        "critique": {"when": "off",                     # off | on_problems | always
                     "attach_image": "visual_only"},    # never | visual_only | always
        "node_cap": 7,
        "annotations": {"blocker": "circle", "settled": "underline"},
    },
    "verdict": {
        # The eco YAML already carries these two (the legacy `quorum.verdict:` block) and §12.2 lists
        # them as settings — omitting them here made fail-closed validation reject every existing
        # ecosystem. Free-form lists: the room's own vocabulary, not an enum we should police.
        "scale": [],
        "must_state": [],
        "invariants": {"open_blocker_blocks_clean": True, "dissent_forces_conditions": True},
    },
    "models": {
        # The model actually used must be RECORDED, or a run record omits its most consequential
        # variable and two runs on different models read as identical. "" resolves to the runtime's
        # QUORUM_MODEL_ID and the resolved value is written into _run_config.json.
        # DEFAULT CHANGED (E4b): the single largest effect measured in this project. Sonnet 5 in every
        # seat reached coverage 1.0 and raised `fraud abuse` in 3 of 3 runs — an issue missed in 6/6,
        # 12/15 and 3/4 runs under Sonnet 4.5, which had made the blind spot look structural. It is also
        # cheaper: gate faults 18 -> 1, yields 13 -> 1, wasted share 0.53 -> 0.16. Set "" to fall back to
        # the runtime's QUORUM_MODEL_ID.
        "default": "us.anthropic.claude-sonnet-5",
        # The chair/facilitator AND the room machinery it stands for (turn allocation, the verdict).
        # Pinning these is what keeps an experiment about the MEMBERS: if the judge or the turn
        # allocator changes between runs, the comparison measures them instead of the room.
        "chair": "",
        # "" = every member on `default`; a model id = every member on that; "balance" = spread the
        # seats across balance_pool at convene, for deliberate model diversity.
        "members": "",
        # Probed invokable in this account/region. `list-inference-profiles` reporting ACTIVE is NOT
        # enough: nova-premier lists as ACTIVE and refuses Converse as "Legacy", and gpt-5.6 rejects
        # the temperature field we always send. A live room died on turn three finding that out, hence
        # models_usable() below and a default pool that has actually been called.
        "balance_pool": [
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "us.amazon.nova-pro-v1:0",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        ],
        "per_seat": {},                     # persona_id -> model id; an explicit override wins
        "temperature": {"turn": 0.4, "sketch": 0.3, "verdict": 0.4},
        # turn=1200 is the budget the Sonnet 5 result was MEASURED at, not a tuned optimum, and it is
        # what a reasoning model needs to answer at all (DeepSeek R1 at 520 was truncated mid-thought).
        # Unexplained and worth its own experiment: the same larger budget made Sonnet 4.5 *worse* at
        # passing the gates (18 faults vs 5 at 520).
        "max_tokens": {"turn": 1200, "sketch": 700, "critique": 700, "verdict": 3000, "empanel": 8000},
        "latency_budget_per_turn_s": 20,    # over budget => drop OPTIONAL calls (critique, then sketch)
    },
    # A quality measure that can MOVE. Process metrics (dominance, yields, retries) say how the room
    # behaved, not whether it was any good, and the one outcome we had — the verdict grade — was
    # identical in all three arms of the first experiment, so it discriminated nothing. Supply the
    # issues a competent panel must surface for THIS decision and coverage becomes comparable across
    # configs. Deliberately per-run config, not platform content: the rubric belongs to the question
    # being asked, and it is recorded with the run so a score can be audited.
    "evaluation": {"expected_issues": []},
    "limits": {"max_rounds": 4, "converge_after_passes": 3},
}

# --- validation (fail closed) -----------------------------------------------------------
_ENUMS = {
    "room.chair_mode": ("facilitator", "moderator", "none"),
    "room.agenda_ownership": ("emergent", "chaired", "preset"),
    "room.turn_allocation": ("moderated", "round_robin", "fairness_first"),
    "room.human_role": ("design_owner", "observer", "chair"),
    "personas.agreeableness": ("contrarian", "balanced", "accommodating"),
    "gates.mode": ("prevent", "single_retry", "reject_and_regenerate", "warn_only", "off"),
    "floor.pause_on": ("question_only", "explicit_address", "any_directed", "never"),
    "floor.grant": ("immediate", "end_of_turn"),
    "whiteboard.critique.when": ("off", "on_problems", "always"),
    "whiteboard.critique.attach_image": ("never", "visual_only", "always"),
    "whiteboard.triggers.chair_map": ("on_change", "every_round", "off"),
}
_RANGES = {
    "gates.max_retries_per_turn": (0, 3),
    "gates.novelty.threshold": (0.0, 1.0),
    "gates.novelty.window": (1, 100),
    "gates.saturation.remarks": (1, 20),
    "gates.saturation.member_cap": (1, 20),
    "floor.pause_rate_limit_turns": (0, 20),
    "floor.chair_offers_floor_every": (0, 50),
    "whiteboard.sketch_calls_per_turn": (0, 5),
    "whiteboard.node_cap": (2, 12),
    "models.latency_budget_per_turn_s": (1, 600),
    "limits.max_rounds": (1, 20),
    "limits.converge_after_passes": (1, 20),
}
# keys whose sub-structure is free-form (persona ids, model ids, agenda text …)
_FREEFORM = ("personas.overrides", "models.per_seat", "models.balance_pool", "room.preset_agenda",
             "evaluation.expected_issues",
             "room.disposition_mix", "whiteboard.annotations",
             "verdict.scale", "verdict.must_state")


def _walk(d: dict, prefix: str = ""):
    for k, v in (d or {}).items():
        path = f"{prefix}.{k}" if prefix else k
        yield path, v
        if isinstance(v, dict) and path not in _FREEFORM:
            yield from _walk(v, path)


def validate(resolved: dict) -> list[str]:
    """Return a list of problems. Empty means the settings are usable."""
    errs: list[str] = []
    known = {p for p, _ in _walk(DEFAULTS)}
    for path, val in _walk(resolved):
        if path not in known:
            errs.append(f"unknown setting {path!r}")
            continue
        if path in _ENUMS and val not in _ENUMS[path]:
            errs.append(f"{path}={val!r} must be one of {_ENUMS[path]}")
        if path in _RANGES:
            lo, hi = _RANGES[path]
            if not isinstance(val, (int, float)) or isinstance(val, bool) or not (lo <= val <= hi):
                errs.append(f"{path}={val!r} must be a number in [{lo}, {hi}]")
        ref = DEFAULTS
        for part in path.split("."):
            ref = ref.get(part) if isinstance(ref, dict) else None
        if isinstance(ref, bool) and not isinstance(val, bool):
            errs.append(f"{path}={val!r} must be true or false")
    mods = resolved.get("models") or {}
    # A reasoning model spends the turn budget thinking before it answers. Measured: DeepSeek R1 at the
    # default 520 tokens was truncated mid-thought and produced NO usable turn — six sessions with zero
    # member turns. Refuse the combination at launch rather than convene a room that cannot speak.
    _turn_budget = int(((mods.get("max_tokens") or {}).get("turn") or 520))
    _reasoning = [m for m in configured_models(resolved) if "deepseek" in str(m).lower()]
    if _reasoning and _turn_budget < 1200:
        errs.append(f"models.max_tokens.turn={_turn_budget} is too small for a reasoning model "
                    f"({', '.join(_reasoning)}) — it spends the budget thinking and produces no turn. "
                    f"Use 1200 or more.")
    if mods.get("members") == "balance" and not (mods.get("balance_pool") or []):
        errs.append("models.members='balance' needs a non-empty models.balance_pool")
    for key in ("default", "chair", "members"):
        if not isinstance(mods.get(key, ""), str):
            errs.append(f"models.{key} must be a model id string ('' = the runtime default)")
    trig = ((resolved.get("whiteboard") or {}).get("triggers") or {}).get("on_worked_topic")
    if trig is not None and not (trig in ("saturation", "off") or
                                 (isinstance(trig, str) and trig.startswith("remarks:"))):
        errs.append("whiteboard.triggers.on_worked_topic must be 'saturation', 'off' or 'remarks:N'")
    return errs


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in (
                "overrides", "per_seat", "disposition_mix", "annotations"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve(eco_config: Optional[dict] = None, overrides: Optional[dict] = None) -> dict:
    """DEFAULTS ⊕ the ecosystem's `quorum:` block ⊕ launch-time overrides."""
    q = ((eco_config or {}).get("quorum") or {})
    eco_settings = {k: v for k, v in q.items() if k in DEFAULTS}
    # the historical eco shape put a few values elsewhere; carry them into the new home
    legacy = {}
    delib = q.get("deliberation") or {}
    if "max_rounds" in delib:
        legacy.setdefault("limits", {})["max_rounds"] = delib["max_rounds"]
    if "turn_policy" in delib:
        legacy.setdefault("room", {})["turn_allocation"] = delib["turn_policy"]
    return _merge(_merge(_merge(DEFAULTS, legacy), eco_settings), overrides or {})


def fingerprint(resolved: dict) -> str:
    """Stable short hash, so two runs can be compared at a glance."""
    blob = json.dumps(resolved, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def changed_from_defaults(resolved: dict) -> dict:
    """Only the values that differ from DEFAULTS — for the launch summary and the run record."""
    out: dict = {}
    for path, val in _walk(resolved):
        if isinstance(val, dict) and path not in _FREEFORM:
            continue
        ref: Any = DEFAULTS
        for part in path.split("."):
            ref = ref.get(part) if isinstance(ref, dict) else None
        if ref != val:
            out[path] = val
    return out


def record(resolved: dict, overrides: Optional[dict] = None, code_version: str = "") -> dict:
    """The `_run_config.json` payload: the resolved settings plus what makes it comparable."""
    return {
        "config_hash": fingerprint(resolved),
        "code_version": code_version,
        "overrides_at_launch": overrides or {},
        "changed_from_defaults": changed_from_defaults(resolved),
        "settings": resolved,
    }


def configured_models(resolved: dict) -> list[str]:
    """Every model id this configuration could actually call, so they can be checked BEFORE the room
    convenes rather than when a member's turn comes round."""
    m = resolved.get("models") or {}
    ids = {str(m.get("default") or ""), str(m.get("chair") or "")}
    members = str(m.get("members") or "")
    if members == "balance":
        ids |= {str(x) for x in (m.get("balance_pool") or [])}
    elif members:
        ids.add(members)
    ids |= {str(v) for v in (m.get("per_seat") or {}).values()}
    return sorted(i for i in ids if i.strip())


def models_usable(ids: list[str], probe) -> list[str]:
    """Return a problem per model that cannot be invoked. `probe(model_id)` must raise on failure.

    Deliberately NOT a fallback: an unusable model is refused at launch with the provider's own reason,
    rather than substituted for something that happens to work. A live balanced room otherwise convenes
    a full panel and then dies mid-meeting with the seat's first turn — measured, twice.
    """
    errs = []
    for mid in ids:
        try:
            probe(mid)
        except Exception as e:                      # noqa: BLE001 - the provider's reason is the value
            reason = str(getattr(e, "response", {}).get("Error", {}).get("Message", "") or e)
            errs.append(f"model {mid!r} cannot be used: {reason[:160]}")
    return errs
