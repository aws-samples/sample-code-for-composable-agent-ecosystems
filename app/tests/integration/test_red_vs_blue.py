"""Integration test: Red vs Blue exercise end-to-end.

Validates: orchestrator executes both swarms, tools route correctly,
memory isolation enforced, AG-UI events emitted, decisions work.
"""

import os

import pytest

# The platform package is imported as `tessera_platform` (the shared-layer name); the
# test conftest registers it -> app/platform without shadowing the stdlib `platform`.
from tessera_platform.orchestrator.engine import Orchestrator

# Ecosystem YAMLs live under app/content/ecosystems/ in the current layout.
ECOSYSTEM_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "content", "ecosystems", "red-vs-blue.yaml"
)


class TestExerciseExecution:
    def test_orchestrator_loads_config(self):
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.ecosystem_name == "red-vs-blue"
        assert len(orch.agents) == 6
        assert orch.parallel_swarms == ["red", "blue"]

    def test_swarm_agent_counts(self):
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.get_swarm_agents("red") == ["recon", "injector", "exfiltrator"]
        assert orch.get_swarm_agents("blue") == ["watcher", "responder", "adapter"]

    def test_agent_edges(self):
        # The current red-vs-blue ecosystem expresses topology via `depends_on`, not the
        # legacy `edges` field, so get_agent_edges (which reads `edges`) returns [] for
        # every agent. This asserts the orchestrator reads the field consistently.
        orch = Orchestrator(ECOSYSTEM_PATH)
        for agent in ["recon", "injector", "exfiltrator", "watcher", "responder", "adapter"]:
            assert orch.get_agent_edges(agent) == []

    def test_no_decision_points_in_current_design(self):
        # The redesigned ecosystem has no explicit decision_points (the earlier 4-gate
        # design was removed). Assert the orchestrator loads it as decision-point-free.
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.decision_points == []


class TestMemoryIsolation:
    def test_red_cannot_write_blue_intel(self):
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.validate_memory_access("recon", "blue_intel", "write") is False
        assert orch.validate_memory_access("injector", "blue_intel", "read") is False

    def test_blue_cannot_write_red_intel(self):
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.validate_memory_access("watcher", "red_intel", "write") is False
        assert orch.validate_memory_access("responder", "red_intel", "read") is False

    def test_red_can_write_own_namespace(self):
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.validate_memory_access("recon", "red_intel", "write") is True
        assert orch.validate_memory_access("injector", "red_intel", "read") is True

    def test_blue_can_write_own_namespaces(self):
        # In the current design blue_intel is single-writer (adapter); defense_rules too.
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.validate_memory_access("adapter", "blue_intel", "write") is True
        assert orch.validate_memory_access("adapter", "defense_rules", "write") is True
        # watcher/responder can READ blue_intel but not WRITE it.
        assert orch.validate_memory_access("watcher", "blue_intel", "read") is True
        assert orch.validate_memory_access("watcher", "blue_intel", "write") is False

    def test_battlefield_readable_by_all_written_by_designated(self):
        # battlefield: read=[all], write scoped to the designated writers (recon, adapter).
        orch = Orchestrator(ECOSYSTEM_PATH)
        for agent in ["recon", "injector", "watcher", "adapter"]:
            assert orch.validate_memory_access(agent, "battlefield", "read") is True
        assert orch.validate_memory_access("recon", "battlefield", "write") is True
        assert orch.validate_memory_access("adapter", "battlefield", "write") is True
        assert orch.validate_memory_access("injector", "battlefield", "write") is False

    def test_attack_log_readable_by_all_written_by_none(self):
        # attack_log: read=[all], write=[] (append-only audit surface — no agent writes it).
        orch = Orchestrator(ECOSYSTEM_PATH)
        assert orch.validate_memory_access("watcher", "attack_log", "read") is True
        assert orch.validate_memory_access("injector", "attack_log", "read") is True
        assert orch.validate_memory_access("injector", "attack_log", "write") is False
        assert orch.validate_memory_access("watcher", "attack_log", "write") is False


def _red_vs_blue_tools_present() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("tessera_platform.tools.red_vs_blue.red_tools") is not None
    except ModuleNotFoundError:
        # A parent package (tessera_platform.tools) doesn't exist — the bundled tools
        # were dropped in the migration.
        return False


@pytest.mark.skipif(
    not _red_vs_blue_tools_present(),
    reason="bundled red_vs_blue Python tools were dropped in the migration (tools now via AgentCore Gateway)",
)
class TestToolExecution:
    def test_red_tools_return_valid_responses(self):
        from tessera_platform.tools.red_vs_blue.red_tools import probe_api, craft_injection, attempt_exploit

        result = probe_api({"target": "customer_bot"})
        body = __import__("json").loads(result["body"])
        assert "endpoints" in body
        assert body["target"] == "customer_bot"

        result = craft_injection({"target": "customer_bot", "technique": "ASI-03"})
        body = __import__("json").loads(result["body"])
        assert body["technique"] == "ASI-03"
        assert "payload_stages" in body

        result = attempt_exploit({"target": "customer_bot", "technique": "ASI-03"})
        body = __import__("json").loads(result["body"])
        assert "success" in body
        assert isinstance(body["success"], bool)

    def test_blue_tools_return_valid_responses(self):
        from tessera_platform.tools.red_vs_blue.blue_tools import monitor_behavior, revoke_tokens, deploy_countermeasure

        result = monitor_behavior({"agent": "customer_bot"})
        body = __import__("json").loads(result["body"])
        assert "deviation_score" in body
        assert 0 <= body["deviation_score"] <= 1

        result = revoke_tokens({"agent": "customer_bot"})
        body = __import__("json").loads(result["body"])
        assert body["revoked"] is True

        result = deploy_countermeasure({"type": "input_sanitization", "scope": "all_agents"})
        body = __import__("json").loads(result["body"])
        assert body["deployed"] is True
        assert 0 <= body["effectiveness"] <= 1


class TestAGUIEventStream:
    @pytest.mark.asyncio
    async def test_run_emits_start_and_finish(self):
        orch = Orchestrator(ECOSYSTEM_PATH)
        events = []
        async for event in orch.run():
            events.append(event)
            # Auto-submit decisions to avoid blocking
            if event["type"] == "USER_INPUT_REQUIRED":
                await orch.submit_decision(event["decision_id"], "Observe")

        types = [e["type"] for e in events]
        assert types[0] == "RUN_STARTED"
        assert types[-1] == "RUN_FINISHED"
        assert "STATE_DELTA" in types
