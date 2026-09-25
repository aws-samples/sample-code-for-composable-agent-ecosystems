"""AI generates ecosystem config via Amazon Bedrock.

Architecture:
- Sync entry from Amazon API Gateway returns 202 + jobId immediately (under the 29s timeout).
- Async self-invocation does the actual Amazon Bedrock call (can take 30-60s).
- Result is pushed to the client via WebSocket using their clientId.

If the request has no clientId, falls back to fully synchronous mode (used for testing
and CLI invocations) — that path WILL hit the API Gateway timeout, by design.
"""
import os
import json
import re
import uuid
import boto3
import yaml

REGION = os.environ.get("AWS_REGION", "us-east-1")
WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "")
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")

# Eval-proposal pass (spec §8): buckets for reading the converged eco + writing
# the approved golden set. Optional — the proposal itself needs neither.
ECOSYSTEMS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")

# Web search (live research grounding) — same AgentCore Identity flow the
# external-grounding Lambda uses. Optional: if not configured, the builder still
# works, it just can't verify facts (assumptions stay needs_verification).
WEBSEARCH_CREDENTIAL_PROVIDER = os.environ.get("WEBSEARCH_CREDENTIAL_PROVIDER", "")
# Tavily endpoint (env-overridable; real default so a sanitized deploy can't break it).
WEBSEARCH_API_URL = os.environ.get("WEBSEARCH_API_URL", "https://api.tavily.com/search")
WORKLOAD_IDENTITY_NAME = os.environ.get("WORKLOAD_IDENTITY_NAME", "")
_agentcore_dp = boto3.client("bedrock-agentcore", region_name=REGION)
_ws_key_cache = {"key": None, "at": 0}


def _websearch_key():
    import time as _t
    if _ws_key_cache["key"] and (_t.time() - _ws_key_cache["at"] < 600):
        return _ws_key_cache["key"]
    if not WEBSEARCH_CREDENTIAL_PROVIDER or not WORKLOAD_IDENTITY_NAME:
        return ""
    wat = _agentcore_dp.get_workload_access_token(workloadName=WORKLOAD_IDENTITY_NAME).get("workloadAccessToken")
    if not wat:
        return ""
    key = _agentcore_dp.get_resource_api_key(
        resourceCredentialProviderName=WEBSEARCH_CREDENTIAL_PROVIDER,
        workloadIdentityToken=wat,
    ).get("apiKey", "")
    _ws_key_cache["key"] = key
    _ws_key_cache["at"] = _t.time()
    return key


def web_search(query):
    """Return a short list of {title, url, content} for a query, or [] on any
    failure (best-effort — never breaks a builder turn)."""
    try:
        import httpx
        key = _websearch_key()
        if not key:
            return []
        resp = httpx.post(
            WEBSEARCH_API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"query": str(query)[:400], "max_results": 4, "search_depth": "basic"},
            timeout=10,
        )
        resp.raise_for_status()
        return [{"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content", "") or "")[:500]}
                for r in resp.json().get("results", [])[:4]]
    except Exception:
        return []


def _ground_facts(design_state):
    """Search fact-type assumptions flagged needs_verification (cap 2/turn) and
    return [{statement, results}] to feed back into the next turn so the model
    can verify instead of guess."""
    out = []
    for a in (design_state or {}).get("assumptions", []) or []:
        if a.get("type") == "fact" and a.get("status") == "needs_verification":
            res = web_search(a.get("statement", ""))
            if res:
                out.append({"statement": a.get("statement", ""), "results": res})
            if len(out) >= 2:
                break
    return out

# The builder is the highest-leverage function in the platform: a single ecosystem
# YAML drives every downstream agent invocation. Use the most capable Opus available.
# Env-driven so versions can be bumped without code changes.
BUILDER_MODEL_ID = os.environ.get("BUILDER_MODEL_ID", "us.anthropic.claude-opus-4-7")

_QUORUM_ADDENDUM = """

QUORUM ARCHETYPE (only if the operator explicitly wants a scrutiny/defensibility PANEL):
  If — and only if — the operator asks for a *panel that scrutinises a decision* (e.g.
  "defensibility rehearsal", "would this survive a regulator/board/court", "convene experts
  to interrogate this decision"), design a QUORUM ecosystem instead of a DAG. Otherwise
  ignore this section entirely.
  A Quorum eco does NOT list agents/depends_on. It declares policy:
    strategy: quorum
    inputs: [{id: decision}, {id: context}, {id: supporting_documentation}]
    quorum:
      empanelment:
        persona_source: /catalog/scrutineer/
        select: {min: 3, max: 7, must_include: [regulators_eye],
                 diversity: [regulatory, conduct, customer, security, privacy, accountability, ethics, reputation]}
        completeness_adversary: true
        justify_exclusions: true
      deliberation: {max_rounds: 4, turn_policy: moderated, escalation: true, preserve_dissent: true}
      verdict:
        scale: [defensible, defensible_with_conditions, indefensible]
        must_state: [against_whom_it_fails, indefensible_elements, remediation]
    quality: {gate: {block_below: defensible_with_conditions}}
  Choose `diversity` dimensions and `must_include` personas that fit the decision's domain.
  Do NOT invent agents; the panel is convened at runtime from the scrutineer catalog.
"""

