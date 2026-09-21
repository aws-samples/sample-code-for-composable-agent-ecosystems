# Services: Amazon Bedrock, Amazon Bedrock Guardrails, Amazon S3
"""Tessera Orchestrator — Calls real agent HTTP services via A2A protocol.

Loads ecosystem YAML, resolves agent endpoints from Registry,
calls each agent via A2A (POST /a2a), streams their AG-UI events to the frontend.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import AsyncIterator

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

REGISTRY_URL = os.environ.get("TESSERA_REGISTRY_URL", "http://localhost:8003")
ECOSYSTEMS_DIR = Path(__file__).parent.parent.parent / "ecosystems"


async def check_grounding(response: str, sources: dict[str, str]) -> dict | None:
    """Validate report is grounded in source agent outputs using Amazon Bedrock Guardrails."""
    try:
        import boto3
        session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
        client = session.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

        # Build grounding source from all agent outputs
        grounding_source = "\n\n".join(f"[{agent_id}]: {output[:2000]}" for agent_id, output in sources.items())

        result = client.apply_guardrail(
            guardrailIdentifier=os.environ.get("TESSERA_GUARDRAIL_ID", ""),
            guardrailVersion="DRAFT",
            source="OUTPUT",
            content=[{"text": {"text": response[:4000]}}],
            groundingSource={"text": {"text": grounding_source[:10000]}},
        )

        action = result.get("action", "NONE")
        assessments = result.get("assessments", [])
        return {"action": action, "grounded": action == "NONE", "assessments": assessments}
    except Exception:
        return None


def REPORT_AGENT_CONFIG(depends_on: list[str]) -> dict:
    """Platform report agent — injected as final node when report: true."""
    return {
        "id": "report_agent",
        "name": "Report",
        "swarm": "platform",
        "depends_on": depends_on,
        "tools": [],
        "prompt": (
            "You are the Report Agent. Produce a polished final report from all previous agents' outputs. "
            "Structure your response EXACTLY as follows:\n\n"
            "## Executive Summary\nOne paragraph summarizing the overall outcome.\n\n"
            "## Key Findings\nBulleted list of the most important discoveries or decisions. Include [source: agent_name] for each.\n\n"
            "## Disagreements & Tensions\nWhere agents contradicted each other, what the tension was, and how it resolved (or didn't).\n\n"
            "## Decisions Made\nWhat was decided and by whom.\n\n"
            "## Confidence Assessment\nAggregate confidence levels from agents. Flag any LOW confidence findings.\n\n"
            "## Risks & Concerns\nAny unresolved risks or dissenting views.\n\n"
            "## Next Steps\nConcrete actionable recommendations.\n\n"
            "Be concise, factual, and reference specific agent findings. No filler.\n"
            "CRITICAL: Quote agents directly — do not paraphrase or alter their findings. Use exact data points and verdicts as stated.\n\n"
            "IMPORTANT: Use the generateUI tool to create visual summaries where data supports it. "
            "For example: a table comparing agent positions, a bar chart of votes or scores, "
            "or a card highlighting the final decision. Use at least one visualization."
        ),
    }


def CHALLENGER_AGENT_CONFIG(depends_on: list[str]) -> dict:
    """Platform challenger agent — reviews outputs and flags weak claims."""
    return {
        "id": "challenger_agent",
        "name": "Challenger",
        "swarm": "platform",
        "depends_on": depends_on,
        "tools": [],
        "prompt": (
            "You are the Challenger Agent. Your job is to stress-test the outputs from all previous agents. "
            "For each agent's output, identify:\n"
            "1. Claims made without evidence or tool results to back them up\n"
            "2. Logical gaps or unsupported leaps in reasoning\n"
            "3. Contradictions between agents that weren't addressed\n"
            "4. Overconfident conclusions from limited data\n\n"
            "Structure your response as:\n"
            "## Weak Claims\nList each weak claim, which agent made it, and why it's weak.\n\n"
            "## Logical Gaps\nReasoning that doesn't follow from the evidence.\n\n"
            "## Unresolved Contradictions\nWhere agents disagree and neither provided sufficient evidence.\n\n"
            "## Verdict\nOverall quality assessment: STRONG / ADEQUATE / WEAK with justification.\n\n"
            "Be rigorous but fair. If the work is solid, say so."
        ),
    }

app = FastAPI(title="Tessera Orchestrator")
app.add_middleware(CORSMiddleware, allow_origins=[os.environ.get("CORS_ORIGIN", "*")], allow_methods=["*"], allow_headers=["*"])


async def call_agent(agent_config: dict, params: dict) -> AsyncIterator[dict]:
    """Invoke an agent via AgentCore Runtime."""
    from tessera_platform.agentcore.client import invoke_agent

    async for event in invoke_agent(agent_config, params):
        yield event


async def run_ecosystem(config: dict, session_id: str, mission: str = "") -> AsyncIterator[dict]:
    """Run ecosystem as a DAG based on depends_on relationships."""
    ecosystem_name = config["ecosystem"]["name"]
    agents = list(config["agents"])
    swarms = config.get("swarms", {})
    strategy = config.get("strategy", "graph")
    input_context = config.get("_input_context", "")

    # Inject platform agents based on quality settings (defaults: all on)
    quality = config.get("quality", {})
    enable_challenger = quality.get("challenger", True)
    enable_report = config.get("report", True)
    enable_grounding = quality.get("grounding", True)

    if enable_report:
        all_ids = {a["id"] for a in agents}
        depended_on = set()
        for a in agents:
            for dep in a.get("depends_on", []):
                depended_on.add(dep)
        leaf_ids = list(all_ids - depended_on) or [agents[-1]["id"]]

        if enable_challenger:
            challenger_config = CHALLENGER_AGENT_CONFIG(leaf_ids)
            report_config = REPORT_AGENT_CONFIG(leaf_ids + ["challenger_agent"])
            agents.append(challenger_config)
        else:
            report_config = REPORT_AGENT_CONFIG(leaf_ids)

        agents.append(report_config)

    yield {"type": "RUN_STARTED", "ecosystem": ecosystem_name, "session_id": session_id, "timestamp": time.time()}

    # Build execution order from depends_on (topological sort)
    agent_outputs: dict[str, str] = {}

    if strategy == "choreography":
        # Event-driven: agents react to emitted signals
        async for event in run_choreography(agents, session_id, mission, agent_outputs):
            yield event
    else:
        # Orchestrated: DAG execution based on depends_on
        async for event in run_dag(agents, session_id, mission, agent_outputs, input_context):
            yield event

    # Store outputs for post-run chat
    _run_outputs[session_id] = {**agent_outputs, "_config": config}

    # Grounding check on report agent output
    if enable_grounding and enable_report and "report_agent" in agent_outputs and os.environ.get("TESSERA_GUARDRAIL_ID"):
        grounding_result = await check_grounding(
            response=agent_outputs["report_agent"],
            sources={k: v for k, v in agent_outputs.items() if k != "report_agent"},
        )
        if grounding_result:
            yield {"type": "STATE_DELTA", "agent": "report_agent", "grounding": grounding_result, "timestamp": time.time()}

    yield {"type": "RUN_FINISHED", "ecosystem": ecosystem_name, "session_id": session_id, "timestamp": time.time()}


async def run_dag(agents: list, session_id: str, mission: str, agent_outputs: dict, input_context: str = "") -> AsyncIterator[dict]:
    """Run agents in layers — agents in the same layer run in parallel."""
    remaining = list(agents)
    completed = set(agent_outputs.keys())

    while remaining:
        ready = [a for a in remaining if all(dep in completed for dep in a.get("depends_on", []))]
        if not ready:
            ready = remaining

        # Run ready agents in parallel, collect events via queue
        queue = asyncio.Queue()
        layer_outputs: dict[str, list[str]] = {a["id"]: [] for a in ready}

        async def run_one(agent_config):
            agent_id = agent_config["id"]
            agent_swarm = agent_config.get("swarm", "")
            dep_context = ""
            for dep_id in agent_config.get("depends_on", []):
                if dep_id in agent_outputs:
                    dep_context += f"\n\n[Output from {dep_id}]:\n{agent_outputs[dep_id][:2000]}"
            params = {"session_id": session_id, "mission": mission, "dependency_context": dep_context}
            # Pass input_context to entry_point agents (those with no dependencies)
            if not agent_config.get("depends_on") and input_context:
                params["input_context"] = input_context
            async for event in call_agent(agent_id, params, agent_config=agent_config):
                if agent_swarm:
                    event["swarm"] = agent_swarm
                if event.get("type") == "TEXT_MESSAGE_CONTENT":
                    layer_outputs[agent_id].append(event.get("content", ""))
                await queue.put(event)

        tasks = [asyncio.create_task(run_one(a)) for a in ready]

        # Yield events as they arrive from parallel agents
        done_count = 0
        while done_count < len(tasks):
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield event
            except asyncio.TimeoutError:
                # Check if all tasks finished
                done_count = sum(1 for t in tasks if t.done())

        # Drain remaining events
        while not queue.empty():
            yield await queue.get()

        # Wait for all tasks and record outputs
        await asyncio.gather(*tasks, return_exceptions=True)
        for a in ready:
            output = "\n".join(layer_outputs[a["id"]])
            agent_outputs[a["id"]] = output
            completed.add(a["id"])

            # Per-agent grounding check against dependency context
            if os.environ.get("TESSERA_GUARDRAIL_ID") and a.get("depends_on"):
                sources = {dep: agent_outputs[dep] for dep in a.get("depends_on", []) if dep in agent_outputs}
                if sources and output:
                    grounding = await check_grounding(response=output, sources=sources)
                    if grounding:
                        yield {"type": "STATE_DELTA", "agent": a["id"], "grounding": grounding, "timestamp": time.time()}

        remaining = [a for a in remaining if a["id"] not in completed]


async def run_choreography(agents: list, session_id: str, mission: str, agent_outputs: dict) -> AsyncIterator[dict]:
    """Event-driven execution: agents react to emitted signals."""
    # Build trigger map: signal → list of agents that trigger on it
    trigger_map: dict[str, list[dict]] = {}
    for a in agents:
        for trigger in a.get("triggers", []):
            trigger_map.setdefault(trigger, []).append(a)

    # Start with agents triggered by "mission_start"
    pending_signals = ["mission_start"]
    executed = set()

    while pending_signals:
        signal = pending_signals.pop(0)
        triggered_agents = trigger_map.get(signal, [])

        for agent_config in triggered_agents:
            agent_id = agent_config["id"]
            if agent_id in executed:
                continue
            executed.add(agent_id)

            # Context from the signal source
            dep_context = f"\n\n[Triggered by signal: {signal}]"
            if signal in agent_outputs:
                dep_context += f"\n{agent_outputs[signal][:2000]}"

            params = {"session_id": session_id, "mission": mission, "dependency_context": dep_context}

            output_parts = []
            async for event in call_agent(agent_id, params, agent_config=agent_config):
                yield event
                if event.get("type") == "TEXT_MESSAGE_CONTENT":
                    output_parts.append(event.get("content", ""))

            output = "\n".join(output_parts)
            agent_outputs[agent_id] = output

            # Emit this agent's signals
            for emitted_signal in agent_config.get("emits", []):
                agent_outputs[emitted_signal] = output
                pending_signals.append(emitted_signal)


# === API Endpoints ===

@app.get("/api/ecosystems")
async def list_ecosystems():
    """List available ecosystems."""
    ecosystems = []
    for f in ECOSYSTEMS_DIR.glob("*.yaml"):
        with open(f) as fh:
            config = yaml.safe_load(fh)
        eco = config.get("ecosystem", {})
        memory = config.get("memory", {})
        namespaces = memory.get("shared_namespaces", [])

        # Derive cross-ecosystem reads: namespaces with no writers = external dependency
        writes_to = [ns.get("path", ns.get("name", "")) for ns in namespaces if ns.get("write")]
        reads_from_external = [ns.get("path", ns.get("name", "")) for ns in namespaces if not ns.get("write") and ns.get("read")]

        ecosystems.append({
            "id": f.stem,
            "name": eco.get("name", f.stem),
            "description": eco.get("description", ""),
            "agents": len(config.get("agents", [])),
            "strategy": config.get("strategy", "graph"),
            "swarms": list(config.get("swarms", {}).keys()),
            "inputs": eco.get("inputs", []),
            "writes_to": writes_to,
            "reads_from_external": reads_from_external,
        })
    return ecosystems


@app.get("/api/ecosystems/{ecosystem_id}")
async def get_ecosystem(ecosystem_id: str):
    """Get full ecosystem config."""
    path = ECOSYSTEMS_DIR / f"{ecosystem_id}.yaml"
    if not path.exists():
        return {"error": "Not found"}
    with open(path) as f:
        config = yaml.safe_load(f)
    # Include platform agents if report enabled
    if config.get("report", True):
        quality = config.get("quality", {})
        all_ids = {a["id"] for a in config["agents"]}
        depended_on = set()
        for a in config["agents"]:
            for dep in a.get("depends_on", []):
                depended_on.add(dep)
        leaf_ids = list(all_ids - depended_on) or [config["agents"][-1]["id"]]
        platform_agents = []
        if quality.get("challenger", True):
            platform_agents.append(CHALLENGER_AGENT_CONFIG(leaf_ids))
            platform_agents.append(REPORT_AGENT_CONFIG(leaf_ids + ["challenger_agent"]))
        else:
            platform_agents.append(REPORT_AGENT_CONFIG(leaf_ids))
        config["agents"] = list(config["agents"]) + platform_agents
    return config


@app.post("/api/agents/suggest")
async def suggest_agent(request: Request):
    """Use Amazon Bedrock to suggest an agent config from a natural language description."""
    body = await request.json()
    description = body.get("description", "")
    ecosystem_context = body.get("ecosystem_context", "")
    if not description:
        return {"error": "description required"}

    import boto3
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    client = session.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    prompt = f"""Design a single agent for the Tessera platform ecosystem "{ecosystem_context}".

