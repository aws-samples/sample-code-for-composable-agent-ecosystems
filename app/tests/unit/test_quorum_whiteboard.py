"""Unit tests for the Quorum Whiteboard (docs/spec-quorum-whiteboard.md) — stages 0 & 1:
persona affinity, policy extraction/enablement (fail-closed), per-eco affinity overrides, and
the append-only board op-log incl. erase-by-consent (rule 3) and the verdict digest (rule 6)."""
import os

import pytest

from tessera_platform.quorum.models import Persona
from tessera_platform.quorum.engine import quorum_policy
from tessera_platform.quorum import whiteboard as wb


# ---- persona affinity (rules 1,2) -----------------------------------------------------
def test_persona_affinity_defaults_none():
    p = Persona.from_record({"persona_id": "risk-analyst", "name": "Risk"})
    assert p.whiteboard_affinity == "none"


def test_persona_role_disposition_parsed():
    p = Persona.from_record({"persona_id": "chair", "name": "Chair", "role": "Facilitator",
                             "disposition": "Supportive"})
    assert p.role == "facilitator" and p.disposition == "supportive"
    # american spelling normalizes; unknown role -> challenger
    assert Persona.from_record({"persona_id": "s", "name": "S", "role": "synthesizer"}).role == "synthesiser"
    assert Persona.from_record({"persona_id": "x", "name": "X", "role": "wizard"}).role == "challenger"
    assert Persona.from_record({"persona_id": "d", "name": "D"}).role == "challenger"  # default


def test_quorum_plan_meeting_frame_default():
    from tessera_platform.quorum.models import QuorumPlan
    assert QuorumPlan().meeting_frame == ""
    assert QuorumPlan(meeting_frame="Cloud ARB").to_dict()["meeting_frame"] == "Cloud ARB"


def test_persona_affinity_parsed_and_normalized():
    p = Persona.from_record({"persona_id": "arch", "name": "Arch", "whiteboard_affinity": "HIGH"})
    assert p.whiteboard_affinity == "high"
    bad = Persona.from_record({"persona_id": "x", "name": "X", "whiteboard_affinity": "sometimes"})
    assert bad.whiteboard_affinity == "none"  # unknown -> none (fail-safe)


# ---- policy extraction + enablement (fail-closed) -------------------------------------
def test_quorum_policy_extracts_whiteboard_block():
    cfg = {"name": "e", "quorum": {"whiteboard": {"enabled": True,
            "persona_affinity": {"arch": "high"}}}}
    pol = quorum_policy(cfg)
    assert pol["whiteboard"]["enabled"] is True
    assert pol["whiteboard"]["persona_affinity"]["arch"] == "high"


def test_quorum_policy_whiteboard_defaults_empty():
    assert quorum_policy({"name": "e"})["whiteboard"] == {}


def test_whiteboard_enabled_requires_env_and_optin(monkeypatch):
    pol = {"whiteboard": {"enabled": True}}
    monkeypatch.delenv("WHITEBOARD_ENABLED", raising=False)
    assert wb.whiteboard_enabled(pol) is False          # env off -> inert
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    assert wb.whiteboard_enabled(pol) is True
    assert wb.whiteboard_enabled({"whiteboard": {"enabled": False}}) is False  # opt-in off
    assert wb.whiteboard_enabled({}) is False


def test_apply_affinity_overrides_in_place():
    by_id = {"arch": Persona.from_record({"persona_id": "arch", "name": "A"}),
             "risk": Persona.from_record({"persona_id": "risk", "name": "R", "whiteboard_affinity": "low"})}
    wb.apply_affinity(by_id, {"whiteboard": {"persona_affinity": {"arch": "high", "risk": "none"}}})
    assert by_id["arch"].whiteboard_affinity == "high"
    assert by_id["risk"].whiteboard_affinity == "none"


def test_apply_affinity_noop_without_overrides():
    p = Persona.from_record({"persona_id": "arch", "name": "A", "whiteboard_affinity": "medium"})
    wb.apply_affinity({"arch": p}, {})
    assert p.whiteboard_affinity == "medium"  # catalog default stands


def test_infer_affinity_from_persona_nature_when_enabled():
    arch = Persona.from_record({"persona_id": "p1", "name": "Solution Architect", "stance": "systems design"})
    risk = Persona.from_record({"persona_id": "p2", "name": "Risk Analyst", "stance": "narrative risk"})
    pol = {"whiteboard": {"enabled": True}}
    wb.apply_affinity({"p1": arch, "p2": risk}, pol)
    assert arch.whiteboard_affinity == "high"   # architect -> draws
    assert risk.whiteboard_affinity == "none"   # narrative -> doesn't