PROMPT_TEMPLATE = """You are an expert at designing multi-agent ecosystems for the Tessera platform.

Description: {description}

Tessera runs each agent in an AWS Step Functions topology where downstream agents receive
upstream agents' STRUCTURED FINDINGS. The platform's value is COLLABORATIVE
INTELLIGENCE — agents must build on each other, not work in parallel silos. Your
job is to design a YAML where each agent has a clear, NON-OVERLAPPING role and
where downstream agents are REQUIRED to cite upstream.

==================================================================
HARD RULES (failure to follow will produce a broken ecosystem)
==================================================================

1. EACH agent's `prompt` field MUST start with this opening sentence:
   "Your specific contribution: <one or two sentences naming the narrow contribution>."
   Example for a quant analyst:
     "Your specific contribution: historical-pattern matching, DCF/comparable-multiples
      fair value, and 3-scenario simulation. You quantify; others narrate."

2. Synthesizer agents (those with non-empty `depends_on`) MUST include in the prompt:
   "You do NOT: <list, by id, the things OTHER agents handle that you must not redo>."
   Example for a risk_manager that depends on quant + fundamental + macro:
     "You do NOT: do quantitative pattern analysis (quant handles that),
      fundamental analysis (fundamental handles that), or macro outlook
      (macro handles that). Your job is risk synthesis on top of their work."

3. Synthesizer agents MUST end their prompt with this exact citation rule:
   "Cite each upstream agent's findings using `[per <agent_id>: <upstream finding>]`
    for every claim that derives from them. Use `[novel — <reason>]` for any claim
    with no upstream basis. Standalone synthesis without citations will be REJECTED."

4. TOOL COMPOSITION (strict — the platform validates this):
   - Leaf agents (depends_on: []) MAY include `web_search` for fresh research.
   - Synthesizer agents MUST NOT include `web_search` / `exa_search`.
     They synthesize upstream + use `memory_read` if they need shared catalogs.
   - RESEARCH-WITH-CONTEXT: if a step needs BOTH upstream context (depends_on)
     AND fresh web research, DO NOT make it a synthesizer with web_search (the
     platform zero-budgets that). Set `output_kind: researcher` — a first-class
     type that KEEPS its `depends_on` (so it sees upstream) AND gets a real
     `web_search` budget (like a leaf). Its findings are grounding-checked. Use
     it whenever an agent must look something up based on what upstream produced
     (e.g. "find comps for the car the ingest agent identified").
   - All agents may use: `memory_read`, `memory_write`, `code_interpreter`,
     `calculator`, `current_time`, `generateUI`.

5. DAG SHAPE:
   - Generate 5-8 agents organized in 2-4 layers.
   - Leaves run in parallel (depends_on: []).
   - At least one synthesizer layer that depends on multiple leaves.
   - The final stage depends on the ecosystem's DELIVERABLE TYPE — see Rule 9.

6. TOOL BUDGETING (Phase 5.1 — important):
   The platform applies sensible default per-tool budgets per agent based on role
   (leaf vs synthesizer). Defaults:
   - web_search: 6 (leaf), 0 (synthesizer — they synthesize, not research),
     6 (researcher — a synthesizer-with-deps that IS allowed to research)
   - memory_read / memory_write: UNCAPPED (the inter-agent handoff mechanism — never capped)
   - code_interpreter: 3 (leaf), 5 (synthesizer)
   - generateUI: 5 (leaf), 8 (synthesizer)

   Defaults are tuned for typical workloads. Add a per-agent `tool_budget:`
   override ONLY when the agent's role legitimately demands more or less:
   - Override UP when persona implies exhaustive research (e.g.,
     "literature reviewer", "deep market analyst", "multi-instrument scanner"
     covering 8+ assets) — bump web_search to 10-15.
   - Override DOWN to 0 when you want to disable a tool entirely.
   - Otherwise OMIT the field — defaults handle it.

   Example (only when needed):
     tool_budget:
       web_search: 12
       code_interpreter: 6

7. TASK TIMEOUT (rarely needed — defaults are correct for almost all cases):
   The platform catches stuck agents via a 90-second stream-silence heartbeat
   inside the agent-invoker Lambda; an absolute AWS Step Functions backstop fires
   at 900s only if the heartbeat itself misbehaves. Operators DO NOT need to
   set `task_timeout` for normal agents. Add `task_timeout: <int seconds>` per
   agent ONLY when:
   - the agent legitimately needs more than 900s end-to-end (very rare;
     would also require a platform-level Lambda timeout bump above 900s)
   - or the operator wants a TIGHTER backstop than 900s for cost containment
     on a known-bounded task

   The 90s silence threshold is NOT operator-tunable per-agent — it is a
   platform-wide signal-of-stuck. Override only the absolute backstop.

   Example (only when needed):
     task_timeout: 600

7b. RETRY ATTEMPTS (rarely needed — default is correct for almost all cases):
   The platform retries each agent task once on transient failures (citation
   contract drops, Lambda service exceptions). Default is `retry_attempts: 1`
   meaning the agent gets one second chance before the run stops. Operators
   may override per-agent via YAML `retry_attempts: <int>` — set 0 to disable
   retry entirely (useful for code_interpreter-heavy agents where retry doubles
   the cost), or 2-3 for agents with very stochastic tool patterns.

   Example (only when needed):
     retry_attempts: 0    # never retry — single attempt only
   or
     retry_attempts: 2    # try up to 3 times total

8. MEMORY DESIGN (mandatory — prevents downstream agents from losing access to
   synthesizer findings):
   - Every SYNTHESIZER agent MUST have at least one writable namespace declared
     in `memory.shared_namespaces`. Without it, `memory_write` returns "not
     authorized" and the synthesizer's analysis is invisible to downstream agents
     that try to consult it via `memory_read`. The platform's structured-findings
     pipeline still flows, but cross-layer memory persistence breaks.

   Two namespace SCOPING rules (critical for correctness):

   SCOPE 1 — PER-RUN MEMORY (isolated, no cross-run leakage)
   Use single-segment paths: path: /assessment, path: /songbook, path: /trading_thesis
   Each run gets its own session. Run 2 NEVER sees data from Run 1.
   Use this when agents pass data to each other WITHIN a single run.

   SCOPE 2 — PERSISTENT CATALOG (shared across runs, accumulates knowledge)
   Use multi-segment paths: path: /catalog/properties/, path: /catalog/risks/
   Data persists and is visible to all future runs.
   Use this ONLY for reference catalogs that should grow over time
   (e.g. property database, risk catalog, regulatory rules).

   RULE: If the ecosystem processes a NEW input each run (a new document,
   a new question, a new case), its working namespaces MUST be per-run
   (single-segment). Only use /catalog/xxx/ for data that should accumulate.

   Two patterns — pick the right one for each namespace:

   PATTERN A — DOSSIER (multiple peers contribute findings about the SAME subject)
   Use this for per-property, per-case, per-customer, per-application,
   per-incident dossiers where every Layer-1 specialist working on the same
   subject should publish its findings so downstream synthesizers see the
   full picture.
     - name: properties           # or claims, models, applications, incidents
       path: /catalog/properties/
       write: [all]               # ← every agent can publish findings
       read: [all]                # ← every agent can consult peers
       auto_persist: true         # ← findings auto-mirror, no manual memory_write

   PATTERN B — CANONICAL CATALOG (one agent owns; many read)
   Use this when exactly one agent is the authoritative source of a domain
   catalog (e.g. an "agent_patterns" catalog written by a researcher who
   profiles real-estate agents; a "council_rules" catalog written by a
   regulation-extractor specialist). Other agents only read.
     - name: agent_patterns
       path: /catalog/agents/
       write: [auction_intelligence_analyst]   # ← single canonical writer
       read: [all]                             # ← everyone reads
       auto_persist: true                      # ← still good — auto-publishes
                                               #   the writer's findings without
                                               #   it needing memory_write calls

   AVOID (anti-pattern, will trigger compiler warning):
     - name: properties
       path: /catalog/properties/
       write: [single_specialist]   # ← only one writer
       read: [all]                  # ← but many readers
       # ← no auto_persist
   Result: downstream agents find at most that one specialist's entry;
   peer specialists' findings are invisible. Compiler emits
   `shared_namespace_one_writer_no_auto_persist` warning at save time.

   Default to PATTERN A unless there's a clear single-writer justification.

   Recommended layering example for a hedge-fund-style ecosystem:
     - desk_research   write: [all]                read: [all]   auto_persist: true
     - desk_decisions  write: [all]                read: [all]   auto_persist: true
     - trade_book      write: [<composer agent>]   read: [all]   auto_persist: true

9. DELIVERABLE TYPE (decides whether the ecosystem produces a downloadable file):
   First CLASSIFY what the ecosystem is for, then shape the final stage accordingly.

   EXECUTION MODEL (decide FIRST): is this ecosystem INTERACTIVE or ORCHESTRATED?
     - INTERACTIVE (`strategy: interactive`): it ANSWERS — a live, conversational,
       sub-minute multi-agent turn (Q&A, triage, routing, a concierge/front-door,
       "what can you do about X"). No downloadable deliverable; no Step Functions;
       served synchronously and streamed. Use when the value is a fast answer, not a
       produced artifact.
     - ORCHESTRATED (`strategy: orchestrated`, default): it PRODUCES — a multi-agent
       batch that runs for minutes and yields an analysis/decision/deliverable, with
       challenger + gates. Use when the value is a rigorous produced output.
     If the intent is genuinely ambiguous, ASK the operator: "Should this answer live
     and conversationally (interactive), or produce a reviewed deliverable (orchestrated)?"
     Set `strategy: interactive` for the former; otherwise keep orchestrated.

   (a) REPORT / ANALYSIS / DECISION ecosystems — the value is the synthesized
       findings, narrative, scores, or recommendation shown in the run window.
       Examples: investment analysis, debate synthesis, risk scoring, incident
       triage. These DO NOT need a file. Set:
         outcome:
           kind: report
       Do NOT add a drafter agent. The platform's report stage handles presentation.

   (b) DELIVERABLE ecosystems — the value is a downloadable FILE the user takes
       away. Examples: a spec document, a vendor-assessment report, a runbook/
       playbook, a generated dataset, a chart bundle. These MUST end with a
       DRAFTER agent and declare a file_artifact outcome.

   For DELIVERABLE ecosystems only, the final agent is a DRAFTER that:
     * Has `output_kind: drafter`
     * Has `code_interpreter` in its tools list
     * Depends on the final synthesizer(s)
     * Has `output_format: {{ type: <fmt>, save_as: <filename>, validate: true }}`
     * Its prompt instructs it to BUILD the file inside code_interpreter and
       write it to <filename> (the exact save_as name).

   CHOOSE THE FORMAT FROM THE DELIVERABLE TYPE (output_format.type MUST match
   the outcome artifact format):
     - spec / config / schema            -> yaml
     - document / report / playbook      -> html   (rich, inline CSS)
     - printable / formal document       -> pdf    (use fpdf in code_interpreter)
     - tabular data / matrix             -> csv
     - structured data / API payload     -> json
     - notes / readme / markdown doc     -> markdown

   And declare the matching top-level outcome:
     outcome:
       kind: file_artifact
       audience: <who READS/acts on the deliverable — e.g. "board of directors",
                  "customer", "regulator", "clinician". SET THIS for decision /
                  assessment / report deliverables (see Rule 9c). OMIT for creative /
                  pure-data artifacts.>
       artifacts:
       - id: <artifact_id>
         agent: <drafter_agent_id>
         format: <same fmt as the drafter's output_format.type>
         save_as: <same filename as the drafter's output_format.save_as>
         download_label: <human-readable label>

==================================================================
RULE 9c — AUDIENCE-APPROPRIATE PROVENANCE (do NOT hand-author it)
==================================================================
For decision / assessment / report deliverables, set `outcome.audience` to who will READ
and act on the deliverable (e.g. "board of directors", "customer", "regulator", "clinician").
The platform then injects an audience-appropriate PROVENANCE contract into the deliverable
agent (reporter/drafter): the body cites Authority / Evidence / Confidence in the audience's
language, machine identifiers (agent ids, /tessera/catalog paths, record slugs) are kept OUT
of the body, and a platform-verified "Assurance & Provenance" block is stamped from the run
record.

THEREFORE: do NOT write citation/provenance-rendering instructions into a deliverable agent's
prompt — no "cite [per agent_id]", no "list catalog record ids", no bespoke "Citations &
Provenance section" spec. Just set `output_kind` + `outcome.audience` and let the platform
render provenance. (Upstream analysts/synthesizers still cite each other via the standard
citation rule — Rule 9c is only about the FINAL deliverable's rendering.)

Omit `audience` for creative or pure-data artifacts (songbook, diagram, dataset) — they
correctly get no provenance contract.

DECISION ENGINE (assessment / go-no-go / rating ecosystems only — docs/spec-decision-engine.md):
When the ecosystem's job is to CLASSIFY items and reach a REPRODUCIBLE verdict (risk go/no-go,
buy/hold/sell, approve/reject, pass/fail), add a top-level `decision:` block so the label and
the verdict are COMPUTED by a rubric — not argued in prose (which varies run-to-run). Shape:
  decision:
    scored_by: <agent_id that emits the items — usually the synthesizer>
    item_noun: finding            # or position / option / claim
    dimensions:                   # the eco's OWN scoring axes (typed)
      <name>: {type: ordinal, levels: [low, medium, high]}   # or boolean / category:{values:[...]} / number
      confidence: {use: verifiability}                        # optional common dim: reasoning<assertion<verified<grounded
    labels: [<most_severe>, ..., <least>]
    rubric:                       # first match wins; MUST end with a default
      - {label: <label>, when: "<expr over dimensions>"}
      - {label: <least>, default: true}
    decision:
      outcomes: [<...>]
      rule:                       # aggregates over the labelled set; MUST end with a default
        - {outcome: <...>, when: "count(label == <label> and status == open) > 0"}
        - {outcome: <...>, default: true}
      conditions_from: [<labels that become conditions>]
The scored_by agent emits `## DECISION ITEMS` (dimension VALUES only); the platform computes
labels + verdict + a trace; the deliverable renders them verbatim. Expr operators: == != < <= >
>= (ordinals compare by declared order), and/or/not, in, and count()/any()/all() in the decision
rule. Only add this for assessment ecos — creative/narrative ecos omit it (stay prose).


==================================================================
RULE 9d — TAG LIVE-RESEARCH AGENTS `output_kind: researcher`
==================================================================
Any agent whose job is to gather FRESH EXTERNAL FACTS (it lists `web_search` /
`exa_search` / `web_fetch`) MUST be tagged `output_kind: researcher` — even when
it has `depends_on`. The `swarm:` name is only for grouping and does NOT grant a
research budget. An agent that lists `web_search` but is left untagged AND has
`depends_on` compiles as a SYNTHESIZER whose web_search budget is 0: it will
silently be unable to search and will fabricate/guess instead. Rule of thumb:
  - leaf researcher (no deps, does research)             → no output_kind needed
  - dependent researcher (has deps, does live research)  → output_kind: researcher
  - composes from upstream + memory only                 → synthesizer (do NOT list web_search)
  - builds the final downloadable file                   → output_kind: drafter
Do NOT put `web_search`/`web_fetch` on a synthesizer or drafter; if it genuinely
needs a live fact, make it `output_kind: researcher` instead.

==================================================================
RULE 10 — RECOMMEND A QUALITY POSTURE (operator confirms in the wizard)
==================================================================
Always emit a top-level `quality:` block with a DELIBERATE recommendation that
fits the pipeline shape. The operator can change it, but your recommendation is
the starting point.

`challenger` — adversarial review mode:
  - per_level → RECOMMEND for any pipeline with 2+ sequential reasoning layers
    OR a downloadable deliverable. A challenger reviews each premise-bearing
    layer (research/synthesis/drafting); if a layer's verdict is below
    `block_below` the run pauses for a human decision (Accept / Loop / Abort),
    so a wrong premise is caught where it originates instead of contaminating
    everything downstream. Loop re-runs that layer with the critique injected.
  - final → short/flat pipelines (one reasoning layer) where a single
    end-of-run review is sufficient.
  - off → trivial single-agent or purely creative ecosystems where adversarial
    review adds no value.

`challenger_gate.block_below` — when a checkpoint pauses (per_level only):
  - ADEQUATE → DEFAULT. Pause only on a WEAK verdict.
  - STRONG → strict; pause unless the verdict is STRONG (high-stakes deliverables).
  - WEAK → advisory; effectively never pauses.

`grounding` → true for any factual/research ecosystem; false only for creative.

`gates` — what happens when a per-output validator fails (citation / format /
grounding / output_kind):
  - ask → DEFAULT. Pause for the operator to decide.
  - warn → log and continue (advisory).
  - halt → fail the run immediately.
  Recommend `ask` for citation and grounding on factual ecosystems.

==================================================================
YAML SCHEMA (output ONLY valid YAML, no markdown fences)
==================================================================

ecosystem:
  name: <kebab-case-id>
  description: <one paragraph>
  version: "1.0"

strategy: orchestrated
report: true

# Discovery metadata (the ecosystems page GROUPS by `category` and SEARCHES
# `tags`). Choose a concise, human-readable `category` in Title Case (1-3 words)
# that best fits the ecosystem's DOMAIN. Prefer a common bucket when one fits —
# e.g. Finance, Security, Risk & Compliance, Research & Analysis, Operations,
# Vendor, Real Estate, Healthcare, Legal, Creative, Lifestyle — and only invent a
# better-fitting category when none apply. Add 2-4 short lowercase `tags`.
category: <Title Case domain category>
tags: [<2-4 short lowercase tags>]

# Recommended quality posture (Rule 10) — the operator confirms/edits this in
# the wizard. Always emit this block with a deliberate recommendation.
quality:
  challenger: per_level        # off | final | per_level  (see Rule 10)
  challenger_gate:
    block_below: ADEQUATE      # STRONG | ADEQUATE | WEAK
  grounding: true
  gates:
    citation: ask              # ask | warn | halt
    format: ask
    grounding: ask
    output_kind: ask

# Pick ONE outcome form:
#   report-only ecosystems:   outcome: {{ kind: report }}
#   deliverable ecosystems:   the file_artifact block (see Rule 9)
outcome:
  kind: report          # or file_artifact (+ artifacts: below) per Rule 9

memory:
  strategies: [semantic]
  shared_namespaces:
    - name: <namespace_name>
      path: /<namespace-path>/
      write: [<agent_ids>]
      read: [<agent_ids> or "all"]

agents:
  - id: <kebab-case>
    name: <Display Name>
    swarm: <research|risk|challenge|...>
    depends_on: []   # or list of upstream ids
    tools: [<from approved list above>]
    prompt: |
      Your specific contribution: <...>
      <persona body — domain language, what they do, how they think>
      <if synthesizer: "You do NOT: ...">
      <if synthesizer: ending citation rule (verbatim from rule #3 above)>

  # ONLY for DELIVERABLE ecosystems (Rule 9b) — the final drafter agent:
  - id: <drafter-id>
    name: <Display Name>
    swarm: synthesis
    depends_on: [<final synthesizer id(s)>]
    output_kind: drafter
    output_format:
      type: <yaml|html|pdf|csv|json|markdown>
      save_as: <filename>
      validate: true
    tools: [memory_read, code_interpreter, generateUI]
    prompt: |
      Your specific contribution: assemble the final downloadable deliverable.
      Build the file inside code_interpreter and write it to <filename>.
      <if synthesizer: ending citation rule (verbatim from rule #3 above)>

==================================================================
QUALITY CHECKLIST (mentally verify before emitting)
==================================================================
- Every agent has a clear, narrow contribution that doesn't overlap with siblings.
- Synthesizers explicitly delegate sibling jobs in their "You do NOT" lists.
- depends_on graph is acyclic and every id referenced is defined.
- No synthesizer has web_search (if it needs live research with upstream context,
  make it `output_kind: researcher` instead — that keeps depends_on and gets web_search).
- Every synthesizer prompt ends with the citation rule (rule #3 verbatim).
- Outcome is classified (Rule 9): report-only ecosystems set `outcome: {{ kind: report }}`
  and have NO drafter; deliverable ecosystems end with a drafter agent
  (`output_kind: drafter` + `output_format`) and declare `outcome.kind: file_artifact`.
- For deliverable ecosystems: the drafter's `output_format.type`, its `save_as`,
  and the `outcome.artifacts[].format`/`save_as` all MATCH, and the format fits
  the deliverable type (spec->yaml, document->html/pdf, tabular->csv, data->json).
- A concise top-level `category` (Title Case, domain-fitting) and 2-4 `tags` are set.
"""