Description: {description}

The agent's prompt MUST include:
1. Clear role identity and expertise
2. Specific output format with section headers
3. Quality constraints (cite evidence, no speculation, be concise)
4. Instructions on how to use its tools effectively

Respond with ONLY valid JSON (no markdown fences) for one agent with these fields:
- id (snake_case identifier)
- name (display name)
- swarm (optional group name)
- tools (list of tool name strings the agent needs)
- depends_on (list of upstream agent ids, can be empty)
- prompt (detailed system prompt, 4-6 sentences with output format and constraints)"""

    resp = client.invoke_model(
        modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}),
    )
    result = json.loads(resp["body"].read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = "\n".join(text.split("\n")[:-1])

    try:
        agent = json.loads(text)
    except Exception:
        agent = None

    return {"agent": agent}


@app.post("/api/ecosystems/suggest")
async def suggest_ecosystem(request: Request):
    """Use Amazon Bedrock to suggest an ecosystem config from a natural language description."""
    body = await request.json()
    description = body.get("description", "")
    if not description:
        return {"error": "description required"}

    import boto3
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    client = session.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    prompt = f"""You are an expert at designing multi-agent ecosystems for the Tessera platform.
Given this description, suggest an ecosystem YAML config with agents, tools, strategy, and memory.

Description: {description}

