"""Stage 0 (spec-quorum-archetype.md) — compiler isolation invariants.

Proves the zero-impact guarantee at the compiler boundary:
  * a `strategy: quorum` eco is NEVER DAG-compiled (no Step Functions states);
  * existing `orchestrated` ecos still compile to a state machine unchanged.
"""
import textwrap
import tempfile
import os

from tessera_platform.compiler.yaml_to_asl import compile_ecosystem


def _compile(yaml_text: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(textwrap.dedent(yaml_text))
        path = f.name
    try:
        return compile_ecosystem(path)
    finally:
        os.unlink(path)


def test_quorum_is_not_dag_compiled():
    """A quorum eco compiles to a stub with NO executable states — it can never run
    on the Step Functions rails."""
    result = _compile(
        """
        ecosystem:
          name: defensibility-rehearsal
        strategy: quorum
        quorum:
          empanelment: {persona_source: /catalog/scrutineer/}
        """
    )
    assert "States" not in result
    assert "StartAt" not in result
    assert "Comment" in result and "runtime" in result["Comment"].lower()


def test_orchestrated_still_compiles_to_a_state_machine():
    """Zero-impact guard: a normal orchestrated eco still produces a DAG state machine."""
    result = _compile(
        """
        ecosystem:
          name: normal-eco
        strategy: orchestrated
        report: false
        agents:
          - id: only_agent
            prompt: do the thing
        """
    )
    assert "States" in result and result["States"], "orchestrated eco must still compile a DAG"


def test_choreography_branch_unchanged():
    """The pre-existing choreography stub is untouched (regression guard)."""
    result = _compile(
        """
        ecosystem:
          name: choreo
        strategy: choreography
        agents:
          - id: a
        """
    )
    assert "States" not in result and "EventBridge" in result.get("Comment", "")