CONVERSATION_SYSTEM = """You are the Tessera Builder — a conversational design partner that
helps a user design a multi-agent ecosystem, then converges on a deployable spec.

GOLDEN RULES OF THE CONVERSATION:
- CHAT FIRST, DESIGN LATER. On the FIRST turn (no design yet) do NOT create agents. Briefly
  acknowledge the goal in one line and ask your single most important clarifying question.
  Only start sketching agents once you actually understand the intent.
- ONE QUESTION AT A TIME. Ask EXACTLY ONE question per turn (the most important still-open intent
  item). Provide `options` when it is a choice so the user can tap an answer.
- NEVER silently guess. intent (only the user knows) -> ASK. fact (a named format/tool/standard)
  -> do NOT invent; flag it as an assumption with status "needs_verification". consistency
  (internal) -> self-check. NEVER guess the format OR cardinality of a NAMED artifact from a
  keyword ("spec"/"doc"/"report" do NOT imply a format or how many files).
- USE VERIFIED FACTS. The turn input may include `verified_facts` (web-search results for your
  prior needs_verification assumptions). When present, use them to set those fact assumptions to
  status "grounded" (or "contradicted") with a one-line evidence note citing the source — do NOT
  keep asking the user to confirm a fact you can now verify.
- SKELETON UNTIL CONVERGED. While converged=false, agents are SKELETONS ONLY:
  {id, name, role, depends_on} — NO prompt, NO tools. This keeps turns small and fast.
  ONLY when you set converged=true do you include the FULL agents (with swarm, tools, prompt,
  and output_kind/output_format for the drafter) — that final turn is the complete design.
- converged=true ONLY when no intent-type assumption is still "open" and the design is consistent.

OUTPUT FORMAT — your response has EXACTLY two parts:
PART 1: a short, friendly reply to the user — AT MOST 2 sentences, plain prose, no JSON. This is
the only thing shown to the user as text.
PART 2: a new line with the marker
===DESIGN===
then a SINGLE JSON object (no markdown fences):
{
  "next_question": { "id": "...", "text": "...", "options": ["...", "..."] } | null,
  "design": {
    "intent": { "goal":"...", "name":"<kebab-name>", "domain":"...", "scope":"...",
                "category":"<Title Case domain category, 1-3 words>", "tags":["<2-4 short lowercase tags>"],
                "deliverable": {"kind":"report|file_artifact","format":"<or null if unconfirmed>",
                                "cardinality":"single|bundle|none"} },
    "assumptions": [ {"id":"a1","statement":"...","type":"intent|fact|consistency",
                      "status":"open|grounded|confirmed|needs_verification"} ],
    "agents": [ "<while NOT converged: {id,name,role,depends_on} skeletons only>",
                "<when converged: full {id,name,swarm,depends_on,tools,prompt,output_kind?,output_format?}>" ],
    "outcome": { "kind":"report|file_artifact",
                 "artifacts":[ {"id":"...","agent":"<drafter id>","format":"...","save_as":"...","download_label":"..."} ] },
    "quality": { "challenger":"off|final|per_level","block_below":"STRONG|ADEQUATE|WEAK","grounding":true,
                 "gates":{"citation":"ask","format":"ask","grounding":"ask","output_kind":"ask"} },
    "memory": { "strategies":["semantic"],
                "shared_namespaces":[ {"name":"<slug>_findings","path":"/<slug>_findings",
                                       "write":["all"],"read":["all"],"auto_persist":true} ] }
  },
  "converged": false
}

Design rules (apply when you expand the full design at convergence):
  - Set intent.category (concise Title Case domain bucket, e.g. Finance, Security,
    Risk & Compliance, Research & Analysis, Operations, Vendor, Real Estate,
    Healthcare, Legal, Creative, Lifestyle — invent one only if none fit) and 2-4 short
    lowercase intent.tags. These power the ecosystems page grouping + search.
  - Leaf agents (depends_on: []) research/gather; a synthesizer depends on them; a deliverable
    ecosystem ends with a drafter (output_kind: drafter + output_format) and outcome.kind=file_artifact;
    a report-only ecosystem has no drafter and outcome.kind=report.
  - The drafter MUST include "code_interpreter" in its tools — it builds the file (or zips the
    bundle) there. For a bundle it writes several files and zips them into the .zip save_as.
  - The drafter's output_format.type/save_as MUST match outcome.artifacts[].format/save_as.
  - Supported deliverable formats: yaml, html, pdf, csv, json, markdown, docx, png, and
    "bundle" (a .zip of MULTIPLE files). When the user wants a SET of files (e.g. several
    steering docs), use a drafter with output_format.type "bundle" and save_as ending ".zip",
    and set the outcome artifact format to "bundle". For a single file, use the matching type.
  - Recommend quality.challenger=per_level for pipelines with 2+ reasoning layers or a deliverable.
  - Keep each agent's contribution narrow and non-overlapping.
  - MEMORY (always emit `memory.shared_namespaces` once you have 2+ agents — an eco with NO
    namespace makes any `memory_write` return "not authorized", a dead-end). Default to ONE
    per-run shared dossier so every agent can publish findings and downstream agents can read them:
      {name:"<slug>_findings", path:"/<slug>_findings", write:["all"], read:["all"], auto_persist:true}
    SCOPING (important): a SINGLE-SEGMENT path like /xxx_findings is isolated to the CURRENT RUN
    (no cross-run or cross-eco leakage) — use this for working memory. Use a /catalog/<x>/ path
    (trailing slash) ONLY for reference knowledge that should accumulate ACROSS runs. `auto_persist`
    publishes each agent's findings automatically, so agents don't need manual memory_write calls;
    give synthesizers `memory_read` so they can consult peers. Do NOT instruct an agent to "write to
    shared memory" unless a namespace in shared_namespaces grants it write.

  - DOMAIN (D.7): if `current_design._domain` is present, this ecosystem BELONGS to that domain.
    Set the top-level `domains: [<_domain.domain>]`. The domain already provides shared, entity-keyed
    catalogs (listed in `_domain.catalogs` as {type,key}); REUSE them — wire relevant agents to read
    them (memory_read), and where an agent produces that durable entity, include the catalog in
    memory.shared_namespaces with `read:[all]` and the producing agent in `write`. Do NOT re-invent a
    catalog the domain already provides. Only add a NEW catalog if the domain genuinely lacks it.
    Conclusions (valuations, rankings, verdicts) stay run-only — never cataloged.
"""

def _quorum_enabled():
    return os.environ.get("QUORUM_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


_CONVO_QUORUM_ADDENDUM = """

── QUORUM SENSING (a distinct ecosystem SHAPE — decide EARLY, in business terms) ──
Some requests are not about PRODUCING a deliverable or ANSWERING a question — they are about
having KEY DECISIONS scrutinised and judged for DEFENSIBILITY: "pressure-test this before we
commit", "would this hold up / survive scrutiny", "have it picked apart from every angle", "is
this a sound decision or a mistake", "convene people to interrogate this". This is the QUORUM
shape: at RUN TIME the user brings ONE specific decision and a PANEL is assembled FOR THAT
DECISION, argues adversarially, preserves dissent, and returns a VERDICT. It is fully GENERIC —
it works for ANY decision, ANY industry, ANY company size, corporate OR personal. It is NOT a
fixed pipeline.

On the FIRST turn, SENSE the shape — do NOT ask a technical question (the user won't know
"DAG vs panel"). Record it in design.intent:
  - approach = "panel"    → key decisions to be scrutinised & given a verdict (the Quorum shape)
  - approach = "workflow" → produces a deliverable / answers a question (normal shape; default)
  - approach_label  = plain-English one-liner (e.g. "A panel that scrutinises a decision and
      returns a defensible verdict")
  - approach_reason = one sentence on why you sensed this.

If approach="panel", CONFIRM in business language as your next_question, e.g.
  text: "It sounds like you want a panel that scrutinises a decision and gives you a defensible
         verdict, rather than a workflow that produces a document — have I got that right?"
  options: ["Yes — scrutinise decisions", "No — produce a deliverable"]

CRITICAL — BUILD TIME vs RUN TIME. You are designing a REUSABLE panel, NOT judging one decision:
  - Do NOT ask about, or bake in, the SPECIFIC decision, its details, or its particular
    pros/cons — that is a RUN-TIME input (the `decision` input), supplied every time the eco runs.
    Asking "what is the decision / what would make IT indefensible" is a RUN-TIME question — never
    ask it at build time.
  - Keep the `decision`/`context` input LABELS GENERAL and reusable. NEVER tailor them to one
    example decision (no "new city / pay change / this warehouse").
  - Do NOT prescribe which reviewers sit or which dimensions matter — the PANEL IS ASSEMBLED AT
    RUN TIME from the actual decision. Leave must_include and diversity EMPTY.
  - Ask AT MOST one BUILD-TIME design question, and only if it genuinely varies the design (e.g.
    "a tight panel of 3-4 or a broad 6-8?"). Otherwise converge immediately with sensible defaults.

When approach="panel" and converged=true, DO NOT emit `agents`. Instead set:
  design.strategy = "quorum"
  design.inputs   = [ {"id":"decision","type":"text","label":"The decision under scrutiny","required":true},
                      {"id":"context","type":"text","label":"Context — who/what is affected, stakeholders, constraints, timing, and any jurisdiction","required":false} ]
  design.quorum   = {
    "empanelment": {"select": {"min":3,"max":6,"must_include":[],"diversity":[]},
                    "completeness_adversary": true, "justify_exclusions": true},
    "deliberation": {"max_rounds":2,"turn_policy":"moderated","escalation":true,"preserve_dissent":true},
    "verdict": {"scale":["defensible","defensible_with_conditions","indefensible"],
                "must_state":["against_whom_it_fails","indefensible_elements","remediation"]}
  }
  design.quality  = {"block_below":"defensible_with_conditions"}
For approach="workflow", ignore this section and design normally.
"""


def _convo_system():
    """The builder system prompt, with the Quorum-sensing guidance appended only when the
    archetype is enabled (byte-identical to today when the flag is off)."""
    return CONVERSATION_SYSTEM + (_CONVO_QUORUM_ADDENDUM if _quorum_enabled() else "")


_DESIGN_MARKER = "===DESIGN==="


def _parse_turn(text):
    """Split a two-part builder response into {reply, next_question, design,
    converged, ...}. PART 1 = prose reply; PART 2 (after ===DESIGN===) = JSON."""
    if _DESIGN_MARKER in text:
        reply, _, rest = text.partition(_DESIGN_MARKER)
    else:
        reply, rest = text, ""
    reply = reply.strip()
    rest = rest.strip()
    if rest.startswith("```"):
        rest = "\n".join(rest.split("\n")[1:])
        if rest.endswith("```"):
            rest = "\n".join(rest.split("\n")[:-1])
    structured = {}
    if rest:
        try:
            structured = json.loads(rest)
        except json.JSONDecodeError:
            structured = {}
    turn = {
        "reply": reply or "(no reply)",
        "next_question": structured.get("next_question"),
        "design": structured.get("design") or {},
        "converged": bool(structured.get("converged")),
    }
    if turn["converged"]:
        try:
            y = _compile_design_to_yaml(turn["design"])
            turn["yaml"] = y
            cfg = yaml.safe_load(y)
            turn["config"] = cfg
            turn["outputs"] = _extract_outputs(cfg)
            turn["quality"] = _extract_quality(cfg)
        except Exception as e:
            turn["converged"] = False
            turn["reply"] = turn["reply"] + f"\n\n(Couldn't compile the design yet: {str(e)[:120]})"
    return turn


def _turn_messages(history, message, design_state):
    return [{"role": "user", "content": json.dumps({
        "current_design": design_state or {},
        "conversation_so_far": history or [],
        "user_message": message or "",
        "verified_facts": _ground_facts(design_state),
    })}]


def converse(history, message, design_state):
    """Non-streaming design turn (sync/testing). Returns the parsed turn."""
    client = boto3.client("bedrock-runtime", region_name=REGION)
    resp = client.invoke_model(
        modelId=BUILDER_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "system": _convo_system(),
            "messages": _turn_messages(history, message, design_state),
        }),
    )
    result = json.loads(resp["body"].read())
    if result.get("stop_reason") == "max_tokens":
        raise ValueError("Conversational turn was truncated (max_tokens).")
    return _parse_turn(result["content"][0]["text"])


def _resolve_connections(client_id):
    """Return (apigw_client, [connectionId]) for a clientId, or (None, [])."""
    if not CONNECTIONS_TABLE or not WS_ENDPOINT or not client_id:
        return None, []
    table = boto3.resource("dynamodb", region_name=REGION).Table(CONNECTIONS_TABLE)
    items = table.scan(
        FilterExpression="clientId = :cid",
        ExpressionAttributeValues={":cid": client_id},
    ).get("Items", [])
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
    return apigw, [i["connectionId"] for i in items]


def _post(apigw, conns, payload):
    if not apigw:
        return
    data = json.dumps(payload).encode()
    for c in conns:
        try:
            apigw.post_to_connection(ConnectionId=c, Data=data)
        except Exception:
            pass


def converse_stream(history, message, design_state, client_id, job_id):
    """Streaming design turn: streams PART-1 reply tokens as CONVERSE_DELTA, then
    pushes a final CONVERSE_RESULT with the structured turn. Keeps the builder
    model (quality); liveness comes from streaming, not a faster model."""
    apigw, conns = _resolve_connections(client_id)
    client = boto3.client("bedrock-runtime", region_name=REGION)
    try:
        resp = client.invoke_model_with_response_stream(
            modelId=BUILDER_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8192,
                "system": _convo_system(),
                "messages": _turn_messages(history, message, design_state),
            }),
        )
        full = ""
        streamed = 0          # chars of reply already sent as deltas
        marker_hit = False
        hold = len(_DESIGN_MARKER) - 1
        for event in resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                continue
            d = json.loads(chunk["bytes"])
            if d.get("type") != "content_block_delta":
                continue
            piece = (d.get("delta") or {}).get("text", "")
            if not piece:
                continue
            full += piece
            if marker_hit:
                continue
            idx = full.find(_DESIGN_MARKER)
            if idx == -1:
                # stream reply, holding back a tail so we never emit a partial marker
                safe = len(full) - hold
                if safe > streamed:
                    _post(apigw, conns, {"type": "CONVERSE_DELTA", "jobId": job_id, "text": full[streamed:safe]})
                    streamed = safe
            else:
                if idx > streamed:
                    _post(apigw, conns, {"type": "CONVERSE_DELTA", "jobId": job_id, "text": full[streamed:idx]})
                    streamed = idx
                marker_hit = True
        turn = _parse_turn(full)
        _post(apigw, conns, {"type": "CONVERSE_RESULT", "jobId": job_id, **turn})
    except Exception as e:
        _post(apigw, conns, {"type": "CONVERSE_RESULT", "jobId": job_id, "error": str(e)[:300]})