def test_no_inference_when_disabled():
    arch = Persona.from_record({"persona_id": "p1", "name": "Solution Architect"})
    wb.apply_affinity({"p1": arch}, {"whiteboard": {"enabled": False}})
    assert arch.whiteboard_affinity == "none"   # board off -> no inference


def test_override_beats_inference():
    arch = Persona.from_record({"persona_id": "p1", "name": "Solution Architect"})
    wb.apply_affinity({"p1": arch}, {"whiteboard": {"enabled": True, "persona_affinity": {"p1": "none"}}})
    assert arch.whiteboard_affinity == "none"   # explicit override wins over inference


# ---- policy validation (save-ecosystem, fail-closed) ----------------------------------
def test_validate_policy_ok():
    assert wb.validate_policy({"enabled": True, "persona_affinity": {"a": "high"}}) == []
    assert wb.validate_policy(None) == []


def test_validate_policy_rejects_bad_values():
    assert wb.validate_policy({"enabled": "yes"})            # not a bool
    assert wb.validate_policy({"persona_affinity": {"a": "sometimes"}})
    assert wb.validate_policy({"persona_affinity": []})      # not a mapping


# ---- board op-log (stage 1) -----------------------------------------------------------
def test_add_op_and_live():
    board = wb.Whiteboard()
    op = board.add_op("arch", "box", {"x": 0, "y": 0, "w": 10, "h": 10, "label": "Chair"}, refs=["e1"])
    assert op.op_id == "wb-1" and op.author == "arch" and op.refs == ["e1"]
    assert len(board.live_ops()) == 1


def test_add_op_rejects_unknown_kind():
    with pytest.raises(ValueError):
        wb.Whiteboard().add_op("arch", "hologram", {})


def test_erase_requires_consent_and_not_own(monkeypatch):
    board = wb.Whiteboard()
    a = board.add_op("arch", "box", {"label": "framing"})
    # own work: no request needed (returns None), never enters pending
    assert board.request_erase(a.op_id, by="arch") is None
    assert board.pending_erase == []
    # another author must ask; erase is NOT applied by the request
    req = board.request_erase(a.op_id, by="challenger", reason="misleading")
    assert req and req["owner"] == "arch" and len(board.pending_erase) == 1
    assert a.op_id not in board.erased
    # consent applies it
    assert board.apply_erase(a.op_id, approved_by="arch") is True
    assert a.op_id in board.erased and board.pending_erase == []
    assert board.live_ops() == []


def test_digest_describes_live_board_for_verdict():
    board = wb.Whiteboard()
    board.add_op("chair", "box", {"label": "Chair"})
    board.add_op("human", "box", {"label": "3rd-party vendor"})
    arrow = board.add_op("chair", "arrow", {"from": [0, 0], "to": [1, 1]})
    d = board.digest()
    assert "board:" in d and "Chair" in d and "3rd-party vendor" in d
    board.apply_erase(arrow.op_id, approved_by="chair")  # erased ops drop out of the digest
    assert "arrow" not in board.digest()


def test_roundtrip_replay_preserves_ops_and_counter():
    board = wb.Whiteboard()
    board.add_op("arch", "box", {"label": "A"})
    board.add_op("arch", "arrow", {"from": [0, 0], "to": [2, 2]})
    restored = wb.Whiteboard.from_dict(board.to_dict())
    assert len(restored.ops) == 2
    # new ids continue the sequence (no collision after replay)
    assert restored.add_op("arch", "text", {"text": "hi"}).op_id == "wb-3"


# ---- stage 2: session turn-block handling (rules 1,2,3,5) ------------------------------
from tessera_platform.quorum.session import SessionState, _handle_whiteboard


def _mk_state(affinity, enabled=True, monkeypatch=None):
    st = SessionState(session_id="s1")
    st.policy = {"whiteboard": {"enabled": enabled}}
    st.personas = {"arch": Persona.from_record(
        {"persona_id": "arch", "name": "Arch", "whiteboard_affinity": affinity})}
    return st


def _events(fn):
    out = []
    fn(out.append)
    return out


