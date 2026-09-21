"""Integration: every shipped ecosystem YAML loads into the runtime graph loader
(Orchestrator) and exposes a coherent agent/swarm graph. This is the seam between the
authored YAML and the runtime that reads it.
"""
import glob
import os

import pytest

from tessera_platform.orchestrator.engine import Orchestrator

_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ECO_DIR = os.path.join(_APP, "content", "ecosystems")

ECOSYSTEM_FILES = sorted(glob.glob(os.path.join(_ECO_DIR, "*.yaml")))


def test_there_are_ecosystems_to_check():
    assert ECOSYSTEM_FILES


@pytest.mark.parametrize("eco_path", ECOSYSTEM_FILES, ids=lambda p: os.path.basename(p))
def test_ecosystem_loads_into_orchestrator(eco_path):
    orch = Orchestrator(eco_path)

    # Every eco has a name.
    assert orch.ecosystem_name, "ecosystem.name missing"

    # Agents parse to a non-empty list of dicts with ids.
    agents = orch.agents
    assert isinstance(agents, list) and agents, "no agents"
    ids = [a.get("id") for a in agents]
    assert all(ids), "an agent is missing an id"
    assert len(ids) == len(set(ids)), f"duplicate agent ids in {orch.ecosystem_name}"

    # depends_on references (when present) must point at agents that exist.
    id_set = set(ids)
    for a in agents:
        for dep in (a.get("depends_on") or []):
            assert dep in id_set, f"{a.get('id')} depends_on unknown agent {dep!r}"

    # parallel_swarms (when present) must name declared swarms.
    swarms = orch.swarms
    for sw in orch.parallel_swarms:
        assert sw in swarms, f"parallel_swarm {sw!r} not declared in swarms"