# ── Conversational BEAUTIFY (deliverable design studio) ──────────────────────
# Mirrors the builder's converse engine, but instead of DESIGNING an ecosystem it
# collaboratively BEAUTIFIES a produced HTML deliverable one instruction at a time,
# in a WYSIWYG loop. The frontend supplies the CURRENT html each turn (stateless),
# so this needs no S3/run-bucket access. Phase 1: design refinement only (no
# persistence, no template promotion — see docs/spec-beautify-standardize.md).
BEAUTIFY_SYSTEM = """You are Tessera's deliverable DESIGN agent. You beautify a produced HTML \
deliverable with the user in a live WYSIWYG loop by writing CSS OVERRIDES — you do NOT rewrite \
the document.

You are given:
- the deliverable's FULL HTML (context only — read its structure: tags, classes, ids),
- the CSS OVERRIDES already applied (may be empty), and
- one INSTRUCTION.

Return the COMPLETE, updated CSS override stylesheet that satisfies the instruction — including \
any prior overrides you still want (you may revise or drop them). This stylesheet is injected LAST \
in the document <head>, so your rules win over the document's own styles. Target the document's \
existing elements/classes/ids with specific selectors.

RULES:
- The CSS block styles ONLY — no HTML, no <style> tag, no markdown fences, no commentary inside it.
- Keep selectors specific enough to win; prefer classes/ids the document already uses.
- Keep it clean, professional, board-appropriate.

STRUCTURAL / LAYOUT CHANGES: do NOT attempt these in CSS and NEVER rewrite the document. Emit a \
PRESENTATION TRANSFORMS block — a JSON array from this catalog:
- {"type":"tabs"}  -> groups the top-level <h2> sections into a tabbed layout.
- {"type":"diagram","id":"<short-stable-id>","anchor":"<exact heading text from the OUTLINE>","mode":"after"|"before"|"replace","title":"<short caption>"}
     -> a CONTENT-DERIVED data VISUALISATION. `id` IS THE IDENTITY of that chart and is what \
        prevents duplicates: to CHANGE an existing chart, reuse ITS EXACT id (from CURRENT \
        TRANSFORMS or from the `CHART id="…"` lines in the OUTLINE) and it is updated in place. \
        Use a NEW id only when you genuinely want an ADDITIONAL chart — a section may hold \
        several. mode only matters for a brand-new chart ("after"/"before" the heading, or \
        "replace" to swap the chart already sitting in that section).
- {"type":"remove","id":"<the chart/section id from the OUTLINE>"}   <-- PREFERRED, exact
  {"type":"remove","anchor":"<exact heading text of the section>","target":"chart"|"table"|"section"}
     (or {"type":"remove","target":"tabs"} to undo a tab layout)
     -> DELETES that element. `anchor` is ALWAYS the heading text; `target` is ALWAYS exactly one \
        of chart / table / section / tabs — never put a heading in `target`. Use target "chart" for a \
        diagram/figure/svg, "table" only for a data table, "section" for the whole section. Use this \
        whenever the user asks to remove or drop something; never "hide" things with CSS.
- {"type":"section","id":"<short-stable-id>","title":"<section title>","mode":"end"|"after"|"before","anchor":"<heading, if after/before>"}
     (reuse an existing SECTION id to REPLACE that added section; a new id adds another)
     -> APPENDS A NEW SECTION derived from the document: a glossary of terms the document uses, an \
        index of findings, a contents list, a consolidated summary table, a conditions checklist, an \
        owner roll-up — whatever the user asks for. Supply its markup in a matching ===HTML k=== block \
        (k = the 1-based order of section transforms). Rules for that markup: simple HTML only \
        (p, ul/ol/li, table/thead/tbody/tr/th/td, dl, strong, em, code, a, span, br — no script, no \
        style attributes, no images); it must be GROUNDED IN THE DOCUMENT — every id, figure, name, \
        status and quoted phrase must come from the document (for a glossary you may add the standard \
        meaning of a term the document uses, but never invent findings, numbers or verdicts); it is \
        ADDITIVE reference material — it must never restate a conclusion differently, contradict a \
        finding, or alter existing text.

DIAGRAMS (generic — heatmap, bar, matrix, timeline, donut, quadrant, whatever best fits the data + \
request): for EACH diagram transform, output its SVG in a matching ===SVG k=== block (k = the 1-based \
order of diagram transforms). Rules for the SVG:
- A SINGLE self-contained inline <svg> … </svg>. No <script>, no <foreignObject>, no external refs.
- Build it ONLY from data that appears in the TARGET SECTION (read the DELIVERABLE HTML for that \
  section). NEVER invent, round, or add data points. If the section has no quantitative data to plot, \
  say so in your reply and emit NO diagram.
- Mark every DATA-bearing <text> (category labels, values read from the section) with class="tx-data". \
  Leave chrome (title, legend keys, axis ticks) UNMARKED. A fidelity check compares your tx-data labels \
  against the section — anything not found there is flagged.
- Professional, board-appropriate; width ~860, height to fit.
- HOUSE STYLE — it must look like a designed board-pack exhibit, generous and confident, never thin or \
  cramped: use the full canvas width and give it real height (grow the height rather than crowding). \
  Typography bold and legible — title 13-14px weight 700, category/axis labels 11px weight 700, notes \
  10px weight 600; never shrink labels below 10px to make room, add space instead. Palette: navy \
  #0b2545 for markers and dark blocks, severity fills #d1fae5 (low) / #fef3c7 (moderate) / #fca5a5 \
  (high) / #b91c1c (critical), #d97706 accents, neutrals #e5e7eb / #cbd5e0, WHITE bold text on \
  saturated fills. Plot items as filled navy circles with a white stroke ring and a short white label; \
  use solid colour blocks with white titles for stage/gate flows, joined by matching-colour arrows. \
  Include a KEY panel beside the plot whenever a colour or id carries meaning. Keep every label fully \
  inside the canvas and non-overlapping — via layout and canvas size, NOT by shrinking type.
Transforms either RE-PRESENT existing content (tabs, diagram) or ADD grounded reference material \
(section). You may add whatever the user asks for — glossary, index, contents, summary table, \
checklist, roll-up — PROVIDED it is grounded in this document and additive: never rewrite, restate or \
contradict existing text, and never invent findings, figures or verdicts. Only reference sections shown \
in the OUTLINE. For purely visual instructions, omit the transforms block.

INCREMENTAL — THE TRANSFORMS BLOCK IS A PATCH, NOT A FULL REPLACEMENT. The transforms listed under \
CURRENT TRANSFORMS are already applied and PERSIST automatically. Emit ONLY what this instruction \
changes: a NEW transform, or a REPLACEMENT for one existing transform (same type + same anchor \
replaces it). NEVER re-emit or regenerate an unchanged transform — re-generating a diagram the user \
did not ask about wastes time and risks altering a chart they already accepted. To delete something, use the remove transform above. If the instruction \
touches only styling, omit the transforms block entirely.

OUTPUT FORMAT (in this order; omit any block you don't need):
<one or two plain sentences describing what you changed>
===CSS===
<the complete CSS override stylesheet (echo prior overrides you keep; may be empty)>
===TRANSFORMS===
<a JSON array, e.g. [{"type":"diagram","anchor":"3. Findings by Risk Domain","mode":"after","title":"Risk Heatmap by Domain"}] — omit if no structural change>
===SVG 1===
<the inline svg for the 1st diagram transform — repeat as ===SVG 2=== etc. for more>
===HTML 1===
<the markup for the 1st section transform — repeat as ===HTML 2=== etc. for more>
"""

_BEAUTIFY_MARKER = "===CSS==="
_TRANSFORMS_MARKER = "===TRANSFORMS==="
_SVG_SPLIT_RE = re.compile(r"===SVG\s*(\d+)===", re.IGNORECASE)
_ALLOWED_TX = {"tabs", "diagram", "section", "remove"}
_HTML_SPLIT_RE = re.compile(r"===HTML\s*(\d+)===", re.IGNORECASE)