def test_none_affinity_never_draws(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("none")
    raw = {"whiteboard": {"why": "clearer", "draw": [{"kind": "box", "x": 0, "y": 0, "w": 5, "h": 5}]}}
    evs = _events(lambda emit: _handle_whiteboard(st, "arch", raw, None, emit))
    assert evs == [] and st.whiteboard.ops == []


def test_disabled_env_is_inert(monkeypatch):
    monkeypatch.delenv("WHITEBOARD_ENABLED", raising=False)
    st = _mk_state("high")
    raw = {"whiteboard": {"why": "clearer", "draw": [{"kind": "box"}]}}
    evs = _events(lambda emit: _handle_whiteboard(st, "arch", raw, None, emit))
    assert evs == [] and st.whiteboard.ops == []


def test_high_affinity_draws_and_emits_opened_then_op(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("high")
    raw = {"whiteboard": {"why": "structure is clearer drawn",
                          "draw": [{"kind": "box", "x": 0, "y": 0, "w": 5, "h": 5, "label": "Chair"}]}}
    evs = _events(lambda emit: _handle_whiteboard(st, "arch", raw, None, emit))
    types = [e["type"] for e in evs]
    assert types == ["WHITEBOARD_OPENED", "WHITEBOARD_OP"]
    assert len(st.whiteboard.ops) == 1 and st.whiteboard.ops[0].data["label"] == "Chair"


def test_draw_requires_why(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("high")
    raw = {"whiteboard": {"draw": [{"kind": "box"}]}}   # no `why` -> need-gate blocks it
    evs = _events(lambda emit: _handle_whiteboard(st, "arch", raw, None, emit))
    assert evs == [] and st.whiteboard.ops == []


def test_low_affinity_budget_is_one(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("low")
    raw = {"whiteboard": {"why": "one visual", "draw": [
        {"kind": "box", "label": "a"}, {"kind": "box", "label": "b"}]}}
    _events(lambda emit: _handle_whiteboard(st, "arch", raw, None, emit))
    assert len(st.whiteboard.ops) == 1   # budget=1 for low affinity


def test_ask_submitter_and_erase_request(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("high")
    # seed an op by ANOTHER author so an erase request is valid
    st.whiteboard.add_op("chair", "box", {"label": "framing"})
    raw = {"whiteboard": {"ask_submitter": "sketch the vendor boundary",
                          "request_erase": {"op_id": "wb-1", "reason": "misleading"}}}
    evs = _events(lambda emit: _handle_whiteboard(st, "arch", raw, None, emit))
    types = [e["type"] for e in evs]
    assert "WHITEBOARD_REQUEST" in types and "WHITEBOARD_ERASE_REQUEST" in types
    assert st.whiteboard.ops[0].op_id not in st.whiteboard.erased   # NOT erased without consent
    assert len(st.whiteboard.pending_erase) == 1


# ---- submitter board actions (rules 4, 3-apply) ---------------------------------------
from tessera_platform.quorum.session import submitter_draw, resolve_erase, whiteboard_state_event


def test_submitter_draw_adds_human_ops(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("none")   # affinity is irrelevant for the human submitter
    evs = _events(lambda emit: submitter_draw(st, [{"kind": "box", "label": "vendor"}], emit))
    assert [e["type"] for e in evs] == ["WHITEBOARD_OPENED", "WHITEBOARD_OP"]
    assert st.whiteboard.ops[0].author == "human"


def test_submitter_consent_applies_erase(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("high")
    op = st.whiteboard.add_op("arch", "box", {"label": "x"})
    st.whiteboard.request_erase(op.op_id, by="challenger", reason="stale")
    evs = _events(lambda emit: resolve_erase(st, op.op_id, True, emit))
    assert [e["type"] for e in evs] == ["WHITEBOARD_ERASE"]
    assert op.op_id in st.whiteboard.erased and st.whiteboard.pending_erase == []


def test_submitter_deny_keeps_op(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("high")
    op = st.whiteboard.add_op("arch", "box", {"label": "x"})
    st.whiteboard.request_erase(op.op_id, by="challenger")
    evs = _events(lambda emit: resolve_erase(st, op.op_id, False, emit))
    assert [e["type"] for e in evs] == ["WHITEBOARD_ERASE_DENIED"]
    assert op.op_id not in st.whiteboard.erased and st.whiteboard.pending_erase == []


def test_whiteboard_state_event(monkeypatch):
    monkeypatch.setenv("WHITEBOARD_ENABLED", "true")
    st = _mk_state("high")
    st.whiteboard.add_op("arch", "box", {"label": "x"})
    evs = _events(lambda emit: whiteboard_state_event(st, emit))
    assert len(evs) == 1 and evs[0]["type"] == "WHITEBOARD_STATE" and len(evs[0]["ops"]) == 1
