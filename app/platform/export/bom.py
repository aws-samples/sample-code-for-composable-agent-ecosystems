"""Ecosystem Bill-of-Materials (BoM) analyzer.

Read-only, offline dependency-closure computer for a single ecosystem: classifies
tools (platform-inline vs business/Gateway), computes the Lambda/layer closure,
flags the AgentCore Web Search region gate, and fails closed on unresolved
dependencies. No infrastructure is provisioned. Ported from Tessera-Org and adapted
to this project's layout (ecosystems live under app/content/ecosystems/).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Platform tools that ride the runtime image (no Gateway target needed).
PLATFORM_TOOLS: frozenset[str] = frozenset({
    "web_search", "generateUI", "memory_read", "memory_write", "memory_search",
    "calculator", "current_time", "http_request", "code_interpreter",
    "read_attachment", "browser",
})

# Model roles: an agent WITH depends_on is a "synthesizer", without is a "leaf".
MODEL_ROLES = {
    "synthesizer": ("SYNTHESIZER_MODEL_ID", "us.anthropic.claude-opus-4-7"),
    "leaf": ("LEAF_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
}

# AgentCore built-in Web Search availability (AgentCore Supported Regions).
WEB_SEARCH_REGIONS: frozenset[str] = frozenset({"us-east-1", "eu-west-1", "ap-northeast-1"})

# Lambda run-data-plane that always ships. get-ecosystem serves the single-eco config
# to the UI; mission-console is the conversational run-planner (ships the run-planner
# only, NOT the proprietary ecosystem builder). NOTE: mission-console is created in a
# later slice; it is listed here because the exported package expects it.
ALWAYS_LAMBDAS = ("agent-invoker", "websocket", "runs-api", "run-artifact", "get-ecosystem",
                  "mission-console", "ws-authorizer")


@dataclass
class ToolClosure:
    platform_inline: list[str] = field(default_factory=list)
    business: list[str] = field(default_factory=list)
    region_gated: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    unregistered: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)


@dataclass
class ModelClosure:
    roles: dict = field(default_factory=dict)
    per_agent_overrides: dict = field(default_factory=dict)


@dataclass
class LambdaClosure:
    ships: dict = field(default_factory=dict)
    layers: dict = field(default_factory=dict)


@dataclass
class RegionGate:
    target: str = ""
    unavailable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class BoM:
    eco_id: str = ""
    version: str = ""
    strategy: str = "orchestrated"
    orchestration: str = ""
    agents_declared: list[str] = field(default_factory=list)
    agents_injected: list[str] = field(default_factory=list)
    tools: ToolClosure = field(default_factory=ToolClosure)
    models: ModelClosure = field(default_factory=ModelClosure)
    domains: list[str] = field(default_factory=list)
    catalog_paths: list[str] = field(default_factory=list)
    memory_namespaces: list[str] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    document_inputs: bool = False
    lambdas: LambdaClosure = field(default_factory=LambdaClosure)
    secrets_required: list[str] = field(default_factory=list)
    region_gate: RegionGate = field(default_factory=RegionGate)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"ok": self.ok}


# --------------------------------------------------------------------------- helpers

def _agent_tools(config: dict) -> dict:
    out = {}
    for a in config.get("agents", []) or []:
        if isinstance(a, dict) and a.get("id"):
            out[a["id"]] = sorted(t for t in (a.get("tools") or []) if isinstance(t, str))
    return out


def _all_declared_tools(config: dict) -> set[str]:
    tools: set[str] = set()
    for names in _agent_tools(config).values():
        tools.update(names)
    return tools


def _has_document_inputs(config: dict) -> bool:
    eco = config.get("ecosystem") or {}
    inputs = eco.get("inputs") or config.get("inputs") or []
    file_hint = ("file", "doc", "upload", "pdf", "attachment", "document")
    for i in inputs:
        if isinstance(i, str):
            if any(h in i.lower() for h in file_hint):
                return True
        elif isinstance(i, dict):
            blob = " ".join(str(v) for v in i.values()).lower() + " " + " ".join(i.keys()).lower()
            if any(h in blob for h in file_hint) or "accept" in i or "mime" in i:
                return True
    return False


# --------------------------------------------------------------------------- per-axis

def _resolve_orchestration(config: dict, bom: BoM) -> None:
    strategy = (config.get("strategy") or "orchestrated").lower()
    bom.strategy = strategy
    if strategy == "quorum":
        bom.orchestration = "quorum-engine"
    elif strategy == "choreography":
        bom.orchestration = "eventbridge"
    else:
        bom.orchestration = "step-functions-asl"


def _resolve_agents(config: dict, bom: BoM) -> None:
    agents = config.get("agents", []) or []
    bom.agents_declared = [a["id"] for a in agents if isinstance(a, dict) and a.get("id")]
    if bom.strategy == "orchestrated":
        quality = config.get("quality", {}) or {}
        if config.get("report", True):
            if quality.get("challenger", True):
                bom.agents_injected = ["challenger_agent", "report_agent"]
            else:
                bom.agents_injected = ["report_agent"]


def _resolve_tools(config: dict, bom: BoM, registered: Optional[set[str]]) -> None:
    declared = _all_declared_tools(config)
    tc = bom.tools
    for name in sorted(declared):
        if name in PLATFORM_TOOLS:
            tc.platform_inline.append(name)
        else:
            tc.business.append(name)
    if registered is not None:
        for name in tc.business:
            (tc.verified if name in registered else tc.unregistered).append(name)
    else:
        tc.unverified = list(tc.business)


def _resolve_models(config: dict, bom: BoM) -> None:
    mc = bom.models
    agents = config.get("agents", []) or []
    has_synth = any((a.get("depends_on") or []) for a in agents if isinstance(a, dict))
    has_leaf = any(not (a.get("depends_on") or []) for a in agents if isinstance(a, dict))
    if bom.agents_injected:
        has_synth = True
    for role, present in (("synthesizer", has_synth), ("leaf", has_leaf)):
        if present:
            env, default = MODEL_ROLES[role]
            mc.roles[role] = {"env": env, "default": default}
    for a in agents:
        if isinstance(a, dict) and a.get("model"):
            mc.per_agent_overrides[a["id"]] = a["model"]


def _resolve_data(config: dict, bom: BoM) -> None:
    bom.domains = list(config.get("domains", []) or [])
    mem = config.get("memory", {}) or {}
    for ns in mem.get("shared_namespaces", []) or []:
        if isinstance(ns, dict) and ns.get("name"):
            bom.memory_namespaces.append(ns["name"])
            path = ns.get("path") or ""
            if isinstance(path, str) and path.startswith("/catalog/"):
                bom.catalog_paths.append(path)
    bom.catalog_paths = sorted(set(bom.catalog_paths))


def _resolve_quality(config: dict, bom: BoM) -> None:
    q = config.get("quality", {}) or {}
    bom.quality = {
        # Default True when the key is ABSENT — this MUST match the ASL compiler
        # (save-ecosystem: enable_grounding = quality.get("grounding", True) ...), which
        # emits Grounding_* tasks for any eco that doesn't explicitly disable grounding.
        # If the BoM used bool(q.get("grounding")) (absent → False), it would NOT ship the
        # grounding-check lambda while the ASL still references __GROUNDING_CHECK_ARN__,
        # so the token substitutes to "" at deploy → "FunctionName cannot be empty" on
        # every Grounding_* state (observed on incident-war-room, which omits `grounding:`).
        "grounding": bool(q.get("grounding", True)),
        "challenger": q.get("challenger", True),
        "challenger_gate": q.get("challenger_gate"),
        "gates": q.get("gates", {}) or {},
        "report": config.get("report", True),
    }


def _resolve_lambdas(config: dict, bom: BoM) -> None:
    lc = bom.lambdas
    for name in ALWAYS_LAMBDAS:
        lc.ships[name] = "run data-plane (always)"

    if bom.strategy == "quorum":
        lc.ships["quorum-run"] = "strategy: quorum"

    q = bom.quality
    if q.get("grounding"):
        lc.ships["grounding-check"] = "quality.grounding not disabled → ASL emits Grounding_* (Bedrock Guardrails)"

    if "web_search" in bom.tools.platform_inline:
        lc.ships["external-grounding"] = "web_search used → external claim verification (heuristic)"

    gates = q.get("gates") or {}
    hitl = [k for k, v in gates.items() if str(v).lower() == "ask"]
    if hitl or q.get("challenger_gate"):
        why = f"HITL gates ({', '.join(sorted(hitl))})" if hitl else "challenger_gate present"
        lc.ships["gate-decision"] = why
        lc.ships["gate-cleanup"] = "companion to gate-decision (TTL cleanup)"

    if bom.document_inputs:
        for name in ("presign-upload", "file-classify", "ecosystem-files"):
            lc.ships[name] = "document inputs"

    if config.get("interactive") or (config.get("strategy") == "interactive"):
        lc.ships["interactive-invoke"] = "interactive ecosystem (heuristic — confirm)"

    shared = ["auth", "pricing"]
    if bom.catalog_paths or bom.domains:
        shared.append("tessera_platform.catalog")
    if q.get("gates") or q.get("challenger_gate"):
        shared.append("tessera_platform.decision")
    lc.layers["shared"] = sorted(set(shared))
    if bom.strategy == "quorum":
        lc.layers["quorum"] = ["engine", "session", "models", "run", "bedrock_speaker",
                               "deliberation", "empanelment", "verdict", "personas", "whiteboard"]


def _resolve_secrets(config: dict, bom: BoM) -> None:
    secrets: list[str] = []
    if "web_search" in bom.tools.platform_inline:
        secrets.append("web-search-api-key")
    if bom.tools.business:
        secrets.append("gateway-m2m-client-secret")
        secrets.append("<per-tool credential providers — resolved from Gateway>")
    bom.secrets_required = secrets


def _resolve_region_gate(config: dict, bom: BoM, region: str) -> None:
    rg = bom.region_gate
    rg.target = region
    if "web_search" in bom.tools.platform_inline and region not in WEB_SEARCH_REGIONS:
        rg.unavailable.append("agentcore-web-search")
        rg.notes.append(
            f"AgentCore built-in Web Search is only in {sorted(WEB_SEARCH_REGIONS)}; "
            f"target '{region}' must supply a customer-provided search tool/key.")
    rg.notes.append("Model IDs must be validated present in the target region (not checked offline).")


def _validate(bom: BoM) -> None:
    if not bom.agents_declared:
        bom.errors.append("no agents declared")
    if bom.strategy not in ("orchestrated", "quorum", "choreography", "interactive"):
        bom.warnings.append(f"unknown strategy '{bom.strategy}' — treated as orchestrated")
    if bom.tools.unregistered:
        bom.errors.append(
            "unregistered tools (declared in YAML but no Gateway target/backend): "
            + ", ".join(bom.tools.unregistered))
    if "agentcore-web-search" in bom.region_gate.unavailable:
        bom.warnings.append(
            "web_search unavailable in target region — package MUST substitute a "
            "customer-provided search tool.")
    if bom.tools.unverified:
        bom.warnings.append(
            "business tools not verified offline (pass registered_tools to confirm backends): "
            + ", ".join(bom.tools.unverified))


# --------------------------------------------------------------------------- public API

def analyze(config: dict, *, region: str = "us-east-1",
            registered_tools: Optional[set[str]] = None) -> BoM:
    """Compute the BoM for one ecosystem config. Pure/offline unless registered_tools
    is supplied (from a live Gateway query)."""
    eco = config.get("ecosystem") or {}
    bom = BoM(
        eco_id=eco.get("name") or config.get("name") or "unnamed",
        version=str(eco.get("version") or config.get("version") or ""),
    )
    _resolve_orchestration(config, bom)
    _resolve_agents(config, bom)
    bom.document_inputs = _has_document_inputs(config)
    _resolve_tools(config, bom, registered_tools)
    _resolve_models(config, bom)
    _resolve_data(config, bom)
    _resolve_quality(config, bom)
    _resolve_lambdas(config, bom)
    _resolve_secrets(config, bom)
    _resolve_region_gate(config, bom, region)
    _validate(bom)
    return bom


# --------------------------------------------------------------------------- loading

def resolve_path(eco: str) -> Path:
    """Resolve an ecosystem arg (a YAML path, or a bare id) to a Path.

    Bare ids resolve under app/content/ecosystems/ (this project's layout).
    parents[2] of this file (app/platform/export/bom.py) is the app/ dir.
    """
    p = Path(eco)
    if p.exists() and p.is_file():
        return p
    app_dir = Path(__file__).resolve().parents[2]  # .../app
    stem = eco[:-5] if eco.endswith(".yaml") else eco
    cand = app_dir / "content" / "ecosystems" / f"{stem}.yaml"
    if cand.exists():
        return cand
    raise FileNotFoundError(f"ecosystem not found: {eco} (looked at {cand})")


def load_config(eco: str) -> dict:
    p = resolve_path(eco)
    with open(p) as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{p} did not parse to a mapping")
    return cfg


def render_report(bom: BoM) -> str:
    """Human-readable one-page BoM summary."""
    L = []
    status = "OK" if bom.ok else "FAIL"
    L.append(f"Ecosystem BoM — {bom.eco_id} v{bom.version or '?'}   [{status}]")
    L.append(f"  strategy: {bom.strategy}  orchestration: {bom.orchestration}")
    L.append(f"  agents: {len(bom.agents_declared)} declared, {len(bom.agents_injected)} injected")
    L.append(f"  tools: {len(bom.tools.platform_inline)} platform, {len(bom.tools.business)} business")
    L.append(f"  lambdas: {', '.join(sorted(bom.lambdas.ships))}")
    L.append(f"  region: {bom.region_gate.target}")
    if bom.region_gate.unavailable:
        L.append(f"  region-gated: {', '.join(bom.region_gate.unavailable)}")
    for w in bom.warnings:
        L.append(f"  WARN: {w}")
    for e in bom.errors:
        L.append(f"  ERROR: {e}")
    return "\n".join(L)