def _sanitize_svg(svg):
    """Strip anything executable/external from agent SVG (defense-in-depth; the report
    iframe is script-sandboxed anyway). Returns the <svg>…</svg> slice."""
    s = svg or ""
    # Remove well-formed <script>…</script> / <foreignObject>…</foreignObject> blocks,
    # then strip ANY remaining <script>/<foreignObject> opening OR closing tag so a
    # malformed or unclosed tag (which the paired regex misses) can't survive. Matching a
    # lone opening tag is what prevents the classic "bad HTML filtering" bypass.
    s = re.sub(r"<script[\s\S]*?</script\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<foreignObject[\s\S]*?</foreignObject\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"</?\s*(script|foreignObject)\b[^>]*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(href|xlink:href)\s*=\s*(\"|')\s*javascript:[^\"']*\2", "", s, flags=re.IGNORECASE)
    m = re.search(r"<svg[\s\S]*</svg>", s, re.IGNORECASE)
    return (m.group(0) if m else "").strip()


def _sanitize_section_html(html):
    """Strip anything executable/risky from agent-authored section markup (the frontend
    sanitises again on insert; this is defence in depth)."""
    h = html or ""
    h = re.sub(r"<(script|style|iframe|object|embed|svg)[\s\S]*?</\1\s*>", "", h, flags=re.IGNORECASE)
    h = re.sub(r"<(script|style|iframe|object|embed|img|input)\b[^>]*/?>", "", h, flags=re.IGNORECASE)
    # Strip any leftover opening/closing tag of these elements that the paired-tag pass
    # missed (malformed / unclosed), so a broken <script ...> can't slip through.
    h = re.sub(r"</?\s*(script|style|iframe|object|embed|svg)\b[^>]*>", "", h, flags=re.IGNORECASE)
    h = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", h, flags=re.IGNORECASE)
    h = re.sub(r"(href)\s*=\s*(\"|')\s*javascript:[^\"']*\2", "", h, flags=re.IGNORECASE)
    if h.strip().startswith("```"):
        h = "\n".join(h.strip().split("\n")[1:])
    if h.strip().endswith("```"):
        h = "\n".join(h.strip().split("\n")[:-1])
    return h.strip()


def _parse_transforms(block, svg_blocks=None, html_blocks=None):
    """Parse + validate the transforms JSON against the catalog. `diagram` entries get
    their SVG attached from svg_blocks (1-based per diagram order) and sanitized.
    Diagram entries with no usable SVG are dropped."""
    b = (block or "").strip()
    if b.startswith("```"):
        b = "\n".join(b.split("\n")[1:])
    if b.endswith("```"):
        b = "\n".join(b.split("\n")[:-1])
    b = b.strip()
    if not b:
        return []
    try:
        arr = json.loads(b)
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    svg_blocks = svg_blocks or {}
    html_blocks = html_blocks or {}
    out = []
    diagram_i = 0
    section_i = 0
    for t in arr:
        if not isinstance(t, dict) or t.get("type") not in _ALLOWED_TX:
            continue
        if t["type"] == "tabs":
            item = {"type": "tabs"}
            if isinstance(t.get("params"), dict):
                item["params"] = t["params"]
            out.append(item)
        elif t["type"] == "remove":
            p = t.get("params") if isinstance(t.get("params"), dict) else {}
            def _clean(v):
                v = "" if v is None else str(v).strip()
                return "" if v.lower() in ("", "none", "null", "undefined") else v
            anchor = _clean(t.get("anchor")) or _clean(p.get("anchor"))
            raw_target = _clean(t.get("target")) or _clean(p.get("target"))
            target = raw_target.lower()
            allowed = {"chart", "diagram", "figure", "svg", "table", "section", "tabs"}
            # models sometimes put the HEADING in `target` — normalise that back to anchor
            if target and target not in allowed:
                if not anchor:
                    anchor = raw_target
                target = "chart"
            if target in ("diagram", "figure", "svg"):
                target = "chart"
            if not target:
                target = "chart"
            if not anchor and target != "tabs":
                continue  # nothing identifiable to delete
            out.append({"type": "remove", "params": {"anchor": anchor, "target": target}})
        elif t["type"] == "section":
            section_i += 1
            p = t.get("params") if isinstance(t.get("params"), dict) else {}
            html = _sanitize_section_html(html_blocks.get(section_i) or html_blocks.get(0) or p.get("html") or "")
            if not html:
                continue
            mode = (t.get("mode") or p.get("mode") or "end")
            if mode not in ("end", "after", "before"):
                mode = "end"
            out.append({"type": "section", "params": {
                "title": t.get("title") or p.get("title") or "",
                "anchor": t.get("anchor") or p.get("anchor") or "",
                "id": t.get("id") or p.get("id") or "",
                "mode": mode, "html": html}})
        elif t["type"] == "diagram":
            diagram_i += 1
            p = t.get("params") if isinstance(t.get("params"), dict) else {}
            anchor = t.get("anchor") or p.get("anchor") or ""
            mode = (t.get("mode") or p.get("mode") or "after")
            if mode not in ("after", "before", "replace"):
                mode = "after"
            title = t.get("title") or p.get("title") or ""
            svg = _sanitize_svg(svg_blocks.get(diagram_i) or svg_blocks.get(0) or p.get("svg") or "")
            if not (anchor and svg):
                continue  # a diagram without a target or SVG is unusable
            out.append({"type": "diagram", "params": {"anchor": anchor, "mode": mode, "title": title, "svg": svg}})
    return out


def _parse_beautify(full):
    """Split the model output into {reply, css, transforms?} on the markers. Diagram SVGs
    live in ===SVG k=== blocks after the transforms JSON."""
    idx = full.find(_BEAUTIFY_MARKER)
    if idx == -1:
        stripped = full.strip()
        tix = stripped.find(_TRANSFORMS_MARKER)
        if tix != -1:  # transforms-only turn (no CSS block)
            reply = stripped[:tix].strip() or "Updated the layout."
            tx = _parse_transforms_section(stripped[tix + len(_TRANSFORMS_MARKER):])
            return {"reply": reply, "css": "", "transforms": tx}
        if "{" in stripped and "}" in stripped:
            return {"reply": "Updated the design.", "css": stripped}
        return {"reply": stripped, "css": ""}
    reply = full[:idx].strip()
    rest = full[idx + len(_BEAUTIFY_MARKER):]
    transforms = None
    tix = rest.find(_TRANSFORMS_MARKER)
    if tix != -1:
        transforms = _parse_transforms_section(rest[tix + len(_TRANSFORMS_MARKER):])
        rest = rest[:tix]
    css = rest.strip()
    if css.startswith("```"):
        css = "\n".join(css.split("\n")[1:])
    if css.endswith("```"):
        css = "\n".join(css.split("\n")[:-1])
    out = {"reply": reply or "Updated the design.", "css": css.strip()}
    if transforms is not None:
        out["transforms"] = transforms
    return out


def _parse_transforms_section(section):
    """Given the text AFTER the ===TRANSFORMS=== marker, split off the JSON array and any
    ===SVG k=== blocks, then parse+validate."""
    # split HTML blocks first, then SVG blocks out of the leading remainder
    hparts = _HTML_SPLIT_RE.split(section)
    html_blocks = {}
    for i in range(1, len(hparts) - 1, 2):
        try:
            html_blocks[int(hparts[i])] = hparts[i + 1]
        except Exception:
            pass
    parts = _SVG_SPLIT_RE.split(hparts[0])
    tx_json = parts[0]
    svg_blocks = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            svg_blocks[int(parts[i])] = parts[i + 1]
        except Exception:
            pass
    return _parse_transforms(tx_json, svg_blocks, html_blocks)


def _beautify_messages(history, message, current_html, current_css, outline="", current_transforms=None):
    """Build the turn. The full HTML is sent as INPUT context (cheap/fast); the
    model outputs only a small CSS override block (+ optional transforms JSON)."""
    msgs = []
    for h in (history or [])[-6:]:
        role = h.get("role")
        text = h.get("text") or h.get("content") or ""
        if role in ("user", "assistant") and text:
            msgs.append({"role": role, "content": text})
    user = (
        "DOCUMENT OUTLINE (headings + tables you may target with transforms):\n"
        + (outline or "(outline unavailable)") + "\n\n"
        "DELIVERABLE HTML (context only — do NOT rewrite it):\n```html\n" + (current_html or "") + "\n```\n\n"
        "CURRENT CSS OVERRIDES (already applied; empty if first turn):\n```css\n" + (current_css or "") + "\n```\n\n"
        "CURRENT TRANSFORMS — already applied and PERSISTENT (svg omitted; do NOT re-emit these):\n"
        + (json.dumps([
            {k: v for k, v in ({**t, **(t.get("params") or {})}).items()
             if k in ("type", "anchor", "mode", "title")}
            for t in (current_transforms or []) if isinstance(t, dict)
        ]) or "[]") + "\n\n"
        "INSTRUCTION: " + (message or "")
    )
    msgs.append({"role": "user", "content": user})
    return msgs


def converse_beautify(history, message, current_html, current_css, outline="", current_transforms=None):
    """Non-streaming design turn (sync/testing). Returns {reply, css, transforms?}."""
    client = boto3.client("bedrock-runtime", region_name=REGION)
    resp = client.invoke_model(
        modelId=BUILDER_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16000,
            "system": BEAUTIFY_SYSTEM,
            "messages": _beautify_messages(history, message, current_html, current_css, outline, current_transforms),
        }),
    )
    result = json.loads(resp["body"].read())
    if result.get("stop_reason") == "max_tokens":
        raise ValueError("Beautify overrides were truncated (max_tokens) — narrow the instruction.")
    return _parse_beautify(result["content"][0]["text"])


def converse_beautify_stream(history, message, current_html, current_css, client_id, job_id, outline="", current_transforms=None):
    """Streaming design turn: streams the reply as BEAUTIFY_DELTA (holding back the
    marker), then pushes a final BEAUTIFY_RESULT with {reply, css, transforms?}. Mirrors
    converse_stream. Only the small CSS/transforms blocks are produced (fast) — the
    document itself is never regenerated."""
    apigw, conns = _resolve_connections(client_id)
    client = boto3.client("bedrock-runtime", region_name=REGION)
    try:
        resp = client.invoke_model_with_response_stream(
            modelId=BUILDER_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 16000,
                "system": BEAUTIFY_SYSTEM,
                "messages": _beautify_messages(history, message, current_html, current_css, outline, current_transforms),
            }),
        )
        full = ""
        streamed = 0
        marker_hit = False
        hold = len(_BEAUTIFY_MARKER) - 1
        for event in resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                continue
            d = json.loads(chunk["bytes"])
            if d.get("type") != "content_block_delta":
                continue
            piece = (d.get("delta") or {}).get("text", "")
            if not piece:
                continue
            full += piece
            if marker_hit:
                continue
            idx = full.find(_BEAUTIFY_MARKER)
            if idx == -1:
                safe = len(full) - hold
                if safe > streamed:
                    _post(apigw, conns, {"type": "BEAUTIFY_DELTA", "jobId": job_id, "text": full[streamed:safe]})
                    streamed = safe
            else:
                if idx > streamed:
                    _post(apigw, conns, {"type": "BEAUTIFY_DELTA", "jobId": job_id, "text": full[streamed:idx]})
                    streamed = idx
                marker_hit = True
        parsed = _parse_beautify(full)
        _post(apigw, conns, {"type": "BEAUTIFY_RESULT", "jobId": job_id, **parsed})
    except Exception as e:
        _post(apigw, conns, {"type": "BEAUTIFY_RESULT", "jobId": job_id, "error": str(e)[:300]})


SPOTLIGHT_SYSTEM = """You spotlight sections of a report to answer a user's FOCUS. \
You are given the DELIVERABLE TEXT and a FOCUS (what the user wants to see/understand). \
Pick the 1-5 most relevant sections to walk through, in order.

For each section:
- target: a SHORT, VERBATIM, DISTINCTIVE snippet copied EXACTLY from the deliverable text — \
a unique 4-10 word phrase, or a specific figure/code. It MUST appear character-for-character \
in the deliverable text (never paraphrase, never a long sentence).
- note: one short sentence — why this matters / what to see.

Output ONLY JSON, no prose, no fences:
{"sections":[{"target":"<verbatim snippet>","note":"<one sentence>"}]}
"""


