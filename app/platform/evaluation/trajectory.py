"""AgentCore Evaluations — trajectory / trace evaluation (spec §7.3, Phase 3).

Because Tessera runs ON AgentCore, it can use AgentCore Evaluations natively to
score the agent's PATH — not just its final answer:
  * tool SELECTION (right tool for the step?),
  * tool PARAMS (right tool, wrong arguments?),
  * SYNTHESIS of tool outputs (did it use what the tool returned?).

Input is the ADOT/OTel spans emitted by the runtime (§5.1). Offline mode is the
build-time deploy gate; online mode is production monitoring (§7.4) — the SAME
declared metrics across both lifecycle stages. This module extracts a normalised
trajectory from spans (pure, unit-testable) and formats the AgentCore
Evaluations request; the actual API call is isolated in `submit_trajectory_eval`.

Eco-agnostic: it scores tool-use MECHANICS, which are domain-blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrajectoryStep:
    agent_id: str
    tool: str | None
    params: dict[str, Any]
    output_summary: str
    span_id: str


@dataclass
class Trajectory:
    session_id: str
    steps: list[TrajectoryStep] = field(default_factory=list)

    def tools_used(self) -> list[str]:
        return [s.tool for s in self.steps if s.tool]


def extract_trajectory(spans: list[dict], session_id: str = "") -> Trajectory:
    """Normalise ADOT/OTel spans into a Trajectory. Tolerant to span shape: we
    look for gen-ai / tool conventions. Domain-blind."""
    steps: list[TrajectoryStep] = []
    for sp in spans or []:
        attrs = sp.get("attributes") or sp.get("attrs") or {}
        name = sp.get("name") or ""
        # A tool-call span in gen-ai OTel semantic conventions
        tool = (attrs.get("gen_ai.tool.name") or attrs.get("tool.name")
                or (name if name.startswith("tool.") else None))
        if tool is None and sp.get("kind") not in ("tool", "TOOL"):
            # not a tool span; skip (we score tool-use mechanics)
            if not attrs.get("gen_ai.operation.name") == "execute_tool":
                continue
            tool = attrs.get("gen_ai.tool.name")
        params = attrs.get("gen_ai.tool.input") or attrs.get("tool.params") or {}
        if isinstance(params, str):
            params = {"_raw": params}
        out = attrs.get("gen_ai.tool.output") or attrs.get("tool.result") or ""
        steps.append(TrajectoryStep(
            agent_id=attrs.get("gen_ai.agent.id") or attrs.get("agent.id") or "",
            tool=tool,
            params=params if isinstance(params, dict) else {"_raw": str(params)},
            output_summary=str(out)[:400],
            span_id=sp.get("span_id") or sp.get("spanId") or "",
        ))
    return Trajectory(session_id=session_id, steps=steps)


def score_trajectory_heuristic(traj: Trajectory, allowed_tools: set[str]) -> dict:
    """A cheap, deterministic pre-check used OFFLINE before (or instead of) the
    AgentCore judge: flags tool calls outside the eco's declared tool set and
    empty-param calls. This is the domain-blind mechanics check; the AgentCore
    Evaluations judge scores selection/synthesis quality on top of it."""
    invalid_tool = []
    empty_params = []
    for s in traj.steps:
        if s.tool and allowed_tools and s.tool not in allowed_tools:
            invalid_tool.append({"tool": s.tool, "span_id": s.span_id, "agent": s.agent_id})
        if s.tool and not s.params:
            empty_params.append({"tool": s.tool, "span_id": s.span_id})
    n = max(1, len([s for s in traj.steps if s.tool]))
    path_validity = 1.0 - (len(invalid_tool) / n)
    return {
        "path_validity": round(max(0.0, path_validity), 4),
        "invalid_tool_calls": invalid_tool,
        "empty_param_calls": empty_params,
        "tool_calls": len(traj.tools_used()),
    }


def submit_trajectory_eval(*, agentcore_client, session_id: str, trace_id: str,
                           judge_model: str, dimensions: list[str] | None = None):
    """Submit an AgentCore Evaluations trajectory eval for a run's trace
    (isolated boto3 call). Dimensions default to the trajectory triad. Fails
    loudly on API error."""
    dims = dimensions or ["tool_selection", "tool_parameters", "output_synthesis"]
    return agentcore_client.create_evaluation(
        evaluationType="TRAJECTORY",
        traceIdentifier=trace_id,
        sessionIdentifier=session_id,
        evaluatorModelIdentifier=judge_model,
        dimensions=dims,
    )