Each agent's prompt MUST include:
1. A clear role identity (who they are, their expertise)
2. Specific output format (e.g. "Present as: ## Section 1... ## Section 2...")
3. Quality constraints (e.g. "Cite evidence for every claim", "No speculation", "Be concise")
4. What they should do with their tools
5. How their output feeds into downstream agents

The LAST agent in the depends_on chain should have a synthesis mandate: "Your output is the primary deliverable. Be comprehensive and structured."

CRITICAL REQUIREMENTS:
- Every agent MUST have a depends_on field (use [] for agents with no dependencies)
- Design a proper DAG: some agents run in parallel, others wait for upstream results
- Generate at least 5-8 specialized agents. Prefer focused agents over generalists.
- memory.strategies must be a simple list of strings like [semantic, episodic]

Respond with ONLY valid YAML (no markdown fences). Include:
- ecosystem (name, description, version, inputs — list of input definitions with id, type (text/file/select), label, required, accept fields for what the user needs to provide)
- strategy: orchestrated
- report: true
- memory (strategies, shared_namespaces)
- agents (each with id, name, swarm, depends_on, tools, prompt)
- swarms (if applicable)
- parallel_swarms (if applicable)
- triggers"""

    resp = client.invoke_model(
        modelId="us.anthropic.claude-opus-4-7",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}),
    )
    result = json.loads(resp["body"].read())
    yaml_text = result["content"][0]["text"].strip()
    # Strip markdown fences if present
    if yaml_text.startswith("```"):
        yaml_text = "\n".join(yaml_text.split("\n")[1:])
    if yaml_text.endswith("```"):
        yaml_text = "\n".join(yaml_text.split("\n")[:-1])

    try:
        config = yaml.safe_load(yaml_text)
    except Exception:
        config = None

    return {"yaml": yaml_text, "config": config}


@app.put("/api/ecosystems/{ecosystem_id}")
async def save_ecosystem(ecosystem_id: str, request: Request):
    """Save ecosystem YAML."""
    body = await request.json()
    yaml_content = body.get("yaml", "")
    path = ECOSYSTEMS_DIR / f"{ecosystem_id}.yaml"
    with open(path, "w") as f:
        f.write(yaml_content)
    return {"saved": True, "id": ecosystem_id}


@app.post("/api/runs/{ecosystem_id}")
async def start_run(ecosystem_id: str, request: Request):
    """Start an ecosystem run — spawns agents dynamically, streams AG-UI events via SSE."""
    path = ECOSYSTEMS_DIR / f"{ecosystem_id}.yaml"
    if not path.exists():
        return {"error": "Ecosystem not found"}

    body = await request.json() if await request.body() else {}
    mission = body.get("mission", "")
    inputs = body.get("inputs", {})  # Ecosystem inputs from frontend form

    with open(path) as f:
        config = yaml.safe_load(f)

    session_id = f"{ecosystem_id}-{int(time.time())}"

    # Build input_context from ecosystem inputs (decoupled — platform doesn't interpret)
    input_context = ""
    if inputs:
        input_context = "\n".join(f"[{k}]: {v}" for k, v in inputs.items() if v)

    # Store input_context in config for run_ecosystem to pass to entry_point
    config["_input_context"] = input_context

    # Spawn all agents from config

    async def stream():
        async for event in run_ecosystem(config, session_id, mission):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


from fastapi import WebSocket as FastAPIWebSocket

@app.websocket("/ws/runs/{ecosystem_id}")
async def ws_run(websocket: FastAPIWebSocket, ecosystem_id: str):
    """WebSocket run — parallel agent execution with interleaved events."""
    await websocket.accept()

    path = ECOSYSTEMS_DIR / f"{ecosystem_id}.yaml"
    if not path.exists():
        await websocket.send_json({"error": "Ecosystem not found"})
        await websocket.close()
        return

    # Receive mission from first message
    init = await websocket.receive_json()
    mission = init.get("mission", "")
    inputs = init.get("inputs", {})

    with open(path) as f:
        config = yaml.safe_load(f)

    # Build input_context from ecosystem inputs (same as POST handler)
    input_context = ""
    if inputs:
        input_context = "\n".join(f"[{k}]: {v}" for k, v in inputs.items() if v)
    config["_input_context"] = input_context

    session_id = f"{ecosystem_id}-{int(time.time())}"

    async for event in run_ecosystem(config, session_id, mission):
        await websocket.send_json(event)

    await websocket.close()


# Store run outputs for post-run chat
_run_outputs: dict[str, dict] = {}  # session_id -> {agent_id: output, "_config": config}


@app.post("/api/runs/{session_id}/chat")
async def run_chat(session_id: str, request: Request):
    """Post-run chat: route user question to best agent(s), respond with run context."""
    body = await request.json()
    question = body.get("question", "")
    if not question:
        return {"error": "question required"}

    run_data = _run_outputs.get(session_id)
    if not run_data:
        return {"error": "Run not found. Only available for runs in this session."}

    config = run_data.get("_config", {})
    agents = config.get("agents", [])

    # Route: ask LLM which agent(s) should answer
    import boto3
    session_boto = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    client = session_boto.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    agent_descriptions = "\n".join(f"- {a['id']}: {a.get('name', a['id'])} — {a.get('prompt', '')[:100]}" for a in agents)
    routing_prompt = f"""Given this user question and these agents, return a JSON array of 1-3 agent IDs best positioned to answer. Only return the JSON array, nothing else.