def _spotlight_sections(focus, deliverable_text):
    """Concierge-style: pick 1-5 verbatim snippets + notes for the focus. Only
    returns targets that actually occur in the deliverable text (guarantees the
    frontend matcher can anchor them)."""
    text = (deliverable_text or "")[:60000]
    client = boto3.client("bedrock-runtime", region_name=REGION)
    resp = client.invoke_model(
        modelId=BUILDER_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1500,
            "system": SPOTLIGHT_SYSTEM,
            "messages": [{"role": "user", "content":
                "DELIVERABLE TEXT:\n" + text + "\n\nFOCUS: " + (focus or "the most important points")}],
        }),
    )
    raw = json.loads(resp["body"].read())["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j != -1:
        raw = raw[i:j + 1]
    data = json.loads(raw)
    norm = lambda s: " ".join((s or "").lower().split())
    ntext = norm(text)
    out = []
    for s in (data.get("sections") or []):
        if not isinstance(s, dict):
            continue
        tgt = (s.get("target") or "").strip()
        if tgt and norm(tgt) in ntext:   # enforce verbatim / matchable
            out.append({"target": tgt, "note": (s.get("note") or "").strip()})
    return out[:5]


# ── Conversational MISSION capture (run-prep) ───────────────────────────────────
# Mirrors the builder's converse engine, but instead of DESIGNING an ecosystem it
# helps the user shape the MISSION for a run of an ALREADY-BUILT ecosystem —
# honouring its declared inputs and any uploaded files — then converges on
# {mission, inputs} ready to launch. Reuses the CONVERSE_DELTA/RESULT WS events
# (jobId-keyed) so the frontend mission-chat shares the builder's plumbing.
_MISSION_MARKER = "===MISSION==="

MISSION_SYSTEM = """You are the Tessera Run Planner — a conversational partner that helps a user
define the MISSION for a single run of an ALREADY-BUILT ecosystem. You do NOT design or modify the
ecosystem; you turn the user's intent into a clear, runnable mission for THIS ecosystem.

You are given `ecosystem` (name, description, declared inputs, outcome) and any `attachments` the
user uploaded for this run.

Rules:
- BUILD A BRIEFING CHECKLIST. On your first turn, derive from the ecosystem's declared inputs (and its
  purpose) the things you must know to brief THIS ecosystem — every REQUIRED declared input plus any
  essential scope/constraint. Put them in `mission_state.checklist` as a list of
  { "id", "label", "resolved": false, "value": null, "source": null }. Acknowledge the user's goal in
  one line on the first turn.
- SOURCE FROM THE ATTACHMENTS FIRST. The user uploaded documents precisely so the assessment reads
  them — do NOT ask the user to hand-type information that is present in the files. For each checklist
  item, FIRST try to resolve it from the attachment excerpts; when a document states it, set
  resolved=true, record the value, and set `source` to the filename (e.g. "solution-overview.pdf").
  Extracting a value a document states is SOURCING, not inventing.
- CONFIRM, DON'T INTERROGATE. When attachments resolve items, present the extracted values back to the
  user in ONE consolidated confirmation — a short list of item -> value -> source — and ask them to
  CONFIRM or CORRECT. Do NOT ask for file-covered items one at a time. Treat file-sourced items as
  provisionally resolved pending that single confirmation.
- ASK ONLY FOR GENUINE GAPS, one at a time: items NO attachment covers, that the attachments leave
  ambiguous, or where attachments conflict. If the user says "take it from the files" (or names a
  file), resolve the item from the files and cite the source rather than pushing back for a typed answer.
- NEVER INVENT. Never record a value that is neither stated in an attachment nor given by the user. If
  it is in neither, it is a gap — ask, or if the user defers, record "(left to the ecosystem's
  discretion)".
- USE OPTIONS for closed choices (e.g. a level -> low / medium / high) via next_question.options; the
  user can still type their own.
- CONVERGENCE IS GATED ON THE CHECKLIST. converged=true ONLY when EVERY checklist item is resolved —
  each either confirmed/corrected by the user or explicitly deferred by the user. File-sourced values
  must have been shown to the user for confirmation before you converge. When converged, write
  `mission` as a clear imperative and fill `inputs` with the declared-input values (file-sourced values
  included), noting each value's source in `mission_state.notes`. Convergence is verified by the
  platform, so a premature converged=true will be rejected.

Respond in TWO parts:
PART 1 — a short prose reply to the user (acknowledgement / the one question / confirmation).
PART 2 — on its own line the marker ===MISSION=== then a JSON object:
{
  "next_question": { "id": "...", "text": "...", "options": ["...","..."] } | null,
  "mission_state": {
    "goal": "...",
    "checklist": [ { "id": "...", "label": "...", "resolved": false, "value": null, "source": null } ],
    "notes": "...",
    "inputs": { "<declared_input_id>": "<value>" }
  },
  "converged": false,
  "mission": "<final imperative when converged, else null>",
  "inputs": { "<declared_input_id>": "<value>" }
}
Emit ONLY these two parts."""


# Attachment enrichment for the run planner (see _enrich_attachments).
_ATTACH_TEXT_EXTS = {"md", "txt", "csv", "json", "yaml", "yml", "xml", "html", "htm", "log", "tsv", "ini", "toml", "rtf"}
_ATTACH_DOC_EXTS = {"pdf", "doc", "docx"}  # excerpted via <key>.extracted.txt (file-classify)
_ATTACH_EXCERPT_BYTES = 8000
# Total excerpt budget ACROSS all attachments per turn. Without this, N uploaded
# files each contribute up to _ATTACH_EXCERPT_BYTES on EVERY turn, ballooning the
# model's input as the chat grows and starving the (bounded) output budget — the
# root of the "final turn truncates / mission never finalizes" failure. The full
# files are always given to the run itself; the planner needs enough to SOURCE the
# declared inputs from the documents and present them for confirmation.
_ATTACH_EXCERPT_TOTAL_BYTES = 40000


def _enrich_attachments(attachments):
    """Let the run-planner actually *see* uploaded files (it only receives S3
    refs otherwise). For each attachment we add, when available:
      - `summary`/`tag` from the file-classify `.meta.json` (if it has run yet), and
      - `excerpt` — decoded leading text for text/data formats (reliable even
        before classification finishes, since the PUT completed before this turn).
    Binary files without a summary get a short note. Best-effort: any read error
    just leaves the bare {name, ref} so the turn still works.

    Excerpts share a per-turn TOTAL byte budget (_ATTACH_EXCERPT_TOTAL_BYTES) and
    shrink to fit as more files are attached, so many uploads can't bloat the
    model input (which was starving the output budget and truncating the final
    mission turn)."""
    s3 = boto3.client("s3", region_name=REGION)
    out = []
    atts = attachments or []
    # Divide the total budget across the text-like attachments (min 1 to avoid /0).
    text_atts = sum(1 for a in atts if isinstance(a, dict)
                    and (a.get("name", "").rsplit(".", 1)[-1].lower() if "." in a.get("name", "") else "") in (_ATTACH_TEXT_EXTS | _ATTACH_DOC_EXTS))
    per_file_budget = min(_ATTACH_EXCERPT_BYTES, max(1500, _ATTACH_EXCERPT_TOTAL_BYTES // max(1, text_atts)))
    remaining = _ATTACH_EXCERPT_TOTAL_BYTES
    for a in atts:
        name = a.get("name", "")
        ref = a.get("ref") or a.get("content") or ""
        item = {"name": name, "ref": ref}
        if not isinstance(ref, str) or not ref.startswith("s3://"):
            out.append(item); continue
        bucket, _, key = ref[5:].partition("/")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        # 1) classification summary (if file-classify already wrote meta)
        try:
            meta = json.loads(s3.get_object(Bucket=bucket, Key=key + ".meta.json")["Body"].read())
            if meta.get("summary"): item["summary"] = meta["summary"]
            if meta.get("tag"): item["tag"] = meta["tag"]
        except Exception:
            pass
        # 2) text excerpt — bounded by the shared budget. Text formats are read
        #    directly; PDFs/docs are read from <key>.extracted.txt (full text
        #    written by file-classify) so the planner can SOURCE inputs from them.
        budget = min(per_file_budget, remaining)
        read_key = None
        if ext in _ATTACH_TEXT_EXTS:
            read_key = key
        elif ext in _ATTACH_DOC_EXTS:
            read_key = key + ".extracted.txt"
        if read_key and budget > 0:
            try:
                raw = s3.get_object(Bucket=bucket, Key=read_key, Range=f"bytes=0-{budget - 1}")["Body"].read()
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    item["excerpt"] = text
                    remaining -= len(raw)
                    if len(raw) >= budget:
                        item["excerpt_truncated"] = True
            except Exception as e:
                print(f"attachment excerpt read failed for {read_key}: {e}")
        if "excerpt" not in item and "summary" not in item:
            item["note"] = "binary file — full contents are analyzed by the ecosystem at run time"
        out.append(item)
    return out


def _mission_turn_messages(history, message, mission_state, eco_context, attachments):
    return [{"role": "user", "content": json.dumps({
        "ecosystem": eco_context or {},
        "attachments": _enrich_attachments(attachments),
        "mission_state": mission_state or {},
        "conversation_so_far": history or [],
        "user_message": message or "",
    })}]


def _parse_mission_turn(text):
    """Split a two-part run-planner response into {reply, next_question,
    mission_state, converged, mission, inputs}. PART 2 follows ===MISSION===."""
    if _MISSION_MARKER in text:
        reply, _, rest = text.partition(_MISSION_MARKER)
    else:
        reply, rest = text, ""
    reply = reply.strip()
    rest = rest.strip()
    if rest.startswith("```"):
        rest = "\n".join(rest.split("\n")[1:])
        if rest.endswith("```"):
            rest = "\n".join(rest.split("\n")[:-1])
    structured = {}
    parse_error = False
    if rest:
        try:
            structured = json.loads(rest)
        except json.JSONDecodeError:
            structured = {}
            # PART-2 was present but unparseable — almost always a response cut
            # off mid-JSON (max_tokens). Flag it so the caller can surface an
            # explicit error and PRESERVE prior state instead of returning an
            # empty mission_state (which blanks the user's answers panel).
            parse_error = True
    turn = {
        "reply": reply or "(no reply)",
        "next_question": structured.get("next_question"),
        "mission_state": structured.get("mission_state") or {},
        "converged": bool(structured.get("converged")),
        "mission": structured.get("mission"),
        "inputs": structured.get("inputs") or {},
        "_parse_error": parse_error,
    }
    # Deterministic sufficiency gate: the model tends to converge eagerly, so we
    # ENFORCE that every briefing-checklist item is resolved before allowing
    # convergence — a premature converged=true is overridden here, not trusted.
    checklist = (turn["mission_state"] or {}).get("checklist") or []
    if isinstance(checklist, list):
        unresolved = [c for c in checklist if isinstance(c, dict) and not c.get("resolved")]
        if turn["converged"] and unresolved:
            turn["converged"] = False
            turn["mission"] = None
            if not turn.get("next_question"):
                nxt = unresolved[0]
                turn["next_question"] = {
                    "id": nxt.get("id", "q"),
                    "text": f"Before we launch — {nxt.get('label', 'one more detail')}?",
                }
    return turn


def converse_mission(history, message, mission_state, eco_context, attachments):
    """Non-streaming mission turn (sync/testing). Returns the parsed turn."""
    client = boto3.client("bedrock-runtime", region_name=REGION)
    resp = client.invoke_model(
        modelId=BUILDER_MODEL_ID,
        contentType="application/json", accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "system": MISSION_SYSTEM,
            "messages": _mission_turn_messages(history, message, mission_state, eco_context, attachments),
        }),
    )
    result = json.loads(resp["body"].read())
    if result.get("stop_reason") == "max_tokens":
        raise ValueError("Mission turn was truncated (max_tokens).")
    return _parse_mission_turn(result["content"][0]["text"])


def converse_mission_stream(history, message, mission_state, eco_context, attachments, client_id, job_id):
    """Streaming mission turn — same CONVERSE_DELTA/RESULT contract as the builder."""
    apigw, conns = _resolve_connections(client_id)
    client = boto3.client("bedrock-runtime", region_name=REGION)
    try:
        resp = client.invoke_model_with_response_stream(
            modelId=BUILDER_MODEL_ID,
            contentType="application/json", accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 16384,
                "system": MISSION_SYSTEM,
                "messages": _mission_turn_messages(history, message, mission_state, eco_context, attachments),
            }),
        )
        full = ""
        streamed = 0
        marker_hit = False
        stop_reason = None
        hold = len(_MISSION_MARKER) - 1
        for event in resp["body"]:
            chunk = event.get("chunk")
            if not chunk:
                continue
            d = json.loads(chunk["bytes"])
            # Anthropic-on-Bedrock streams the final stop_reason on the
            # message_delta event. Capture it so we can DETECT truncation
            # (max_tokens) instead of silently shipping a cut-off response.
            if d.get("type") == "message_delta":
                stop_reason = ((d.get("delta") or {}).get("stop_reason")) or stop_reason
            if d.get("type") != "content_block_delta":
                continue
            piece = (d.get("delta") or {}).get("text", "")
            if not piece:
                continue
            full += piece
            if marker_hit:
                continue
            idx = full.find(_MISSION_MARKER)
            if idx == -1:
                safe = len(full) - hold
                if safe > streamed:
                    _post(apigw, conns, {"type": "CONVERSE_DELTA", "jobId": job_id, "text": full[streamed:safe]})
                    streamed = safe
            else:
                if idx > streamed:
                    _post(apigw, conns, {"type": "CONVERSE_DELTA", "jobId": job_id, "text": full[streamed:idx]})
                    streamed = idx
                marker_hit = True
        turn = _parse_mission_turn(full)
        # If the model was CUT OFF (max_tokens) or the PART-2 JSON didn't parse,
        # do NOT ship an empty turn — that blanks the user's answers panel and
        # produces no mission (the exact "stuck on the final question" failure).
        # Surface an explicit, recoverable error and ECHO BACK the prior
        # mission_state so the accumulated answers are preserved.
        if stop_reason == "max_tokens" or turn.get("_parse_error"):
            _post(apigw, conns, {
                "type": "CONVERSE_RESULT", "jobId": job_id,
                "error": ("The planner's reply was too long and got cut off before it finished, "
                          "so the mission couldn't be finalized. Your answers are preserved — "
                          "send once more to retry the final step."),
                "truncated": True,
                "reply": turn.get("reply") or "…",
                "mission_state": mission_state or {},
                "next_question": None,
                "converged": False,
                "mission": None,
                "inputs": (mission_state or {}).get("inputs") or {},
            })
            return
        _post(apigw, conns, {"type": "CONVERSE_RESULT", "jobId": job_id, **turn})
    except Exception as e:
        _post(apigw, conns, {"type": "CONVERSE_RESULT", "jobId": job_id, "error": str(e)[:300]})


def _compile_quorum_design_to_yaml(ds, intent):
    """Serialize a 'panel' (Quorum-archetype) design into an ecosystem YAML — no agent DAG."""
    import re as _re
    q = ds.get("quorum") or {}
    empan = q.get("empanelment") or {}
    select = empan.get("select") or {}
    delib = q.get("deliberation") or {}
    verdict = q.get("verdict") or {}
    name = (intent.get("name") or intent.get("goal") or "defensibility-panel")[:80]
    slug = _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "defensibility-panel"
    inputs = ds.get("inputs") or intent.get("inputs") or [
        {"id": "decision", "type": "text", "label": "Decision under scrutiny (what is proposed)", "required": True},
        {"id": "context", "type": "text", "label": "Context (who/what is affected, jurisdiction, data)", "required": False},
    ]
    eco = {
        "ecosystem": {
            "name": name,
            "description": intent.get("goal", ""),
            "version": "1.0",
            "inputs": inputs,
            "outcome": {"kind": "report"},
        },
        "name": slug,                      # top-level (used for the run's decision_ref)
        "strategy": "quorum",
        "quorum": {
            "empanelment": {
                "persona_source": "/catalog/scrutineer/",
                "select": {
                    "min": int(select.get("min", 3)),
                    "max": int(select.get("max", 6)),
                    # GENERIC archetype: the panel is assembled AT RUN TIME from the actual
                    # decision. No baked-in required members or dimensions — empty unless the
                    # author explicitly constrained them at build time.
                    "must_include": select.get("must_include") or [],
                    "diversity": select.get("diversity") or [],
                },
                "completeness_adversary": bool(empan.get("completeness_adversary", True)),
                "justify_exclusions": bool(empan.get("justify_exclusions", True)),
            },
            "deliberation": {
                "max_rounds": int(delib.get("max_rounds", 2)),
                "turn_policy": delib.get("turn_policy", "moderated"),
                "escalation": bool(delib.get("escalation", True)),
                "preserve_dissent": bool(delib.get("preserve_dissent", True)),
            },
            "verdict": {
                "scale": verdict.get("scale") or ["defensible", "defensible_with_conditions", "indefensible"],
                "must_state": verdict.get("must_state") or ["against_whom_it_fails", "indefensible_elements", "remediation"],
            },
        },
        "quality": {"gate": {"block_below": (ds.get("quality") or {}).get("block_below", "defensible_with_conditions")}},
    }
    eco["category"] = (str(intent.get("category") or "Risk & Compliance").strip())[:40]
    eco["tags"] = [str(t).strip().lower()[:24] for t in (intent.get("tags") or []) if str(t).strip()][:4] or ["quorum", "defensibility"]
    return yaml.safe_dump(eco, sort_keys=False, default_flow_style=False)


