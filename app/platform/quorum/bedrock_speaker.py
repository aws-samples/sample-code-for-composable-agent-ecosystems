"""BedrockSpeaker — production Speaker backed by Amazon Bedrock (Converse API).

Each Speaker method builds a grounded, persona-anchored prompt and asks the model for a
strict-JSON response. boto3/bedrock import is lazy so the rest of the quorum package stays
AWS-free for offline use. Robust to model chatter: JSON is extracted defensively and any
parse failure degrades to an empty contribution (never crashes the run).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from .models import Persona, QuorumPlan, Transcript

_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _extract_json(text: str):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


_STANCE = {
    "contrarian": ("STANCE — you do NOT concede easily. If a colleague's answer is thin, say why it is "
                   "thin and hold the point. Only accept a resolution when the mechanism is named.\n"),
    "accommodating": ("STANCE — you look for what WORKS. Accept a reasonable answer, say so plainly, and "
                      "move the room forward rather than pressing a point that has been addressed.\n"),
}


class BedrockSpeaker:
    def __init__(self, model_id: Optional[str] = None, region: Optional[str] = None):
        import boto3  # lazy
        from botocore.config import Config
        self.model_id = model_id or os.environ.get("QUORUM_MODEL_ID") or os.environ.get(
            "SYNTHESIZER_MODEL_ID", "anthropic.claude-sonnet-4-5-20250929-v1:0")
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        # EXPLICIT retries. A throttled or transient call currently fails the turn, and a failed turn
        # becomes a YIELD — so infrastructure noise would masquerade as a member having nothing to say,
        # contaminating the exact metric the experiments measure. Adaptive mode also spaces out calls
        # when several arms of an experiment run in parallel.
        self._brt = boto3.client(
            "bedrock-runtime", region_name=self.region,
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"},
                          read_timeout=120, connect_timeout=10))
        # PER-SEAT models (settings.models.per_seat). One model in every seat is a structural cause of
        # consensus collapse: the room can agree because it is one mind wearing six name badges. Set by
        # the session before a member's turn; empty means every seat shares self.model_id.
        self._per_seat: dict = {}
        self._seat: str = ""
        # Models that REJECT the temperature field. Newest-generation models (Claude Sonnet 5 / Opus 5,
        # GPT-5.6) return ValidationException for it, and sending it unconditionally silently excluded
        # every one of them from the experiment programme. Discovered by probing at launch, not guessed
        # from a hardcoded list, and recorded with the run.
        self._no_temperature: set = set()

    def probe(self, model_id: str) -> None:
        """Smallest possible real call, to prove a configured model can be invoked with OUR inference
        config. Raises the provider's own error otherwise. Two live failures motivated this: a model
        that lists as ACTIVE but refuses Converse as Legacy, and one that rejects the temperature field
        we always send — both of which killed a convened room on its third turn."""
        try:
            self._brt.converse(
                modelId=model_id, system=[{"text": "Reply with OK."}],
                messages=[{"role": "user", "content": [{"text": "ping"}]}],
                inferenceConfig={"maxTokens": 8, "temperature": 0.4},
            )
        except Exception as e:                  # noqa: BLE001 - inspect, then decide
            if "temperature" not in str(e).lower():
                raise
            # A capability difference, not a failure: retry WITHOUT temperature and remember. This is
            # not a fallback hiding a problem — the model is used exactly as configured, and the
            # discovered capability is recorded in _run_config.json.
            self._brt.converse(
                modelId=model_id, system=[{"text": "Reply with OK."}],
                messages=[{"role": "user", "content": [{"text": "ping"}]}],
                inferenceConfig={"maxTokens": 8},
            )
            self._no_temperature.add(model_id)
            print(f"[quorum.probe] {model_id} rejects temperature — omitting it for this model")

    def seat(self, persona_id: str) -> None:
        """Route the NEXT call to that seat's model, if one was configured for it."""
        self._seat = persona_id or ""

    @property
    def _model(self) -> str:
        return self._per_seat.get(self._seat) or self.model_id

    def _cfg(self, max_tokens: int, temperature: float) -> dict:
        cfg = {"maxTokens": max_tokens}
        if self._model not in self._no_temperature:
            cfg["temperature"] = temperature
        return cfg

    # --- low-level -------------------------------------------------------------------
    def _ask(self, system: str, user: str, max_tokens: int = 1200) -> str:
        resp = self._brt.converse(
            modelId=self._model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig=self._cfg(max_tokens, 0.4),
        )
        parts = resp.get("output", {}).get("message", {}).get("content", [])
        return "".join(p.get("text", "") for p in parts)

    def _ask_image(self, system: str, user: str, png: bytes, max_tokens: int = 700) -> str:
        """Same as _ask but with an image the model can actually look at (Bedrock converse image
        block). Used to show a member its own drawing before it is accepted."""
        resp = self._brt.converse(
            modelId=self._model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [
                {"image": {"format": "png", "source": {"bytes": png}}},
                {"text": user},
            ]}],
            inferenceConfig=self._cfg(max_tokens, 0.3),
        )
        parts = resp.get("output", {}).get("message", {}).get("content", [])
        return "".join(p.get("text", "") for p in parts)

    def _ask_stream(self, system: str, user: str, on_token, max_tokens: int = 600) -> str:
        """Stream the spoken prose token-by-token via on_token, but NEVER emit the '===META==='
        marker or any partial prefix of it (we hold back a small tail until we know it isn't the
        start of the marker). Returns the full accumulated text (prose + meta) for parsing."""
        MARKER = "===META==="
        resp = self._brt.converse_stream(
            modelId=self._model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig=self._cfg(max_tokens, 0.5),
        )

        def _held(s: str) -> int:  # length of the longest suffix of s that is a prefix of MARKER
            for k in range(min(len(s), len(MARKER) - 1), 0, -1):
                if s.endswith(MARKER[:k]):
                    return k
            return 0

        full, emitted, stopped = [], 0, False
        for ev in resp.get("stream", []):
            tok = ev.get("contentBlockDelta", {}).get("delta", {}).get("text")
            if not tok:
                continue
            full.append(tok)
            if stopped:
                continue
            joined = "".join(full)
            mi = joined.find(MARKER)
            if mi != -1:
                if mi > emitted:
                    try: on_token(joined[emitted:mi])
                    except Exception: pass
                emitted, stopped = mi, True
                continue
            safe = len(joined) - _held(joined)  # withhold a possible partial-marker tail
            if safe > emitted:
                try: on_token(joined[emitted:safe])
                except Exception: pass
                emitted = safe
        return "".join(full)

    @staticmethod
    def _persona_card(p: Persona) -> str:
        return (f"[{p.persona_id}] {p.name} — {p.stance}. dimensions={p.dimensions}; "
                f"invokes={p.invokes}; asks={p.key_questions}; voice={p.voice}; "
                f"severity_bias={p.severity_bias}")

    # --- Speaker protocol ------------------------------------------------------------
    def empanel(self, decision: str, context: str, pool: list[Persona], policy: dict,
                max_tokens: int = 8000) -> dict:
        cards = "\n".join(self._persona_card(p) for p in pool) or "(no exemplars — synthesize from scratch)"
        select = policy.get("select", {})
        lo, hi = select.get("min", 3), select.get("max", 6)
        sys = ("You are the CONVENER of a scrutiny quorum. FIRST, think about how a review of THIS kind of "
               "decision actually runs in real life — what that meeting is called, who is in the room, and how "
               "they behave — then assemble that room. Tailor it to the decision's ACTUAL stakes, stakeholders "
               "and domain, which may be corporate, public-sector, community or deeply PERSONAL. Fit the panel "
               "to the decision: do NOT force a regulatory/corporate lens onto a personal or family decision, "
               "nor a purely personal lens onto a regulated one. Use the EXEMPLAR LIBRARY only as inspiration — "
               "REUSE an exemplar (keep its exact persona_id) when it genuinely fits, otherwise SYNTHESIZE a new "
               "member native to the decision's world.\n"
               "A REAL ROOM IS BALANCED — NOT a pack of adversaries. Compose a MIX of dispositions so the review "
               "feels like a real meeting, not a pile-on:\n"
               "  • a FACILITATOR / CHAIR who runs the meeting — keeps time, synthesises the threads, and is the "
               "person who naturally 'puts the points on the board' and drives the room to a decision;\n"
               "  • a DESIGN OWNER / PROPONENT who DEFENDS the proposal, explains trade-offs, and pushes back on "
               "disproportionate demands (a real review has someone arguing FOR it);\n"
               "  • at least one SYNTHESISER / integrator who looks for what is SOUND and what can be built on;\n"
               "  • the domain CHALLENGERS / SMEs who pressure-test their lane.\n"
               "Spread 'disposition' across the spectrum — roughly one facilitator, one proponent, one "
               "synthesiser, and the rest challengers — so affirmation, defence and synthesis are present, not "
               "just critique. Set each member's 'whiteboard_affinity' by nature: facilitator/chair and "
               "architecture/design/systems members = 'high' or 'medium'; narrative, policy, legal, reputational "
               "and advocacy members = 'none'. EVERY seated member MUST have BOTH a plausible human first + last "
               "name AND their title in the 'name' field, 'Firstname Lastname — Title'. NEVER use a bare role "
               "label as the name. You MUST justify inclusions, and name only the perspectives whose ABSENCE is a real risk to this review — the platform records every unseated persona itself, so do not enumerate the library. "
               "Reply STRICT JSON only.")
        usr = (f"DECISION:\n{decision}\n\nCONTEXT:\n{context}\n\n"
               f"EXEMPLAR LIBRARY (optional — adapt, reuse by id, or ignore):\n{cards}\n\n"
               f"PANEL SIZE: between {lo} and {hi} members. must_include={select.get('must_include', [])}\n\n"
               'Reply JSON: {'
               '"meeting_frame":"<one sentence: what this review is called and how it runs in real life>",'
               '"seated":[{"persona_id":str_snake_case_id,"name":str,"stance":str,'
               '"role":"facilitator|proponent|synthesiser|challenger","disposition":"supportive|balanced|critical",'
               '"whiteboard_affinity":"none|low|medium|high",'
               '"dimensions":[str],"invokes":[str],"key_questions":[str],"voice":str,'
               '"severity_bias":"balanced|high|low"}, ...],'
               '"excluded":[{"persona_id":str,"reason":str}, ...],"justification":str}\n\n'
               "For 'excluded', list ONLY perspectives a reasonable observer might expect for THIS "
               "decision that you consciously left out — NOT the exemplar-library entries. "
               "Keep each seated persona TERSE so the whole panel fits in one response: stance <=20 "
               "words, dimensions <=4 short tags, invokes <=3, key_questions <=3, voice <=8 words.")
        # Budget, not a constant. Two things pushed the old hardcoded 4000 over the edge: Sonnet 5
        # returns a REASONING block alongside the text and both draw on maxTokens, and the persona pool
        # grew from 10 to 16 so there are more exclusions to justify. The response truncated mid-JSON and
        # the panel silently fell back to the pool — a whole run's composition decided by code.
        raw = self._ask(sys, usr, max_tokens=max_tokens)
        out = _extract_json(raw)
        if not (out or {}).get("seated") and not str(raw).rstrip().endswith(("}", "]")):
            # Truncated rather than refused: retry ONCE with more room. Not a fallback to something
            # else — the same question, asked with enough space to finish answering it.
            print(f"[quorum.empanel] response truncated at {max_tokens} tokens "
                  f"({len(str(raw))} chars) — retrying once with {max_tokens * 2}")
            raw = self._ask(sys, usr, max_tokens=max_tokens * 2)
            out = _extract_json(raw)
        if not isinstance(out, dict) or not out.get("seated"):
            print("[quorum.empanel] no parseable seated panel from convener (will fall back to pool)")
            return out if isinstance(out, dict) else {}
        return out

    def completeness(self, decision: str, context: str, seated: list[Persona], pool: list[Persona]) -> dict:
        seated_cards = "\n".join(self._persona_card(p) for p in seated)
        avail = [p.persona_id for p in pool if p not in seated]
        sys = ("You are the COMPLETENESS ADVERSARY. Your only job: name the perspective this "
               "panel is MISSING that a real examiner/commissioner would demand. Reply STRICT JSON only.")
        usr = (f"DECISION:\n{decision}\n\nSEATED PANEL:\n{seated_cards}\n\n"
               f"AVAILABLE TO ADD: {avail}\n\n"
               'Reply JSON: {"missing":[dimension,...],"add":[persona_id,...],"note":str}')
        return _extract_json(self._ask(sys, usr)) or {}

    def digest(self, decision: str, context: str) -> str:
        """ONE-TIME compact review brief of the submitted material. Turning a 17k-char document into
        a ~2k-char brief cuts ~4k input tokens off EVERY subsequent turn (turn latency was the main
        driver of the stalls), while keeping the substance panelists need to criticise."""
        if len(context or "") < 4000:
            return context or ""
        sys = ("You are the panel secretary. Compress the submitted material into a dense REVIEW BRIEF "
               "for expert reviewers: the concrete claims, numbers, thresholds, named services, stated "
               "risks and explicit gaps — the things a reviewer would interrogate. No preamble, no praise, "
               "no markdown headings. Max 350 words.")
        usr = f"DECISION UNDER REVIEW:\n{decision}\n\nSUBMITTED MATERIAL:\n{context[:60000]}"
        try:
            out = self._ask(sys, usr, max_tokens=700).strip()
            return out or context[:4000]
        except Exception as e:
            print(f"[quorum.digest] failed ({type(e).__name__}); falling back to truncated context")
            return context[:4000]

    def speak(self, persona: Persona, decision: str, context: str, view: list[dict], round: int,
              on_token=None, must_answer_human: bool = False, agenda: Optional[dict] = None,
              banned_openings: Optional[list] = None, opening_mode: str = "",
              human_question: str = "", asked_and_answered: Optional[list] = None,
              prior_answers: Optional[list] = None, lane_map: Optional[list] = None,
              chair_note: str = "", avoid_texts: Optional[list] = None,
              whiteboard: bool = False, board_digest: str = "", meeting_frame: str = "",
              agreeableness: str = "balanced", gates_mode: str = "reject_and_regenerate",
              allowed_topics: Optional[list] = None, max_tokens: int = 520) -> list[dict]:
        ag = agenda or {}
        raised = ", ".join(ag.get("raised") or []) or "(nothing yet)"
        # When the chair owns the agenda, the live item is GIVEN to the member. "Don't re-raise a
        # settled topic" then becomes an instruction up front instead of a rejection afterwards —
        # the same rule, at a third of the cost, because a rejected turn is regenerated.
        if ag.get("chair_owns"):
            _floor_item = ag.get("on_the_floor") or ""
            raised = (f'ON THE FLOOR (the chair has this item open — address THIS, or say plainly that '
                      f'it is finished): {_floor_item}' if _floor_item
                      else "ON THE FLOOR: nothing — the chair invites a NEW item from your lane")
        saturated = ", ".join(ag.get("saturated") or []) or "(none)"
        settled = ", ".join(ag.get("settled_by_human") or []) or "(nothing yet)"
        bans = "; ".join(f'"{b}"' for b in (banned_openings or [])) or "(none)"
        answered_qs = "\n".join(f"- {q}" for q in (asked_and_answered or [])) or "(none)"
        my_lane = ", ".join((persona.dimensions or [])[:5]) or (persona.stance or "")
        others = "\n".join(f"- {m['name']} owns: {m['lane']}" for m in (lane_map or [])
                           if m.get("id") != persona.persona_id) or "(unknown)"
        avoid = "\n".join(f"- {a[:200]}" for a in (avoid_texts or [])) or ""

        floor = ""
        if must_answer_human:
            floor = ("*** YOU HOLD THE FLOOR TO ANSWER THE SUBMITTER. ***\n"
                     f'They just said, verbatim: "{human_question}"\n'
                     "Your FIRST sentence must answer THAT, directly and concretely. If they asked what must "
                     "change / what you need to approve it, NAME the specific change(s) you require — a "
                     "mechanism, threshold or artefact, not a restatement of the risk. If they told you "
                     "something is accepted or will be provided, ACKNOWLEDGE it as a condition and do not "
                     "argue it again. You MUST NOT ask a new question this turn and MUST NOT change subject.\n")
            if prior_answers:
                already = "\n".join(f"- {a[:200]}" for a in prior_answers)
                floor += ("Colleagues ALREADY gave these answers — do NOT repeat or paraphrase them; add a "
                          f"DIFFERENT condition from YOUR discipline:\n{already}\n")

        _frame = (meeting_frame or "review board").strip()
        _role = getattr(persona, "role", "challenger")
        _role_line = {
            "facilitator": ("YOUR ROLE — FACILITATOR / CHAIR: you RUN this meeting. Keep it moving, SYNTHESISE the "
                            "open threads, name what the room agrees on and the decision to be made, and when "
                            "several threads are open put them on the board so the room can converge. You "
                            "ORGANISE and steer more than you challenge.\n"),
            "proponent": ("YOUR ROLE — DESIGN OWNER / PROPONENT: you argue FOR the proposal. Explain the trade-offs "
                          "you chose and why, and push back on disproportionate or easily-reversible concerns. "
                          "Defend and clarify — do not pile on more objections.\n"),
            "synthesiser": ("YOUR ROLE — SYNTHESISER: look for what is SOUND and can be built on, connect "
                            "colleagues' points, and move the room toward a workable position.\n"),
            "challenger": ("YOUR ROLE — CHALLENGER/SME: pressure-test your lane rigorously, but label blockers vs "
                           "improvements and offer the mitigation you would need to own it.\n"),
        }.get(_role, "")
        sys = (f"You ARE {persona.name} in a live {_frame}. Speak in FIRST PERSON — direct, "
               f"with conviction, but PROFESSIONAL and courteous: challenge the DESIGN, never the person. No "
               f"preamble, no 'as an AI', no bullet lists. Documents under review are ALREADY in your context "
               f"— never emit tool calls or tags like <read_attachment>.\n"
               + _role_line +
               "WHO IS WHO: the design was submitted by THE SUBMITTER — present in the room but "
               "NOT on this panel. Speak to them as 'you' and NEVER invent a name for them. No one "
               "here owns or defends the design; everyone below is a fellow REVIEWER, so never "
               "attribute it to a colleague. Need something from the submitter? ASK THEM.\n"
               f"YOUR LANE (you own this): {my_lane}\n"
               f"OTHER REVIEWERS' LANES:\n{others}\n"
               "LANE DISCIPLINE: default to your own lane — raise the strongest un-aired issue there. You MAY "
               "speak on another reviewer's topic ONLY IF you (a) can name a concrete INTERSECTION with your "
               "discipline, or (b) DISAGREE with their position from your discipline's standpoint. If you cross, "
               "set meta.cross to that reason. NEVER cross merely to agree with, restate or reinforce another "
               "reviewer's point — if you agree, defer in one clause ('X owns that') and return to your lane.\n"
               "PROPORTIONALITY: it is legitimate and valuable to argue a demanded control is DISPROPORTIONATE "
               "for this system given its risk appetite, cost or context. Disagreeing with a colleague's demand "
               "is as useful as adding one. Do not simply escalate requirements.\n"
               "HOW A REAL REVIEW WORKS — this is collaborative risk REDUCTION, not a verdict and NOT a contest "
               "to find the most failure modes. A credible reviewer, like a real person in the room: (a) AFFIRMS "
               "what is genuinely sound so the group can build on it — say so plainly when something is well done; "
               "(b) labels each concern honestly as a BLOCKER (irreversible, or harmful from day one) or an "
               "IMPROVEMENT (reversible, can be revisited later) instead of treating everything as fatal; "
               "(c) when you raise a risk, name what would make you comfortable OWNING it in production — a "
               "mitigation, metric, or check — not just the risk; (d) helps the room CONVERGE: if your point is "
               "already on the table, AGREE and deepen it, offer the fix, or move toward a decision, rather than "
               "restating it as a fresh objection. Agreeing, synthesising, affirming, or proposing a decision is a "
               "FULL contribution — not every turn must be an attack, and a room of pure critics is a failed review.\n"
               + floor +
               f"IDENTITY: remarks tagged member '{persona.persona_id}' are YOUR OWN — use 'I'. Each turn is "
               "COMPLETE: never say you were 'cut off', and never restart a point you already made.\n"
               f"ALREADY ASKED OF THE SUBMITTER AND ANSWERED — never ask any of these again, in any wording:\n{answered_qs}\n"
               # gates.mode: prevent — say what IS open instead of three prohibitions. The bans are
               # ignored and then enforced by regenerating the turn, which costs a whole model call;
               # a positive list makes the compliant turn the easy one. Same predicates, stated forward.
               + (f"AGENDA — you may speak to ONE of these OPEN items, or open a NEW issue from your "
                  f"lane if none fits: {', '.join(allowed_topics) or '(none yet — open the first)'}\n"
                  f"Everything else on the agenda is finished. Do not reopen it.\n"
                  if gates_mode == "prevent" else
                  f"AGENDA — live issues (do NOT re-raise, but you may rebut): {raised}\n"
                  f"AGENDA — EXHAUSTED, closed for debate (do not add to these): {saturated}\n"
                  f"AGENDA — settled by the submitter (treat as noted conditions; do NOT re-litigate): {settled}\n")
               # How hard this member pushes (settings.personas.agreeableness). Costs NOTHING at the
               # default: 'balanced' injects no text, so the measured prompt budget is unchanged and
               # only a deliberate experiment pays for the extra instruction.
               + (_STANCE.get(agreeableness, "") if agreeableness != "balanced" else "")
               + (f"{chair_note}\n" if chair_note else "")
               + (f"DO NOT REPEAT these remarks — say something substantively different:\n{avoid}\n" if avoid else "")
               + f"STYLE — {opening_mode or 'open naturally and vary your phrasing'}. Your first four words MUST "
               f"NOT resemble any of these recent openings: {bans}. Never begin with 'I'm going to', 'I'll', "
               "'I need to', 'I want to' or 'Look,'.\n"
               "YOUR TURN HAS TWO PARTS — this is how real meetings work:\n"
               "1) SPOKEN — what you actually say out loud, and nothing more. ONE or TWO sentences, HARD "
               "MAX 35 words. Real speech: contractions, plain words, a fragment is fine. NO lists, NO "
               "section numbers, NO strings of specifics — nobody recites paragraphs in a meeting. Make one "
               "point land, the way a person would say it across a table.\n"
               "2) DETAIL — the substance behind what you just said, for the record: the specific mechanism, "
               "threshold, clause, number or evidence you're relying on, and exactly what you require. "
               "40-120 words. Your colleagues read this and must engage with it; the submitter can expand it. "
               "Put the rigour HERE, not in what you say out loud.\n"
               "YIELD by leaving both parts blank if the review is genuinely exhausted. ASK THE "
               "SUBMITTER (type 'question', target 'human') whenever their answer would change your "
               "assessment — you are in the room with them and they can answer; keep it short and "
               "concrete. Do not re-ask what they have already answered.")
        # Whiteboard affordance (spec-quorum-whiteboard.md) — offered ONLY to a drawing-inclined
        # persona when the board is live. Need-based: most turns draw nothing.
        offer_wb = bool(whiteboard and getattr(persona, "whiteboard_affinity", "none") != "none")
        if offer_wb:
            _aff = getattr(persona, "whiteboard_affinity", "none")
            sys += (
                "\nWHITEBOARD: a shared board is in the room and you are the sort of person who uses "
                f"it (propensity '{_aff}'). You do NOT draw here \u2014 instead, when a diagram would "
                'genuinely help, add "sketch":"<what to draw, <=12 words>" to the META JSON and it '
                "will be drawn for you. Use it a few times across a review (map the architecture or "
                "flow, group the risks, lay out the go/no-go), and ALWAYS when you say out loud that "
                'you will sketch something. You may instead set "ask_submitter":"<what to show>" to '
                "invite the submitter to draw. Omit both when no visual is warranted.\n")
        usr = (f"THE DECISION UNDER REVIEW:\n{decision}\n\nREVIEW BRIEF:\n{context}\n\nHOW THE ROOM HAS GONE:\n"
               f"{json.dumps(view)[:3500]}\n\n"
               + (f"ON THE SHARED WHITEBOARD (engage with it if relevant):\n{board_digest}\n\n" if board_digest else "")
               + "Speak now. Format EXACTLY:\n"
               "<what you say OUT LOUD — max 35 words, or blank to yield>\n===DETAIL===\n"
               "<the substance: specifics + exactly what you require, 40-120 words>\n===META===\n"
               '{"type":"claim|objection|rebuttal|concession|question|dissent",'
               '"severity":"blocker|material|note","target":"<entry_id | human | null>",'
               '"topic":"<2-5 word label for the issue>",'
               '"cross":"<why this crosses into another lane, or null>",'
               '"resolve_topic":"<agenda topic the submitter has now settled, or null>"'
               + (',"sketch":"<what to draw, or omit>","ask_submitter":"<what to ask them to show, or omit>"'
                  if offer_wb else "")
               + "}")
        # Reasoning models spend the budget thinking before they answer: DeepSeek R1 at 520 tokens was
        # truncated mid-thought and produced no usable turn at all. The budget is configurable so an
        # experiment can hold it CONSTANT across arms rather than advantaging one family.
        raw = self._ask(sys, usr, max_tokens=max_tokens)
        # Reasoning models emit their scratchpad inline (and Bedrock returns it in a content block this
        # SDK reports as SDK_UNKNOWN_MEMBER). Remove any leaked <think> span before parsing: this is
        # format normalisation across vendors, not a fallback — a turn that still fails to parse is
        # still reported as no usable turn.
        raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I).strip()
        raw = re.sub(r"^</?think>\s*", "", raw, flags=re.I).strip()
        body, _, meta_s = raw.partition("===META===")
        spoken_raw, _, detail_raw = body.partition("===DETAIL===")
        spoken = re.sub(r"\s*=+\s*$", "", spoken_raw).strip()
        detail = re.sub(r"\s*=+\s*$", "", detail_raw).strip()
        if not spoken and not detail:
            return []  # the member yielded — nothing new to add
        if not spoken:
            # Model skipped the spoken part: use its first sentence so something human-sized is heard.
            first = re.split(r"(?<=[.!?])\s+", detail.strip())[0]
            spoken = first[:240]
        # Hard cap the spoken line — a person cannot deliver a paragraph in one breath.
        words = spoken.split()
        if len(words) > 45:
            cut = " ".join(words[:45])
            m = re.search(r"^(.*[.!?])", cut)
            spoken = (m.group(1) if m else cut.rstrip(",;:") + "…")
            detail = (detail + ("\n\n" if detail else "") + " ".join(words[45:]))[:2000]
        meta = _extract_json(meta_s) or {}
        etype = meta.get("type") if meta.get("type") in ("claim", "objection", "rebuttal", "concession", "question", "dissent") else "claim"
        sev = meta.get("severity") if meta.get("severity") in ("blocker", "material", "note") else "note"
        tgt = meta.get("target")
        if tgt in ("null", "", "none", "None"):
            tgt = None
        rt = meta.get("resolve_topic")
        if rt in ("null", "", "none", "None"):
            rt = None
        cross = meta.get("cross")
        if cross in ("null", "", "none", "None"):
            cross = None
        return [{"type": etype, "text": spoken[:400], "detail": detail[:2000], "target": tgt,
                 "severity": sev, "topic": meta.get("topic") or "", "resolve_topic": rt, "cross": cross,
                 # sketch INTENT only — geometry comes from the dedicated sketch() call (Fix 4)
                 "sketch": (str(meta.get("sketch")).strip()[:120] if meta.get("sketch") else None),
                 "ask_submitter": (str(meta.get("ask_submitter")).strip()[:200]
                                   if meta.get("ask_submitter") else None),
                 "whiteboard": meta.get("whiteboard") if isinstance(meta.get("whiteboard"), dict) else None}]

    def sketch(self, persona: Persona, purpose: str, decision: str, board_digest: str = "",
               agenda: Optional[dict] = None, inventory: Optional[list] = None,
               repertoire: Optional[list] = None) -> dict:
        """Describe a diagram — WHAT it shows, not where the pixels go.

        Two measured failures drove this contract. (1) When the model chose coordinates, every
        sketch started in the same corner because each call sees a blank canvas: 62 overlapping box
        pairs on one real board, and arrows that landed on nothing (0 of 4 in one sketch). (2) Asked
        for "boxes and arrows", it drew LISTS OF NOUNS — 'Monitoring Dashboard, Refund Rate Tracker,
        Decision Latency…' — decorative rectangles with no claim, and the same label three times.

        So the model now returns a typed spec with a REQUIRED point, and the server lays it out
        (whiteboard.render_sketch): overlap and dangling arrows become impossible, and a drawing
        that cannot state what it demonstrates is not drawn at all."""
        ag = agenda or {}
        shapes = "|".join(repertoire) if repertoire else "|".join(SKETCH_TYPES)
        # A person at a whiteboard does not draw a fresh diagram each time they speak: they add a box,
        # talk, add an arrow, come back later and extend it. The board arrives complete and unrelated
        # every time because each sketch call composes from scratch. So when the board ALREADY has
        # content, ask for an EXTENSION of at most three new elements tied to what is up there, and let
        # the layout engine place them beside their referents. State-triggered, not an option the model
        # may ignore: the instruction changes with the board.
        # (The board's contents are listed further down; this carries only the instruction.)
        grow = ("" if not inventory else
                "THE BOARD IS ALREADY IN USE — extend it, do not start again. Add AT MOST 3 new nodes, "
                "and tie them to what is already up there by naming the existing element in an edge's "
                "`from_label`. If nothing needs adding, annotate instead of drawing.\n")
        sys = (
            grow +
            "You sketch on a meeting whiteboard. A sketch must MAKE A POINT — it is an argument, not "
            "decoration. Never draw a list of nouns; draw a relationship: what flows where, what "
            "crosses a boundary, what breaks and what that breaks next, or what the decision hinges "
            "on.\n"
            f"YOU DRAW WITH: {shapes}. That is your repertoire, the way your discipline draws — pick "
            "the one that fits THIS topic. Do not use a shape outside it; a drawing with no shape "
            "chosen is not drawn at all.\n"
            "SHAPES: flow = a path A->B->C (shows WHERE something enters). failure = trigger -> "
            "propagation -> impact. boundary = inside vs outside a trust/data line, with what crosses "
            "it marked side:out. decision = node 0 is the verdict, the rest are its conditions. "
            "matrix = place items by quadrant (1=high impact/low likelihood, 2=high/high, 3=low/low, "
            "4=low/high). bowtie = threats -> the top event -> consequences, with barriers on each leg "
            "(shows WHERE the controls sit). swimlane = who does what, one `lane` per actor. "
            "timeline = when things happen, in order.\n"
            "Reply STRICT JSON only:\n"
            '{"point":"<the ONE thing this drawing proves, <=14 words>",'
            f'"type":"{shapes}",'
            '"boundary":"<only for type=boundary: what the line IS>",'
            '"nodes":[{"label":"<=4 words","kind":"risk|control|condition|store|service|actor|impact",'
            '"emphasis":"circle (only the ONE node that is the punchline; omit otherwise)",'
            '"side":"in|out (boundary only)","quadrant":1-4 (matrix only),'
            '"lane":"<who owns it, swimlane only>","importance":"high|low (optional)"}],'
            '"edges":[{"from":<node index>,"to":<node index>,"label":"<=3 words",'
            '"from_label":"<existing board element to attach to, when extending>"}],'
            '"annotations":[{"targets":["<board element id>"],'
            '"mark":"circle|strike|underline|note","text":"<=8 words"}]}\n'
            "ANNOTATE INSTEAD OF REDRAWING when the board already shows what you mean: circle a GROUP "
            "of existing elements to say these belong together or these are the problem, strike out "
            "what has been ruled out, underline what is now agreed, or attach a note. You may annotate "
            "ANY element including a colleague's — adding a mark needs no permission (removing one "
            "does). Annotations may be sent with NO nodes at all.\n"
            "Mark an edge label optional or async and the line is drawn dashed; unreliable draws it "
            "wavy. Set importance:high on the one or two nodes that carry the weight.\n"
            "2-7 nodes. Every node must be a concrete thing in THIS system (a service, a store, a "
            "control, an actor), never a topic heading. Index edges from 0. You do NOT choose "
            "positions, sizes or colours — `kind` picks the colour and layout is done for you."
        )

        usr = (f"DECISION UNDER REVIEW:\n{decision[:1200]}\n\n"
               f"WHAT YOU WANT TO SHOW: {purpose}\n\n"
               f"OPEN ISSUES: {', '.join((ag.get('raised') or [])[:6]) or '(none)'}\n"
               + (f"ALREADY ON THE BOARD (do not repeat it): {board_digest[:600]}\n" if board_digest else "")
               + (("ELEMENTS YOU CAN ANNOTATE (use these ids):\n"
                   + "\n".join(f"  {e['id']}: {e['label']!r} (by {e['author']})"
                               for e in (inventory or [])[:16]) + "\n") if inventory else "")
               + f"You are {persona.name}. Reply JSON only.")
        # 700 tokens truncated the strict-JSON spec mid-node-list on newer models (claude-sonnet-5),
        # so _extract_json recovered a partial object with <2 nodes and render refused it — a measured
        # cause of empty boards. 1100 gives the full 2-7 node spec + edges + annotations room to close.
        out = _extract_json(self._ask(sys, usr, max_tokens=1100))
        return out if isinstance(out, dict) else {}

    def critique_sketch(self, persona: Persona, spec: dict, audit: dict, description: str,
                        png: Optional[bytes] = None) -> Optional[dict]:
        """Look at the drawing that was just produced and fix it, or accept it.

        This is the auditor half of a planner–auditor loop (DiagrammerGPT), and the step that lets a
        model reason about a drawing rather than emit one blind: Whiteboard-of-Thought hands the
        rendered image BACK to the model, and Visual Sketchpad has it plan "according to the visual
        artifacts it has drawn". Geometry facts come from code (crossings and overflow are arithmetic,
        not opinion); the model judges whether the picture actually makes its point.

        Returns a revised spec, or None to keep what was drawn."""
        facts = (f"boxes={audit.get('boxes')} arrows={audit.get('arrows')} "
                 f"edge_crossings={audit.get('crossings')} bends={audit.get('bends')} "
                 f"overlapping_boxes={audit.get('overlaps')} "
                 f"labels_too_long={audit.get('truncated_labels')}")
        probs = "; ".join(audit.get("problems") or []) or "(no geometry problems found)"
        sys = ("You are reviewing a diagram YOU just drew on a meeting whiteboard, before the room "
               "sees it. Judge one thing: does it make its point at a glance? Fix it if not.\n"
               "Common fixes: drop nodes that carry no argument (fewer, sharper boxes beat more); "
               "re-order so the flow reads left to right without doubling back; change `type` if the "
               "shape is wrong (a causal chain is `flow`; trigger→impact is `failure`; inside-vs-"
               "outside is `boundary`; verdict+conditions is `decision`); shorten labels to <=4 words; "
               "mark the ONE node that is the punchline with emphasis \"circle\".\n"
               "You do NOT control positions, sizes or colours — layout is done for you, so never ask "
               "for coordinates. Reply STRICT JSON only: either {\"ok\":true} to accept it as drawn, "
               "or a full replacement spec {\"point\":...,\"type\":...,\"nodes\":[...],\"edges\":[...]}.")
        usr = (f"THE POINT YOU WANTED TO MAKE: {spec.get('point','')}\n"
               f"WHAT YOU DREW:\n{description}\n\nMEASURED: {facts}\nFLAGGED: {probs}\n\n"
               + ("The image above is the drawing as the room will see it. " if png else "")
               + "Accept it or return a better spec. JSON only.")
        try:
            raw = (self._ask_image(sys, usr, png) if png else self._ask(sys, usr, max_tokens=700))
        except Exception as e:                       # noqa: BLE001 - never break the meeting
            print(f"[quorum.critique] unavailable: {type(e).__name__}: {e}")
            return None
        out = _extract_json(raw)
        if not isinstance(out, dict) or out.get("ok") is True or not out.get("nodes"):
            return None
        return out

    def next_speaker(self, decision: str, context: str, view: list[dict], seated: list[Persona], recent: list[str],
                     agenda: Optional[dict] = None, owed_reply: bool = False) -> Optional[str]:
        cards = "\n".join(f"[{p.persona_id}] {p.name} — {p.stance}" for p in seated)
        sys = ("You are the MODERATOR of a live panel. Pick who should speak NEXT — the ONE member with "
               "the most pressing NEW thing to say. Do NOT pick someone to repeat a point already on the "
               "record, and do NOT re-open an issue the decision-maker has already answered or accepted — "
               "ADVANCE the meeting to an issue not yet aired. If the DECISION-MAKER (the human) just spoke, "
               "pick the member best placed to respond to THEM. Don't go around the table in order, and don't "
               "let one person dominate. Only say the debate is DONE when the panel has genuinely run out of "
               "NEW substantive points — allow rebuttals, cross-examination, and second-order concerns to "
               "surface first; do NOT end merely because each topic has been mentioned once. Once the main risks "
               "are on the table, a member who will AFFIRM what is sound, SYNTHESISE the open threads (including "
               "mapping them on the board), or move the room toward a decision is as valuable as another critic — "
               "favour balance and progress over piling on. STRICT JSON only.")
        ag = agenda or {}
        usr = (f"DECISION:\n{decision}\n\nPANEL:\n{cards}\n\nRECENT SPEAKERS (most recent last): {recent}\n"
               f"ALREADY RAISED (do not repeat): {ag.get('raised') or []}\n"
               f"SETTLED BY THE DECISION-MAKER (do not re-open): {ag.get('settled_by_human') or []}\n"
               f"{'THE DECISION-MAKER IS AWAITING AN ANSWER — pick whoever can answer them, and never say done.' if owed_reply else ''}\n\n"
               f"DISCUSSION SO FAR (recent):\n{json.dumps(view)[:2200]}\n\n"
               'Reply JSON: {"next":"<persona_id, or \'none\' if the debate is exhausted>","why":"<max 6 words>"}')
        out = _extract_json(self._ask(sys, usr, max_tokens=120)) or {}
        return (out.get("next") if isinstance(out, dict) else None) or None

    def escalate(self, decision: str, view: list[dict], available: list[Persona]) -> Optional[str]:
        if not available:
            return None
        avail = "\n".join(self._persona_card(p) for p in available)
        sys = ("You are the MODERATOR. If the deliberation has surfaced a dimension no seated member "
               "covers, summon ONE additional scrutineer; otherwise summon none. Reply STRICT JSON only.")
        usr = (f"DECISION:\n{decision}\n\nTRANSCRIPT (recent):\n{json.dumps(view)[:5000]}\n\n"
               f"AVAILABLE:\n{avail}\n\n" 'Reply JSON: {"summon": persona_id_or_null}')
        out = _extract_json(self._ask(sys, usr, max_tokens=200)) or {}
        s = out.get("summon") if isinstance(out, dict) else None
        return s or None

    def verdict(self, decision: str, context: str, transcript: Transcript, plan: QuorumPlan, agenda: Optional[dict] = None) -> dict:
        sys = ("You are the PRESIDING member. Render a DEFENSIBILITY verdict from the deliberation. "
               "Grade defensible / defensible_with_conditions / indefensible. State AGAINST WHOM it "
               "fails, the indefensible elements, and remediation. Reply STRICT JSON only.")
        human_turns = [e.text for e in transcript.entries if e.member == "human" and str(e.text).strip()]
        human_block = "\n".join(f"- {t}" for t in human_turns) if human_turns else "(the decision-maker did not speak during the session)"
        tail = json.dumps(transcript.to_dict())[-14000:]  # MOST RECENT slice, so late points aren't lost
        usr = (f"DECISION:\n{decision}\n\nCONTEXT:\n{context}\n\n"
               f"WHAT THE DECISION-MAKER (the human) TOLD THE PANEL — weigh this heavily and reflect it in "
               f"the ruling:\n{human_block}\n\n"
               f"OPEN ISSUES (unresolved): {(agenda or {}).get('raised') or []}\n"
               f"ACCEPTED BY THE DECISION-MAKER (state these as CONDITIONS, not blockers): "
               f"{(agenda or {}).get('settled_by_human') or []}\n\n"
               f"DELIBERATION (most recent):\n{tail}\n\n"
               'Reply JSON: {"grade":str,"summary":str,"against_whom":[str],'
               '"indefensible_elements":[{"element":str,"argument":str,"by":str}],"remediation":[str]}')
        raw_text = self._ask(sys, usr, max_tokens=3000)
        out = _extract_json(raw_text)
        if not isinstance(out, dict) or not str(out.get("summary", "")).strip():
            salv = (raw_text or "").strip()
            if isinstance(out, dict):
                out.setdefault("summary", salv[:3000])
            else:
                print(f"[quorum.verdict] non-JSON verdict; salvaging {len(salv)} chars")
                out = {"summary": salv[:3000]}
        return out
