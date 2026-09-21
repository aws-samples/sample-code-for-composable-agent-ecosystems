"""Tessera Compiler — YAML ecosystem → AWS Step Functions ASL.

Reads an ecosystem YAML and produces an Amazon States Language definition
that orchestrates agents via AgentCore Runtime invocations.
Injects platform agents (challenger, report) based on quality settings.
"""

import json
import yaml
from pathlib import Path


def challenger_agent_config(depends_on):
    return {
        "id": "challenger_agent",
        "name": "Challenger",
        "swarm": "platform",
        "depends_on": depends_on,
        "tools": [],
        "prompt": (
            "You are the Challenger Agent. Stress-test the outputs from all previous agents. "
            "Identify: 1) Claims without evidence 2) Logical gaps 3) Contradictions 4) Overconfidence. "
            "Structure: ## Weak Claims, ## Logical Gaps, ## Unresolved Contradictions, ## Verdict (STRONG/ADEQUATE/WEAK)."
        ),
    }


def report_agent_config(depends_on):
    return {
        "id": "report_agent",
        "name": "Report",
        "swarm": "platform",
        "depends_on": depends_on,
        "tools": [],
        "prompt": (
            "You are the Report Agent. Produce a final report with: "
            "## Executive Summary, ## Key Findings (with [source: agent_name]), "
            "## Disagreements & Tensions, ## Decisions Made, ## Confidence Assessment, "
            "## Risks & Concerns, ## Next Steps. "
            "Quote agents directly. Use generateUI for at least one visualization."
        ),
    }


def compile_ecosystem(yaml_path: str) -> dict:
    """Compile an ecosystem YAML into a Step Functions state machine definition."""
    with open(yaml_path) as f:
        config = yaml.safe_load(f)

    agents = list(config.get("agents", []))
    strategy = config.get("strategy", "orchestrated")

    if strategy == "choreography":
        return {"Comment": "Choreography ecosystems use EventBridge, not Step Functions"}

    if strategy == "quorum":
        # Quorum archetype (spec-quorum-archetype.md): the panel is assembled and
        # deliberated at RUNTIME — there is no compiled DAG. Never DAG-compile it.
        return {"Comment": "Quorum ecosystems assemble & deliberate at runtime; no compiled state machine"}

    # Inject platform agents
    quality = config.get("quality", {})
    enable_challenger = quality.get("challenger", True)
    enable_report = config.get("report", True)

    if enable_report:
        all_ids = {a["id"] for a in agents}
        depended_on = set()
        for a in agents:
            for dep in a.get("depends_on", []):
                depended_on.add(dep)
        leaf_ids = sorted(all_ids - depended_on) or [agents[-1]["id"]]

        if enable_challenger:
            agents.append(challenger_agent_config(leaf_ids))
            agents.append(report_agent_config(leaf_ids + ["challenger_agent"]))
        else:
            agents.append(report_agent_config(leaf_ids))

    return build_dag_state_machine(config, agents)


def build_dag_state_machine(config: dict, agents: list) -> dict:
    """Build a Step Functions ASL from agent depends_on relationships."""
    ecosystem_name = config.get("ecosystem", {}).get("name", "unnamed")

    layers = compute_layers(agents)

    states = {}
    states["Start"] = {"Type": "Pass", "Next": layer_name(0)}

    for i, layer in enumerate(layers):
        next_state = layer_name(i + 1) if i + 1 < len(layers) else "End"
        merge_name = f"MergeLayer{i}"

        if len(layer) == 1:
            agent = layer[0]
            states[layer_name(i)] = build_agent_task(agent, merge_name if next_state != "End" else None)
            if next_state == "End":
                states[layer_name(i)]["End"] = True
        else:
            branches = [{"StartAt": agent["id"], "States": {agent["id"]: build_agent_task(agent, None)}} for agent in layer]
            states[layer_name(i)] = {
                "Type": "Parallel",
                "Branches": branches,
                "ResultPath": f"$.layer{i}_results",
                "Next": merge_name,
            }

        # Merge state: build dependency_context from results for next layer
        if next_state != "End":
            states[merge_name] = {
                "Type": "Pass",
                "Parameters": {
                    "mission.$": "$.mission",
                    "session_id.$": "$.session_id",
                    "dependency_context.$": "$.dependency_context",
                    "results.$": "$.results",
                },
                "Next": next_state,
            }

    # Remove End: false entries
    for state in states.values():
        if state.get("End") is False:
            del state["End"]

    return {
        "Comment": f"Tessera ecosystem: {ecosystem_name}",
        "StartAt": "Start",
        "States": states,
    }


def build_agent_task(agent: dict, next_state: str | None) -> dict:
    """Build a Task state that invokes an agent via AgentCore Runtime."""
    task = {
        "Type": "Task",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": "${AgentInvokerLambdaArn}",
            "Payload": {
                "agent_config": agent,
                "mission.$": "$.mission",
                "dependency_context.$": "$.dependency_context",
                "session_id.$": "$.session_id",
                "results.$": "$.results",
            },
        },
        "ResultSelector": {
            "agent_id.$": "$.Payload.agent_id",
            "output.$": "$.Payload.output",
        },
        "ResultPath": f"$.results.{agent['id']}",
        "TimeoutSeconds": 120,
    }

    if next_state:
        task["Next"] = next_state
    else:
        task["End"] = True

    return task


def compute_layers(agents: list) -> list[list[dict]]:
    """Group agents into execution layers based on depends_on."""
    layers = []
    placed = set()
    remaining = list(agents)

    while remaining:
        layer = [a for a in remaining if all(dep in placed for dep in a.get("depends_on", []))]
        if not layer:
            layer = remaining  # deadlock fallback
        layers.append(layer)
        for a in layer:
            placed.add(a["id"])
        remaining = [a for a in remaining if a["id"] not in placed]

    return layers


def layer_name(index: int) -> str:
    return f"Layer{index}"


def compile_all(ecosystems_dir: str) -> dict[str, dict]:
    """Compile all ecosystem YAMLs in a directory."""
    results = {}
    eco_path = Path(ecosystems_dir)
    for yaml_file in eco_path.glob("*.yaml"):
        results[yaml_file.stem] = compile_ecosystem(str(yaml_file))
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = compile_ecosystem(sys.argv[1])
        print(json.dumps(result, indent=2))
    else:
        results = compile_all("ecosystems")
        for name, asl in results.items():
            print(f"\n{'='*60}\n{name}\n{'='*60}")
            print(json.dumps(asl, indent=2))