def _compile_design_to_yaml(ds):
    """Serialize an agreed DesignState into an ecosystem YAML string (deterministic)."""
    intent = ds.get("intent") or {}
    # Quorum archetype (flag-guarded): a scrutiny/judgment PANEL — no agent DAG.
    if _quorum_enabled() and (ds.get("strategy") == "quorum" or intent.get("approach") == "panel"):
        return _compile_quorum_design_to_yaml(ds, intent)
    agents = ds.get("agents") or []
    if not agents:
        raise ValueError("design has no agents yet")
    outcome = ds.get("outcome") or {"kind": "report"}
    quality = ds.get("quality") or {}

    # The platform's outcome validator only accepts these artifact formats.
    # Multi-file bundles (e.g. "zip") aren't supported yet, so coerce any
    # unsupported format to markdown (a single consolidated doc) and keep the
    # producing drafter's output_format in lockstep — otherwise save fails.
    _ALLOWED_FMT = {"csv", "docx", "html", "json", "markdown", "pdf", "png", "yaml", "bundle"}

    def _ext(fmt):
        return {"markdown": "md", "bundle": "zip"}.get(fmt, fmt)

    def _coerce_save_as(save_as, fmt):
        base = save_as.rsplit(".", 1)[0] if save_as and "." in save_as else (save_as or "deliverable")
        return f"{base}.{_ext(fmt)}"

    coerced = {}  # agent_id -> (fmt, save_as) to keep the drafter in sync
    for art in (outcome.get("artifacts") or []):
        fmt = art.get("format")
        if fmt not in _ALLOWED_FMT:
            new_fmt = "markdown"
            art["format"] = new_fmt
            art["save_as"] = _coerce_save_as(art.get("save_as"), new_fmt)
            if art.get("agent"):
                coerced[art["agent"]] = (new_fmt, art["save_as"])
    for a in agents:
        of = a.get("output_format")
        if isinstance(of, dict):
            if a.get("id") in coerced:
                nf, nsa = coerced[a["id"]]
                of["type"], of["save_as"] = nf, nsa
            elif of.get("type") not in _ALLOWED_FMT:
                of["type"] = "markdown"
                of["save_as"] = _coerce_save_as(of.get("save_as"), "markdown")

    eco = {
        "ecosystem": {
            "name": (intent.get("name") or intent.get("goal") or "ecosystem")[:80],
            "description": intent.get("goal", ""),
            "version": "1.0",
        },
        "strategy": "orchestrated",
        "report": True,
    }
    # Discovery metadata (top-level) — the ecosystems page groups by category
    # and searches tags. Author-owned; persisted as-is by save-ecosystem.
    cat = str(intent.get("category") or "").strip()
    if cat:
        eco["category"] = cat[:40]
    tags = [str(t).strip().lower()[:24] for t in (intent.get("tags") or []) if str(t).strip()][:4]
    if tags:
        eco["tags"] = tags
    ch = str(quality.get("challenger", "per_level")).lower()
    qblock = {}
    if ch == "off":
        qblock["challenger"] = False
    elif ch == "per_level":
        qblock["challenger"] = "per_level"
        qblock["challenger_gate"] = {"block_below": (quality.get("block_below") or "ADEQUATE")}
    else:
        qblock["challenger"] = True
    qblock["grounding"] = bool(quality.get("grounding", True))
    gates = quality.get("gates") or {}
    if gates:
        qblock["gates"] = {k: gates.get(k, "ask") for k in ("citation", "format", "grounding", "output_kind")}
    eco["quality"] = qblock
    if (outcome.get("kind") == "file_artifact") and (outcome.get("artifacts")):
        eco["outcome"] = {"kind": "file_artifact", "artifacts": outcome["artifacts"]}
    else:
        eco["outcome"] = {"kind": "report"}
    eco["entry_point"] = agents[0].get("id", "agent_1")
    clean_agents = []
    for a in agents:
        ca = {
            "id": a.get("id"), "name": a.get("name", a.get("id")),
            "swarm": a.get("swarm", ""),
            "depends_on": a.get("depends_on", []) or [],
            "tools": a.get("tools", []) or [],
            "prompt": a.get("prompt", ""),
        }
        if a.get("output_kind"):
            ca["output_kind"] = a["output_kind"]
        # Deterministic role tagging: an agent that lists a live-research tool AND
        # carries upstream deps but is left untagged would compile (save-ecosystem)
        # as a SYNTHESIZER and get web_search budget 0 — silently unable to search.
        # If it clearly IS a researcher (research tool + deps), tag it so it keeps
        # its deps AND gets a research budget. Belt-and-suspenders with the
        # save-ecosystem budget backstop.
        elif ca["depends_on"] and ({"web_search", "exa_search"} & set(ca["tools"])):
            ca["output_kind"] = "researcher"
        if isinstance(a.get("output_format"), dict):
            ca["output_format"] = a["output_format"]
        clean_agents.append(ca)

    # Memory — carry the design's namespaces; otherwise inject a per-run shared
    # dossier so findings persist for downstream agents and any memory_write is
    # AUTHORIZED (prevents the "agent is not authorized to write" dead-end seen
    # when an eco ships with no shared_namespaces). Scoping (verified against the
    # runtime): a SINGLE-SEGMENT path (e.g. /xxx_findings) is isolated to the
    # current run (reads only see this run's session — no cross-run/eco leakage);
    # write/read [all] + auto_persist => every agent publishes + consults without
    # manual memory_write calls. Use /catalog/<x>/ only for cross-run knowledge.
    mem = ds.get("memory") or {}
    ns = mem.get("shared_namespaces") or []
    has_synth = any((a.get("depends_on") or []) for a in agents)
    if not ns and len(agents) >= 2 and has_synth:
        import re as _re
        slug = _re.sub(r"[^a-z0-9]+", "_", (intent.get("name") or intent.get("domain") or "shared").lower()).strip("_")[:32] or "shared"
        ns = [{
            "name": f"{slug}_findings",
            "path": f"/{slug}_findings",   # single-segment => per-run isolated
            "write": ["all"], "read": ["all"], "auto_persist": True,
        }]
    if ns:
        eco["memory"] = {"strategies": (mem.get("strategies") or ["semantic"]), "shared_namespaces": ns}

    eco["agents"] = clean_agents
    return yaml.safe_dump(eco, sort_keys=False, default_flow_style=False)


# ── Eval-proposal pass (spec §8, Phase 5) ────────────────────────────────────
# After an ecosystem CONVERGES, propose an eval suite for SME curation: metric
# bindings + candidate golden cases + custom judge rubrics, streamed CONVERSE-
# style. The SME curates/labels; on approval the result compiles into the
# `evaluation:` block and the golden set is written to S3. Eco-AGNOSTIC — the
# proposal is grounded in the eco's OWN declared agents/contracts/decision block.

_EVAL_PROPOSAL_SYSTEM = """You are Tessera's EVALUATION DESIGN agent. Given a CONVERGED \
multi-agent ecosystem design, you propose an OFFLINE EVALUATION SUITE for a human SME to curate.

You are given: the ecosystem YAML (agents, output contracts, any `decision:` block, inputs) and a \
HEURISTIC SEED proposal derived from its structure. ENRICH the seed — do not discard it. Aim for a \
RICH, MULTI-METRIC suite (typically 4-8 metrics), grounded ONLY in THIS ecosystem's declared \
outputs (never invent domain fields):
1. metric bindings — which OUTPUT fields matter + the fitting GENERIC primitive \
(categorical | numeric_tolerance | set_recall | citation_resolves | abstention | bedrock_judge). \
Prefer: a `categorical` metric on each decision/verdict/disposition field the agents emit; a \
`categorical` metric checking TEMPORAL-REGIME correctness where the eco selects a rule/regime by an \
as-of date; a `citation_resolves` faithfulness metric (field "*"); an `abstention` metric for the \
"correctly REFER/HOLD/decline-to-decide when evidence is insufficient" case; and one or more \
`bedrock_judge` metrics (builtin faithfulness/completeness, and a custom_rubric) for NARRATIVE \
outputs.
2. candidate golden cases — one per archetype (happy_path, edge, adversarial, insufficient_evidence). \
Fill `inputs` to MATCH the eco's declared inputs schema and a plausible `expected` for each declared \
output field. Mark every case labels.reviewed=false — the SME confirms/labels them.
3. custom judge rubrics — for NARRATIVE outputs only, as bedrock_judge custom_rubric text.

HARD RULES (a violation makes the whole suite invalid and it will be rejected):
- ANY metric with `scorer: abstention` MUST include an `expected_value` — the EXACT output value that \
means the system correctly abstained/refused/held (e.g. an abstain-like outcome the eco itself \
declares, such as "REFER", "HOLD-FOR-INFO", "ESCALATE"). NEVER emit an abstention metric without \
`expected_value`.
- Every deterministic metric (categorical/numeric_tolerance/set_recall) MUST name a `field`.
- Every metric `id` in `thresholds` MUST exist in `metrics`.
- Use ONLY the generic scorer/keys above. Domain terms appear ONLY as author VALUES (field names / \
expected values from the eco), NEVER as metric keys.

Return PART 1 = a short prose explanation of the suite, then a line ===EVAL=== then PART 2 = a JSON \
object {"evaluation": {...generic block...}, "golden_cases": [...]}."""


def propose_eval_suite(config):
    """Heuristic seed proposal (works with no LLM) — grounded in the eco structure."""
    from tessera_platform.evaluation.propose import propose_evaluation
    return propose_evaluation(config)


def _normalize_eval_proposal(config, proposal):
    """Repair the common, RECOVERABLE LLM omissions BEFORE validation so a single
    slipped field never collapses a rich suite to the thin seed (the observed
    'abstention metric requires expected_value' self-reject). Eco-AGNOSTIC — we
    only infer missing values from the proposal's OWN golden cases / the eco's
    declared decision block, never inject a domain term. Anything we cannot repair
    is DROPPED (with its threshold) so the remaining rich suite still validates."""
    ev = proposal.get("evaluation")
    if not isinstance(ev, dict):
        return proposal
    metrics = ev.get("metrics") if isinstance(ev.get("metrics"), list) else []
    cases = proposal.get("golden_cases") if isinstance(proposal.get("golden_cases"), list) else []
    dec = config.get("decision") or {}
    outcomes = [str(o) for o in ((dec.get("decision") or {}).get("outcomes") or [])]
    abstain_like = next((o for o in outcomes if str(o).upper() in
                         ("REFER", "REFUSE", "ABSTAIN", "NO_GO", "ESCALATE", "HOLD",
                          "HOLD-FOR-INFO", "HOLD_FOR_INFO")), None)

    def _infer_abstain_value(field):
        # 1) a golden case that should abstain → its expected value for this field
        for c in cases:
            exp = (c.get("expected") or {}) if isinstance(c, dict) else {}
            lbl = (c.get("labels") or {}) if isinstance(c, dict) else {}
            if (exp.get("should_abstain") is True or lbl.get("archetype") == "insufficient_evidence") \
                    and field and exp.get(field) not in (None, ""):
                return exp.get(field)
        # 2) the decision block's abstain-like outcome
        return abstain_like

    kept, dropped_ids = [], set()
    seen = set()
    for m in metrics:
        if not isinstance(m, dict):
            continue
        mid, scorer = m.get("id"), m.get("scorer")
        if not mid or mid in seen:
            dropped_ids.add(mid); continue
        if scorer == "abstention" and m.get("expected_value") in (None, ""):
            inferred = _infer_abstain_value(m.get("field"))
            if inferred in (None, ""):
                dropped_ids.add(mid); continue        # unrepairable → drop
            m["expected_value"] = inferred
        if scorer in ("categorical", "exact_match", "numeric_tolerance", "set_recall", "coverage") \
                and not m.get("field"):
            dropped_ids.add(mid); continue            # deterministic needs a field
        seen.add(mid); kept.append(m)
    ev["metrics"] = kept
    thr = ev.get("thresholds")
    if isinstance(thr, dict) and dropped_ids:
        ev["thresholds"] = {k: v for k, v in thr.items() if k not in dropped_ids}
    return proposal


def _llm_enrich_eval_proposal(config, seed):
    """Ask the builder model to enrich the seed. On any failure, return the seed
    marked llm_enriched=false (a DRAFT for SME curation, not a gate — so this is
    not a silent quality degrade)."""
    try:
        client = boto3.client("bedrock-runtime", region_name=REGION)
        user = ("ECOSYSTEM YAML:\n" + yaml.dump(config, sort_keys=False)[:12000] +
                "\n\nHEURISTIC SEED:\n" + json.dumps(seed, indent=2)[:6000])
        resp = client.invoke_model(
            modelId=BUILDER_MODEL_ID, contentType="application/json", accept="application/json",
            body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 6000,
                             "system": _EVAL_PROPOSAL_SYSTEM,
                             "messages": [{"role": "user", "content": user}]}))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        reply, _, jtxt = text.partition("===EVAL===")
        proposal = json.loads(jtxt[jtxt.find("{"): jtxt.rfind("}") + 1])
        # Repair recoverable omissions (esp. abstention missing expected_value)
        # BEFORE validation so a slip doesn't collapse the whole rich suite.
        proposal = _normalize_eval_proposal(config, proposal)
        # validate the enriched block parses; if not, fall back to seed
        from tessera_platform.evaluation.config import parse_evaluation_block
        parse_evaluation_block({"evaluation": proposal.get("evaluation", {}),
                                "agents": config.get("agents", [])})
        proposal.setdefault("golden_cases", seed.get("golden_cases", []))
        proposal["reply"] = reply.strip()
        proposal["llm_enriched"] = True
        return proposal
    except Exception as e:
        seed = dict(seed)
        seed["reply"] = ("Proposed a heuristic evaluation suite from the design structure "
                         f"(LLM enrichment unavailable: {str(e)[:120]}). Please curate.")
        seed["llm_enriched"] = False
        return seed


def converse_eval_proposal_stream(config, client_id, job_id):
    """Stream the eval-suite proposal to the SME (EVAL_PROPOSAL_RESULT)."""
    apigw, conns = _resolve_connections(client_id)
    try:
        seed = propose_eval_suite(config)
        proposal = _llm_enrich_eval_proposal(config, seed)
        _post(apigw, conns, {"type": "EVAL_PROPOSAL_RESULT", "jobId": job_id, **proposal})
    except Exception as e:
        _post(apigw, conns, {"type": "EVAL_PROPOSAL_RESULT", "jobId": job_id, "error": str(e)[:300]})


