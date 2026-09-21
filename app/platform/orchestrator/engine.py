"""Tessera Ecosystem Orchestrator — Runs ecosystem YAML configurations.

Handles: graph execution, parallel swarms, decision points, memory namespace enforcement.
"""

import asyncio
import json
import time
import yaml
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass
class ExerciseState:
    """Mutable state for a running exercise."""
    started_at: float = field(default_factory=time.time)
    agent_statuses: dict = field(default_factory=dict)
    shared_state: dict = field(default_factory=dict)
    decision_responses: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


class Orchestrator:
    """Executes an ecosystem YAML config."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.state = ExerciseState()
        self._agents = {}
        self._decision_queue = asyncio.Queue()

    @property
    def ecosystem_name(self) -> str:
        return self.config["ecosystem"]["name"]

    @property
    def agents(self) -> list:
        return self.config["agents"]

    @property
    def swarms(self) -> dict:
        return self.config.get("swarms", {})

    @property
    def parallel_swarms(self) -> list:
        return self.config.get("parallel_swarms", [])

    @property
    def decision_points(self) -> list:
        return self.config.get("decision_points", [])

    def get_agent_edges(self, agent_id: str) -> list:
        for agent in self.agents:
            if agent["id"] == agent_id:
                return agent.get("edges", [])
        return []

    def get_swarm_agents(self, swarm_name: str) -> list:
        return self.swarms.get(swarm_name, {}).get("agents", [])

    def validate_memory_access(self, agent_id: str, namespace: str, operation: str) -> bool:
        """Enforce memory namespace isolation per ecosystem config."""
        namespaces = self.config.get("memory", {}).get("shared_namespaces", [])
        for ns in namespaces:
            if ns["name"] == namespace:
                if operation == "write":
                    return agent_id in ns.get("write", [])
                elif operation == "read":
                    readers = ns.get("read", [])
                    return agent_id in readers or "all" in readers
        return False

    async def run(self) -> AsyncIterator[dict]:
        """Run the ecosystem. Yields AG-UI events."""
        yield {"type": "RUN_STARTED", "ecosystem": self.ecosystem_name, "timestamp": time.time()}

        if self.parallel_swarms:
            # Run swarms in parallel
            tasks = [self._run_swarm(swarm) for swarm in self.parallel_swarms]
            async for event in self._merge_streams(tasks):
                yield event
        else:
            # Single graph execution
            entry = self.config.get("entry_point")
            async for event in self._run_agent_chain(entry):
                yield event

        yield {"type": "RUN_FINISHED", "ecosystem": self.ecosystem_name, "timestamp": time.time()}

    async def _run_swarm(self, swarm_name: str) -> AsyncIterator[dict]:
        """Run a swarm's agent graph."""
        agents = self.get_swarm_agents(swarm_name)
        if not agents:
            return
        entry = agents[0]
        async for event in self._run_agent_chain(entry):
            event["swarm"] = swarm_name
            yield event

    async def _run_agent_chain(self, agent_id: str) -> AsyncIterator[dict]:
        """Run an agent and its downstream edges."""
        self.state.agent_statuses[agent_id] = AgentStatus.RUNNING
        yield {"type": "STATE_DELTA", "agent": agent_id, "status": "running", "timestamp": time.time()}

        # Simulate agent execution
        yield {"type": "TEXT_MESSAGE_CONTENT", "agent": agent_id, "content": f"{agent_id} executing..."}
        await asyncio.sleep(0.5)  # Simulate work

        self.state.agent_statuses[agent_id] = AgentStatus.COMPLETE
        yield {"type": "STATE_DELTA", "agent": agent_id, "status": "complete", "timestamp": time.time()}

        # Check decision points
        for dp in self.decision_points:
            if dp["id"] not in self.state.decision_responses:
                yield {
                    "type": "USER_INPUT_REQUIRED",
                    "decision_id": dp["id"],
                    "question": dp["question"],
                    "options": dp.get("options"),
                    "input_type": dp.get("input_type", "choice"),
                }
                # Wait for user response
                response = await self._decision_queue.get()
                self.state.decision_responses[dp["id"]] = response
                break  # One decision per agent step

        # Run downstream edges
        edges = self.get_agent_edges(agent_id)
        if len(edges) > 1:
            # Parallel edges
            tasks = [self._run_agent_chain(e) for e in edges]
            async for event in self._merge_streams(tasks):
                yield event
        elif edges:
            async for event in self._run_agent_chain(edges[0]):
                yield event

    async def _merge_streams(self, coroutines) -> AsyncIterator[dict]:
        """Merge multiple async iterators (parallel execution)."""
        tasks = [asyncio.create_task(self._collect(coro)) for coro in coroutines]
        for task in asyncio.as_completed(tasks):
            events = await task
            for event in events:
                yield event

    async def _collect(self, async_gen) -> list:
        """Collect all events from an async generator."""
        events = []
        async for event in async_gen:
            events.append(event)
        return events

    async def submit_decision(self, decision_id: str, response: str):
        """Submit a user decision (from WebSocket)."""
        await self._decision_queue.put({"decision_id": decision_id, "response": response})
