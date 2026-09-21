"""Domain-seed (Phase D.1, bottom-up) — infer a DOMAIN's canonical knowledge
taxonomy from one or more existing ecosystems.

For each agent it applies one rule: does the agent produce a durable, mission-
INVARIANT *entity* fact (a thing that exists independent of any single run — a
property, a company, a person, a strata scheme, a control, a sold transaction)
→ it should persist to an entity-keyed catalog; or a run-specific *conclusion*
(a value, ranking, verdict, recommendation, strategy) → it must NOT be cataloged.

It then aggregates the entity types into a proposed domain taxonomy and surfaces:
  - gaps  : entity-producing agents whose entity type has NO catalog declared
  - leaks : agents that write to a catalog but actually produce conclusions

Returns the proposal as JSON for the Domain Builder UI. Read-only (no writes).
"""
import os
import json
import re
import boto3
import yaml

BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
MODEL_ID = os.environ.get("DOMAIN_SEED_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")

s3 = boto3.client("s3")
_brt = boto3.client("bedrock-runtime", region_name=REGION)

SYSTEM = (
    "You are Tessera's Domain Taxonomy seeder. Given the agents of one or more "
    "ecosystems in the SAME domain, infer the DOMAIN's canonical knowledge "
    "taxonomy — the durable, reusable entity types a catalog should hold so that "
    "FUTURE runs and sibling ecosystems compound knowledge instead of re-deriving it.\n\n"
    "Apply ONE classification rule to each agent:\n"
    "  ENTITY producer — emits a durable, mission-INVARIANT fact about a thing that "
    "exists independently of any single run (a property, suburb, company, person, "
    "strata scheme, control, a settled/observed transaction). These SHOULD persist to "
    "an entity-keyed catalog namespace /catalog/<type>/<entity-key>.\n"
    "  CONCLUSION producer — emits a run-specific judgment about the mission (a "
    "valuation/price, a risk ranking, a verdict, a recommendation, a strategy/plan, a "
    "review). These must NEVER be cataloged; they belong in the run output.\n\n"
    "Per agent assign a disposition:\n"
    "  use      — entity type already declared as a catalog in this ecosystem.\n"
    "  promote  — entity type is durable + domain-general but NO catalog is declared "
    "for it (a missing, high-reuse catalog that should be added to the domain).\n"
    "  localize — entity type is specific to this one ecosystem, not a domain concept.\n"
    "  none     — conclusion producer; no catalog.\n\n"
    "Then consolidate the entity types into the domain taxonomy (dedup; one entry per "
    "type with a stable entity-key pattern like {address}, {sp_number}, {name}-{postcode}). "
    "Mark each taxonomy entry status=existing (a catalog already declared) or "
    "status=proposed (newly inferred — a gap).\n\n"
    "Return STRICT JSON only (no prose, no code fences) with this shape:\n"
    "{\n"
    '  "domain": {"suggested_id": "kebab-case", "suggested_name": "...", "description": "..."},\n'
    '  "taxonomy": [{"type": "strata-scheme", "key": "{sp_number}", "description": "...", "status": "existing|proposed"}],\n'
    '  "agents": [{"id": "...", "name": "...", "disposition": "use|promote|localize|none", "entity_type": "strata-scheme or null", "reason": "one line"}],\n'
    '  "gaps": [{"agent": "...", "entity_type": "...", "why": "durable entity with no catalog"}],\n'
    '  "leaks": [{"agent": "...", "namespace": "/catalog/...", "why": "writes a conclusion to a catalog"}]\n'
    "}"
)