Agents:
{agent_descriptions}

Question: {question}"""

    resp = client.invoke_model(
        modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 100, "messages": [{"role": "user", "content": routing_prompt}]}),
    )
    result = json.loads(resp["body"].read())
    routing_text = result["content"][0]["text"].strip()
    try:
        routed_ids = json.loads(routing_text)
    except Exception:
        routed_ids = [agents[0]["id"]] if agents else []

    # Invoke each routed agent with the question + their run output as context
    responses = []
    for agent_id in routed_ids:
        agent_config = next((a for a in agents if a["id"] == agent_id), None)
        if not agent_config:
            continue

        agent_output = run_data.get(agent_id, "")
        params = {
            "session_id": session_id,
            "mission": question,
            "dependency_context": f"\n\n[Your previous output from this run]:\n{agent_output[:3000]}",
        }

        output_parts = []
        async for event in call_agent(agent_id, params, agent_config=agent_config):
            if event.get("type") == "TEXT_MESSAGE_CONTENT":
                output_parts.append(event.get("content", ""))

        responses.append({"agent_id": agent_id, "agent_name": agent_config.get("name", agent_id), "response": "\n".join(output_parts)})

    return {"responses": responses, "routed_to": routed_ids}


@app.get("/api/agents")
async def list_agents():
    """List all agents from ecosystem YAMLs (catalog) + running status."""
    agents = []
    # Build catalog from all ecosystem YAMLs
    for f in ECOSYSTEMS_DIR.glob("*.yaml"):
        with open(f) as fh:
            config = yaml.safe_load(fh)
        ecosystem_name = config.get("ecosystem", {}).get("name", f.stem)
        for agent in config.get("agents", []):
            agents.append({
                "agent_id": agent.get("id"),
                "name": agent.get("name", agent.get("id")),
                "ecosystem": ecosystem_name,
                "swarm": agent.get("swarm", ""),
                "tools": agent.get("tools", []),
                "depends_on": agent.get("depends_on", []),
                "status": "registered",
            })
    # Check running agents
    for agent in agents:
        if port:
            agent["status"] = "running"
            agent["port"] = port
    return agents


@app.get("/api/tools")
async def list_tools():
    """List all tools — platform tools + ecosystem-specific tools."""
    # Platform tools (always available)
    tools = [
        {"name": "web_search", "description": "Search the web for current information", "type": "platform"},
        {"name": "generateUI", "description": "Render tables, charts, or cards", "type": "platform"},
    ]
    # Collect unique tools from all ecosystems
    seen = {"web_search", "generateUI"}
    for f in ECOSYSTEMS_DIR.glob("*.yaml"):
        with open(f) as fh:
            config = yaml.safe_load(fh)
        for agent in config.get("agents", []):
            for tool_name in agent.get("tools", []):
                if tool_name not in seen:
                    seen.add(tool_name)
                    tools.append({"name": tool_name, "description": f"Ecosystem tool", "type": "ecosystem"})
    return tools


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "orchestrator"}


if __name__ == "__main__":
    import uvicorn
    print("\n⬡ Tessera Orchestrator (A2A mode)")
    print(f"  Ecosystems: {ECOSYSTEMS_DIR}")
    print(f"  Agents: ports 9001-9006")
    print(f"  → http://localhost:8080\n")
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104 — container service, must bind all interfaces