def apply_eval_proposal(eco_id, approved, config):
    """Compile an SME-approved proposal into the `evaluation:` block + write the
    golden set to S3 (spec §5.3). Returns {evaluation, golden_set_uri, version}.
    Fails loudly on a malformed approval."""
    from tessera_platform.evaluation.propose import compile_approved_proposal
    import datetime as _dt
    eval_block, golden_jsonl = compile_approved_proposal(config, approved)
    version = approved.get("golden_set_version") or _dt.datetime.now(
        _dt.timezone.utc).strftime("v%Y%m%dT%H%M%SZ")
    golden_uri = ""
    if RUNS_BUCKET:
        key = f"evaluations/{eco_id}/{version}/cases.jsonl"
        boto3.client("s3").put_object(Bucket=RUNS_BUCKET, Key=key,
                                      Body=golden_jsonl.encode(), ContentType="application/jsonl")
        golden_uri = f"s3://{RUNS_BUCKET}/{key}"
        eval_block["golden_set"] = golden_uri
    return {"evaluation": eval_block, "golden_set_uri": golden_uri, "version": version}


def handler(event, context):
    # Async self-invocation path
    if event.get("_async"):
        return run_async(event["_async"])
    # Sync entry from Amazon API Gateway
    body = json.loads(event.get("body", "{}"))
    description = body.get("description", "")
    client_id = body.get("clientId", "")

    # Conversational builder turn (mode=converse). Reuses the same async +
    # WebSocket plumbing as the one-shot suggest; pushes CONVERSE_RESULT.
    if body.get("mode") == "converse":
        message = body.get("message", "")
        history = body.get("history", []) or []
        design_state = body.get("design_state", {}) or {}
        # D.7: domain-aware builder. The frontend passes the chosen domain (id + name
        # + catalogs); inject it so the builder mounts it + wires agents to its
        # inherited catalogs rather than inventing new ones.
        if body.get("domain"):
            design_state["_domain"] = body["domain"]
        if not client_id:
            try:
                return _resp(200, converse(history, message, design_state))
            except Exception as e:
                return _resp(500, {"error": str(e)})
        job_id = str(uuid.uuid4())
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"_async": {
                "mode": "converse", "message": message, "history": history,
                "design_state": design_state, "clientId": client_id, "jobId": job_id,
            }}).encode(),
        )
        return _resp(202, {"jobId": job_id, "status": "pending"})

    # Eval-proposal pass (spec §8): propose an eval suite for a CONVERGED eco.
    #   mode=eval_proposal  -> async, streams EVAL_PROPOSAL_RESULT
    #   mode=eval_approve   -> sync, compiles approved proposal + writes golden set
    if body.get("mode") == "eval_proposal":
        config = body.get("config") or {}
        if not config:
            return _resp(400, {"error": "eval_proposal requires the converged `config`"})
        if not client_id:
            try:
                seed = propose_eval_suite(config)
                return _resp(200, _llm_enrich_eval_proposal(config, seed))
            except Exception as e:
                return _resp(500, {"error": str(e)})
        job_id = str(uuid.uuid4())
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=LAMBDA_FUNCTION_NAME, InvocationType="Event",
            Payload=json.dumps({"_async": {"mode": "eval_proposal", "config": config,
                                           "clientId": client_id, "jobId": job_id}}).encode())
        return _resp(202, {"jobId": job_id, "status": "pending"})

    if body.get("mode") == "eval_approve":
        eco_id = body.get("eco_id") or ""
        approved = body.get("approved") or {}
        config = body.get("config") or {}
        if not eco_id or not approved:
            return _resp(400, {"error": "eval_approve requires `eco_id` and `approved`"})
        try:
            return _resp(200, apply_eval_proposal(eco_id, approved, config))
        except Exception as e:
            return _resp(500, {"error": str(e)})

    # Conversational MISSION capture (mode=mission) — shapes the run mission for an
    # already-built ecosystem, file-aware. Same async + WebSocket plumbing.
    if body.get("mode") == "mission":
        message = body.get("message", "")
        history = body.get("history", []) or []
        mission_state = body.get("mission_state", {}) or {}
        eco_context = body.get("eco_context", {}) or {}
        attachments = body.get("attachments", []) or []
        if not client_id:
            try:
                return _resp(200, converse_mission(history, message, mission_state, eco_context, attachments))
            except Exception as e:
                return _resp(500, {"error": str(e)})
        job_id = str(uuid.uuid4())
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"_async": {
                "mode": "mission", "message": message, "history": history,
                "mission_state": mission_state, "eco_context": eco_context,
                "attachments": attachments, "clientId": client_id, "jobId": job_id,
            }}).encode(),
        )
        return _resp(202, {"jobId": job_id, "status": "pending"})

    # Conversational BEAUTIFY (mode=beautify) — WYSIWYG deliverable design studio.
    # The frontend sends the CURRENT html + one instruction; we stream the reply and
    # push a BEAUTIFY_RESULT with the full revised html. Same async + WebSocket plumbing.
    # Concierge-style spotlight: pick verbatim sections + notes for a focus, over the
    # deliverable text. Sync (small/fast JSON) — the studio steps through the sections.
    if body.get("mode") == "spotlight":
        try:
            return _resp(200, {"sections": _spotlight_sections(body.get("focus", ""), body.get("deliverableText", ""))})
        except Exception as e:
            return _resp(500, {"error": str(e)})

    if body.get("mode") == "beautify":
        message = body.get("message", "")
        history = body.get("history", []) or []
        current_html = body.get("currentHtml", "") or ""
        current_css = body.get("currentCss", "") or ""
        outline = body.get("outline", "") or ""
        current_transforms = body.get("currentTransforms", []) or []
        if not client_id:
            try:
                return _resp(200, converse_beautify(history, message, current_html, current_css, outline, current_transforms))
            except Exception as e:
                return _resp(500, {"error": str(e)})
        job_id = str(uuid.uuid4())
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"_async": {
                "mode": "beautify", "message": message, "history": history,
                "currentHtml": current_html, "currentCss": current_css,
                "outline": outline, "currentTransforms": current_transforms,
                "clientId": client_id, "jobId": job_id,
            }}).encode(),
        )
        return _resp(202, {"jobId": job_id, "status": "pending"})

    if not description:
        return _resp(400, {"error": "description required"})

    if not client_id:
        # Fallback: fully sync (will hit 29s timeout for complex generations).
        # This is only for direct Lambda invocations / testing.
        try:
            result = generate(description)
            return _resp(200, result)
        except Exception as e:
            return _resp(500, {"error": str(e)})

    # Async path: kick off self-invocation, return immediately with jobId.
    job_id = str(uuid.uuid4())
    lambda_client = boto3.client("lambda", region_name=REGION)
    lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({
            "_async": {"description": description, "clientId": client_id, "jobId": job_id}
        }).encode(),
    )
    return _resp(202, {"jobId": job_id, "status": "pending"})


def run_async(payload):
    """Background worker. Generates the ecosystem (or runs a conversational
    design turn) and pushes the result via WebSocket."""
    client_id = payload["clientId"]
    job_id = payload["jobId"]
    if payload.get("mode") == "converse":
        converse_stream(payload.get("history", []), payload.get("message", ""),
                        payload.get("design_state", {}), client_id, job_id)
        return {"statusCode": 200}
    if payload.get("mode") == "mission":
        converse_mission_stream(payload.get("history", []), payload.get("message", ""),
                                payload.get("mission_state", {}), payload.get("eco_context", {}),
                                payload.get("attachments", []), client_id, job_id)
        return {"statusCode": 200}
    if payload.get("mode") == "beautify":
        converse_beautify_stream(payload.get("history", []), payload.get("message", ""),
                                 payload.get("currentHtml", ""), payload.get("currentCss", ""),
                                 client_id, job_id,
                                 payload.get("outline", ""), payload.get("currentTransforms", []))
        return {"statusCode": 200}
    if payload.get("mode") == "eval_proposal":
        converse_eval_proposal_stream(payload.get("config", {}), client_id, job_id)
        return {"statusCode": 200}
    try:
        result = generate(payload["description"])
        push_to_client(client_id, {
            "type": "SUGGEST_RESULT",
            "jobId": job_id,
            "yaml": result["yaml"],
            "config": result["config"],
            "outputs": result.get("outputs", {"kind": "report", "artifacts": []}),
            "quality": result.get("quality", {}),
        })
    except Exception as e:
        push_to_client(client_id, {
            "type": "SUGGEST_RESULT",
            "jobId": job_id,
            "error": str(e),
        })
    return {"statusCode": 200}


def generate(description):
    """Run the Amazon Bedrock call and parse the YAML. Raises on truncation or invalid output."""
    # Amazon Bedrock client for ecosystem generation
    client = boto3.client("bedrock-runtime", region_name=REGION)
    prompt = PROMPT_TEMPLATE.format(description=description)
    # Quorum archetype authoring — appended ONLY when the platform has it enabled, so the
    # builder's behaviour is byte-identical when the flag is off (docs/spec-quorum-archetype.md).
    if os.environ.get("QUORUM_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        prompt += _QUORUM_ADDENDUM
    resp = client.invoke_model(
        modelId=BUILDER_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 16384,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    result = json.loads(resp["body"].read())

    # Detect truncation explicitly — no silent fallback to a partial YAML.
    stop_reason = result.get("stop_reason")
    if stop_reason == "max_tokens":
        out_tokens = result.get("usage", {}).get("output_tokens", "?")
        raise ValueError(
            f"Model output was truncated at {out_tokens} tokens (max_tokens=8192). "
            f"Response did not complete. Increase max_tokens or reduce the description scope."
        )

    yaml_text = result["content"][0]["text"].strip()
    # Strip markdown fences if the model added them despite instructions
    if yaml_text.startswith("```"):
        yaml_text = "\n".join(yaml_text.split("\n")[1:])
    if yaml_text.endswith("```"):
        yaml_text = "\n".join(yaml_text.split("\n")[:-1])

    try:
        config = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Model returned invalid YAML: {str(e)[:300]}")

    if not isinstance(config, dict) or "agents" not in config:
        raise ValueError("Model output did not contain a valid ecosystem config (missing 'agents').")

    return {"yaml": yaml_text, "config": config, "outputs": _extract_outputs(config), "quality": _extract_quality(config)}


def _extract_quality(config):
    """Surface the recommended quality posture for the wizard's Quality step.

    Returns {challenger, block_below, grounding, report, gates:{...}} with
    sensible defaults when the model omitted a field.
    """
    q = config.get("quality") or {}
    if not isinstance(q, dict):
        q = {}
    raw_ch = q.get("challenger", "per_level")
    # Normalise challenger: accept bool (legacy) or string mode.
    if raw_ch is True:
        challenger = "final"
    elif raw_ch is False:
        challenger = "off"
    else:
        challenger = str(raw_ch).lower()
        if challenger not in ("off", "final", "per_level"):
            challenger = "per_level"
    cg = q.get("challenger_gate") or {}
    block_below = str(cg.get("block_below", "ADEQUATE")).upper()
    if block_below not in ("STRONG", "ADEQUATE", "WEAK"):
        block_below = "ADEQUATE"
    raw_gates = q.get("gates") or {}
    gates = {}
    for g in ("citation", "format", "grounding", "output_kind"):
        m = str(raw_gates.get(g, "ask")).lower()
        gates[g] = m if m in ("ask", "warn", "halt") else "ask"
    return {
        "challenger": challenger,
        "block_below": block_below,
        "grounding": bool(q.get("grounding", True)),
        "report": bool(config.get("report", q.get("report", True))),
        "gates": gates,
    }


def _extract_outputs(config):
    """Summarise the ecosystem's declared deliverables for the confirmation UI.

    Returns {kind, artifacts: [{id, agent, format, save_as, download_label}]}.
    Reads outcome from either ecosystem.outcome or top-level outcome. For
    report-only ecosystems, artifacts is empty.
    """
    outcome = (config.get("ecosystem") or {}).get("outcome") or config.get("outcome") or {}
    if not isinstance(outcome, dict):
        return {"kind": "report", "artifacts": []}
    kind = outcome.get("kind", "report")
    artifacts = []
    for a in (outcome.get("artifacts") or []):
        if not isinstance(a, dict):
            continue
        artifacts.append({
            "id": a.get("id", ""),
            "agent": a.get("agent", ""),
            "format": a.get("format", ""),
            "save_as": a.get("save_as", ""),
            "download_label": a.get("download_label", ""),
        })
    return {"kind": kind, "artifacts": artifacts}


def push_to_client(client_id: str, payload: dict):
    """Find all WebSocket connections for clientId and push the payload."""
    if not CONNECTIONS_TABLE or not WS_ENDPOINT:
        return
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(CONNECTIONS_TABLE)
    resp = table.scan(
        FilterExpression="clientId = :cid",
        ExpressionAttributeValues={":cid": client_id},
    )
    items = resp.get("Items", [])
    if not items:
        print(f"No WebSocket connection found for clientId={client_id}")
        return
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
    data = json.dumps(payload).encode()
    for item in items:
        try:
            apigw.post_to_connection(ConnectionId=item["connectionId"], Data=data)
        except apigw.exceptions.GoneException:
            try:
                table.delete_item(Key={"connectionId": item["connectionId"]})
            except Exception:
                pass


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*")},
        "body": json.dumps(body),
    }