def _load_ecosystem(eco_id: str) -> dict:
    body = s3.get_object(Bucket=BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()
    return yaml.safe_load(body)


def _declared_catalogs(eco: dict) -> list:
    """Catalog types already declared in the ecosystem's memory.shared_namespaces."""
    out = []
    for ns in (eco.get("memory", {}) or {}).get("shared_namespaces", []) or []:
        path = (ns.get("path") or "").strip("/")
        # /catalog/properties/ -> properties ; /catalog/x/y -> x/y
        if path.startswith("catalog/"):
            out.append(path[len("catalog/"):].rstrip("/"))
    return out


def _agent_brief(eco: dict) -> list:
    briefs = []
    for a in eco.get("agents", []) or []:
        mem = a.get("memory", {}) or {}
        writable = mem.get("writable") or []
        # shared_namespaces at agent level (older shape) also counts as writable intent
        for ns in mem.get("shared_namespaces", []) or []:
            p = ns.get("path") if isinstance(ns, dict) else ns
            if p:
                writable.append(p)
        briefs.append({
            "id": a.get("id"),
            "name": a.get("name", ""),
            "output_kind": a.get("output_kind") or a.get("agent_role") or "",
            "depends_on": a.get("depends_on") or [],
            "writes_to": writable,
            "prompt_excerpt": (a.get("prompt") or "")[:1400],
        })
    return briefs


def _seed_one(eco_id: str) -> dict:
    eco = _load_ecosystem(eco_id)
    payload = {
        "ecosystem_id": eco_id,
        "ecosystem_name": eco.get("name", eco_id),
        "declared_catalogs": _declared_catalogs(eco),
        "agents": _agent_brief(eco),
    }
    proposal = _converse_json(SYSTEM, payload, max_tokens=4000)
    proposal["seeded_from"] = [eco_id]
    return proposal


SYSTEM_DESCRIBE = (
    "You are Tessera's Domain Taxonomy designer. Given a domain NAME and DESCRIPTION, "
    "propose the canonical, entity-keyed catalog taxonomy for the domain — the durable, "
    "reusable entity TYPES a shared catalog should hold so every ecosystem in this domain "
    "compounds knowledge instead of re-deriving it.\n\n"
    "Only include ENTITY types — durable, mission-INVARIANT facts about things that exist "
    "independently of any single run (e.g. a property, suburb, company, person, strata "
    "scheme, control, a settled transaction). NEVER include conclusions (valuations, "
    "rankings, verdicts, recommendations, strategies) — those belong in a run, not a catalog.\n\n"
    "Give each type a stable entity-key pattern ({address}, {sp_number}, {name}-{postcode}, ...). "
    "Propose the 6–8 MOST IMPORTANT, durable entity types — a FOCUSED canonical set, not an "
    "exhaustive list. Prefer the entities with the highest cross-ecosystem reuse. "
    "Keep each \"description\" to ONE short sentence (max ~12 words). "
    "Also refine a kebab-case id + a clean name + one-paragraph description for the domain.\n\n"
    "Return STRICT JSON only (no prose, no code fences):\n"
    "{\n"
    '  "domain": {"suggested_id": "kebab-case", "suggested_name": "...", "description": "..."},\n'
    '  "taxonomy": [{"type": "strata-scheme", "key": "{sp_number}", "description": "...", "status": "proposed"}],\n'
    '  "agents": [], "gaps": [], "leaks": []\n'
    "}"
)


def _loads_salvage(cand: str) -> dict:
    """Parse JSON, tolerating a response truncated mid-generation (e.g. token cap):
    trims to the last complete object and closes open arrays/objects."""
    try:
        return json.loads(cand)
    except json.JSONDecodeError:
        idx = cand.rfind("}")
        if idx == -1:
            raise
        s = cand[:idx + 1]
        s += "]" * max(0, s.count("[") - s.count("]"))
        s += "}" * max(0, s.count("{") - s.count("}"))
        return json.loads(s)


def _converse_json(system: str, payload: dict, max_tokens: int = 4000) -> dict:
    resp = _brt.converse(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": json.dumps(payload)}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return _loads_salvage(m.group(0) if m else text)


def _describe(name: str, description: str) -> dict:
    """Top-down: propose a taxonomy from a domain name + description (no ecosystem)."""
    proposal = _converse_json(SYSTEM_DESCRIBE, {"name": name, "description": description}, max_tokens=1600)
    proposal.setdefault("agents", [])
    proposal.setdefault("gaps", [])
    proposal.setdefault("leaks", [])
    proposal["seeded_from"] = []
    proposal["origin"] = "describe"
    return proposal


# ── Conversational domain builder (Phase 1 — multi-turn, synchronous per turn) ───
# Mirrors suggest-ecosystem's `converse` contract: the model replies with PART 1
# prose + a ===DOMAIN=== marker + PART 2 JSON {next_question, design, converged}.
# design accumulates the taxonomy + conclusions anti-list across turns; the UI
# renders next_question and, on convergence, an editable review of design.
SYSTEM_CONVERSE = (
    "You are Tessera's Domain Taxonomy design partner, in a MULTI-TURN dialogue with a "
    "human. Your job is to converge on a domain's canonical, entity-keyed catalog "
    "taxonomy — the durable, reusable entity TYPES a shared catalog should hold so every "
    "ecosystem in this domain compounds knowledge instead of re-deriving it — plus the "
    "anti-list of conclusions that must NEVER be cataloged.\n\n"
    "THE ONE RULE you apply to every candidate: is it a durable, mission-INVARIANT ENTITY "
    "fact (a thing that exists independent of any single run — a regulatory figure, a "
    "standard, a company, a property, a person) → CATALOG it; or a run-specific "
    "CONCLUSION (a valuation, ranking, verdict, recommendation, strategy, a client's "
    "output) → it belongs in the anti-list, NEVER a catalog.\n\n"
    "DIALOGUE RULES:\n"
    "- ONE QUESTION AT A TIME. Ask EXACTLY ONE question per turn — the most important "
    "still-open point about scope, coverage, sources of truth, or an ambiguous "
    "entity-vs-conclusion call. Provide `options` when it is a closed choice.\n"
    "- NEVER dump a finished taxonomy on turn one. Start from the user's description, "
    "propose a SMALL provisional set, and refine it through the conversation.\n"
    "- Give each entity type a stable key pattern ({measure}-{fy}, {standard_id}, "
    "{name}-{postcode}, ...). Keep each description to ONE short sentence.\n"
    "- Prefer a FOCUSED canonical set (typically 4-8 types) with the highest cross-"
    "ecosystem reuse — not an exhaustive list.\n"
    "- converged=true ONLY when the scope is settled, every proposed type is a genuine "
    "durable entity, and the anti-list captures the obvious conclusions. When the user "
    "signals they're happy (or asks you to finish), converge.\n\n"
    "OUTPUT FORMAT — your response has EXACTLY two parts:\n"
    "PART 1: a short, friendly reply to the user — AT MOST 2 sentences, plain prose, no "
    "JSON. This is the only text shown to the user.\n"
    "PART 2: a new line with the marker\n"
    "===DOMAIN===\n"
    "then a SINGLE JSON object (no markdown fences):\n"
    "{\n"
    '  "next_question": { "id": "...", "text": "...", "options": ["...", "..."] } | null,\n'
    '  "design": {\n'
    '    "domain": {"suggested_id": "kebab-case", "suggested_name": "...", "description": "..."},\n'
    '    "taxonomy": [{"type": "regulatory-figure", "key": "{measure}-{fy}", "description": "...", "status": "proposed"}],\n'
    '    "conclusions_never_cataloged": ["..."]\n'
    "  },\n"
    '  "converged": false\n'
    "}\n"
    "Always echo the FULL current design each turn (not a diff), reflecting the latest "
    "user input. Set next_question to null when converged=true."
)

_DOMAIN_MARKER = "===DOMAIN==="


def _converse_text(system: str, messages: list, max_tokens: int = 2500) -> str:
    resp = _brt.converse(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])


def _turn_input(history: list, message: str, design_state: dict) -> list:
    return [{"role": "user", "content": [{"text": json.dumps({
        "current_design": design_state or {},
        "conversation_so_far": history or [],
        "user_message": message or "",
    })}]}]


def _parse_domain_turn(text: str) -> dict:
    """Split a two-part builder response into {reply, next_question, design, converged}.
    PART 1 = prose reply; PART 2 (after ===DOMAIN===) = JSON."""
    text = (text or "").strip()
    if _DOMAIN_MARKER in text:
        reply, _, rest = text.partition(_DOMAIN_MARKER)
    else:
        reply, rest = text, ""
    reply = reply.strip()
    rest = re.sub(r"^```(?:json)?|```$", "", rest.strip(), flags=re.MULTILINE).strip()
    structured = {}
    if rest:
        m = re.search(r"\{.*\}", rest, re.DOTALL)
        try:
            structured = _loads_salvage(m.group(0) if m else rest)
        except Exception:
            structured = {}
    design = structured.get("design") or {}
    # Normalize taxonomy items to the {type,key,description,status} contract the UI expects.
    tax = []
    for t in design.get("taxonomy") or []:
        if not isinstance(t, dict) or not t.get("type"):
            continue
        tax.append({"type": t.get("type"), "key": t.get("key") or "{id}",
                    "description": t.get("description", ""), "status": t.get("status") or "proposed"})
    design["taxonomy"] = tax
    design.setdefault("conclusions_never_cataloged", [])
    return {
        "reply": reply or "(no reply)",
        "next_question": structured.get("next_question"),
        "design": design,
        "converged": bool(structured.get("converged")),
    }


def _converse(history: list, message: str, design_state: dict) -> dict:
    """Non-streaming domain-design turn (Phase 1). Returns the parsed turn."""
    text = _converse_text(SYSTEM_CONVERSE, _turn_input(history, message, design_state))
    turn = _parse_domain_turn(text)
    turn["origin"] = "converse"
    return turn


SYSTEM_MATCH = (
    "You match a new ecosystem to the most relevant EXISTING domain. Given the new "
    "ecosystem's description and a list of candidate domains (id, name, description), "
    "return the best match (or none if nothing fits well). Judge by subject-matter "
    "overlap — would the new ecosystem reuse the domain's catalogs?\n"
    "Return STRICT JSON only:\n"
    '{ "best": {"id": "<domain-id or null>", "confidence": "high|medium|low", "reason": "one line"},\n'
    '  "ranked": [{"id": "<domain-id>", "score": 0-100, "reason": "one line"}] }'
)


def _list_domains() -> list:
    out, token = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": "domains/"}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get("Contents", []) or []:
            if not obj["Key"].endswith(".yaml"):
                continue
            # Only top-level `domains/<id>.yaml` are domains; skip subdir assets like
            # `domains/<id>/catalog.yaml` and anything under `domains/schemas/`.
            if "/" in obj["Key"][len("domains/"):]:
                continue
            try:
                d = yaml.safe_load(s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()) or {}
                out.append({"id": d.get("domain") or obj["Key"][8:-5], "name": d.get("name", ""),
                            "description": d.get("description", ""),
                            "taxonomy": [t.get("type") for t in d.get("taxonomy", []) or []]})
            except Exception:
                continue
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return out


def _match(text: str) -> dict:
    """Match a new-ecosystem description to the most relevant existing domain."""
    candidates = _list_domains()
    if not candidates:
        return {"best": {"id": None, "confidence": "low", "reason": "no domains exist yet"}, "ranked": [], "candidates": []}
    res = _converse_json(SYSTEM_MATCH, {"new_ecosystem": text, "candidate_domains": candidates}, max_tokens=800)
    # attach the candidate taxonomy summaries so the UI can show inherited catalogs
    by_id = {c["id"]: c for c in candidates}
    res["candidates"] = candidates
    best = (res.get("best") or {}).get("id")
    if best and best in by_id:
        res["best"]["catalogs"] = by_id[best]["taxonomy"]
    return res


# ── Ingest authoring (spec-declarative-ingest.md §6) ─────────────────────────────
# Shared platform primitives the wizard can reuse (kept in sync with
# tessera_platform.catalog.formats.BUILDERS and connectors.CONNECTORS).
_KNOWN_BUILDERS = ["attack", "kev", "veris", "owasp", "nist_ai_rmf", "wiz", "nist_800_53", "atlas"]
_KNOWN_TRANSPORTS = ["http_json", "http_csv", "http_yaml", "http_html", "github_contents",
                     "sdmx", "s3", "rest_paginated", "static"]

SYSTEM_INGEST = (
    "You author a DECLARATIVE catalog-ingest for a Tessera domain (no code is written; "
    "the platform executes your declaration). Given the domain + its taxonomy, decide "
    "whether ingest is necessary and, if so, propose the ingest artifacts.\n\n"
    "Per taxonomy entity type, classify the SOURCE OF TRUTH:\n"
    "  ingest-fed — populated from an EXTERNAL authoritative feed that refreshes "
    "(e.g. a regulator/standards body). Needs a source declaration.\n"
    "  run-fed    — accreted by the domain's OWN ecosystem runs as agents discover "
    "entities (no external feed). A writable catalog namespace, NOT ingest.\n"
    "  none       — it's a run conclusion; never cataloged.\n"
    "If NO type is ingest-fed, return needs_ingest=false with an empty ingest.\n\n"
    "For each ingest-fed type, propose one or more sources. Prefer the most AUTHORITATIVE, "
    "freely accessible source (government / standards-body / official API). Choose transport "
    f"from {_KNOWN_TRANSPORTS} and normalize.mode:\n"
    f"  builder — a known standard format; set normalize.builder from {_KNOWN_BUILDERS}.\n"
    "  map     — a structured feed (JSON/CSV/SDMX); give a JMESPath 'select' + 'fields'.\n"
    "  agent   — an unstructured HTML/PDF page; give 'guidance' + 'key'. (schema-driven, grounded)\n"
    "Structured JSON/CSV/SDMX => map; HTML/PDF/free text => agent; known standard => builder.\n"
    "Also propose a records schema ($defs per ingest-fed type, with required fields + a key).\n"
    "Every proposed source URL must be a REAL authoritative endpoint; if unsure, mark "
    "fetch.url with a '#VERIFY' suffix so the wizard probes it before saving.\n\n"
    "Return STRICT JSON only (no prose, no code fences):\n"
    "{\n"
    '  "needs_ingest": true,\n'
    '  "classification": [{"type": "...", "disposition": "ingest-fed|run-fed|none", "why": "one line"}],\n'
    '  "ingest": {"sources": [{"name": "...", "produces": ["..."], '
    '"fetch": {"transport": "...", "url": "..."}, '
    '"normalize": {"mode": "map|builder|agent", "entity_type": "...", "builder": "...", '
    '"key": "{...}", "select": "...", "fields": {}, "guidance": "..."}}], '
    '"refresh": {"schedule": "rate(7 days)", "sanity_drop": 0.5}, "synthesis": "none"},\n'
    '  "schema": {"x-tessera-keys": {}, "$defs": {}}\n'
    "}"
)


def _generate_ingest(payload: dict) -> dict:
    """Wizard step: from a domain + taxonomy, classify ingest-necessity and (if needed)
    generate the declarative ingest block + records schema for human review. Read-only —
    the UI validates (probe-fetch/dry-run) + saves. Mirrors _describe/_seed."""
    proposal = _converse_json(SYSTEM_INGEST, {
        "domain": payload.get("domain") or payload.get("name") or "",
        "description": payload.get("description", ""),
        "taxonomy": payload.get("taxonomy") or [],
        "known_builders": _KNOWN_BUILDERS,
        "known_transports": _KNOWN_TRANSPORTS,
    }, max_tokens=3000)
    proposal.setdefault("needs_ingest", bool(proposal.get("ingest", {}).get("sources")))
    proposal.setdefault("classification", [])
    proposal.setdefault("ingest", {"sources": [], "synthesis": "none"})
    proposal["origin"] = "generate_ingest"
    return proposal


def handler(event, context):
    headers = {
        "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        "Content-Type": "application/json",
    }
    try:
        body = event.get("body")
        req = json.loads(body) if isinstance(body, str) else (body or {})
        # Conversational domain builder (Phase 1): multi-turn, synchronous per turn.
        if req.get("mode") == "converse":
            return {"statusCode": 200, "headers": headers, "body": json.dumps(_converse(
                req.get("history", []) or [], req.get("message", ""), req.get("design_state", {}) or {}))}
        # Ingest authoring: classify ingest-necessity + generate declarative artifacts.
        if req.get("generate_ingest"):
            return {"statusCode": 200, "headers": headers, "body": json.dumps(_generate_ingest(req))}
        # Domain match: which existing domain best fits a new ecosystem description?
        if req.get("match"):
            return {"statusCode": 200, "headers": headers, "body": json.dumps(_match(req["match"]))}
        # Top-down: build the taxonomy from a name + description (no ecosystem).
        desc = req.get("describe") or req.get("description")
        if desc and not (req.get("ecosystem_id") or req.get("ecosystem_ids")):
            return {"statusCode": 200, "headers": headers,
                    "body": json.dumps(_describe(req.get("name", ""), desc))}
        # Bottom-up: seed from an existing ecosystem.
        eco_ids = req.get("ecosystem_ids") or ([req["ecosystem_id"]] if req.get("ecosystem_id") else [])
        if not eco_ids:
            return {"statusCode": 400, "headers": headers,
                    "body": json.dumps({"error": "provide ecosystem_id / ecosystem_ids[] (seed) or name+description (describe)"})}
        proposal = _seed_one(eco_ids[0])
        proposal["origin"] = "seed"
        return {"statusCode": 200, "headers": headers, "body": json.dumps(proposal)}
    except s3.exceptions.NoSuchKey:
        return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "ecosystem not found"})}
    except Exception as e:
        return {"statusCode": 500, "headers": headers,
                "body": json.dumps({"error": f"{type(e).__name__}: {str(e)[:400]}"})}
