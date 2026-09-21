"""Ground Truth Testing Framework.

Each capability defines:
- A dataset of known-correct inputs/outputs
- An accuracy threshold
- Deployment is blocked if accuracy drops below threshold

Usage:
    Define test cases as (input, expected_output) pairs.
    Use assert_threshold() to enforce minimum accuracy.
"""

import pytest

# Thresholds per capability (from spec)
THRESHOLDS = {
    "diagram_analysis": 0.95,
    "oscal_lookup": 1.0,
    "osa_cross_framework": 1.0,
    "risk_scoring": 0.90,  # ±5 points
    "exploit_chain_detection": 0.90,
    "finding_normalization": 1.0,
    "risk_correlation": 0.95,
    "exposure_calculation": 0.80,  # ±20%
    "soc2_extraction": 0.90,
    "red_flag_detection": 0.95,
    "vendor_scoring": 0.85,  # ±8 points
    "fair_calculation": 0.85,  # ±15%
    "premortem_narrative": 0.80,  # human eval proxy
    "bowtie_completeness": 0.95,
}


def assert_threshold(capability: str, correct: int, total: int):
    """Assert that accuracy meets the threshold for a capability. Fails the test (blocks deploy) if not."""
    threshold = THRESHOLDS.get(capability)
    if threshold is None:
        pytest.skip(f"No threshold defined for '{capability}'")

    accuracy = correct / total if total > 0 else 0
    assert accuracy >= threshold, (
        f"Ground truth FAILED for '{capability}': "
        f"{accuracy:.1%} accuracy ({correct}/{total}) "
        f"below threshold {threshold:.0%}"
    )


class TestGroundTruthFramework:
    """Validates the framework itself works correctly."""

    def test_passing_threshold(self):
        # 100% accuracy on a 1.0 threshold should pass
        assert_threshold("oscal_lookup", 10, 10)

    def test_failing_threshold(self):
        # 80% on a 1.0 threshold should fail
        with pytest.raises(AssertionError, match="Ground truth FAILED"):
            assert_threshold("oscal_lookup", 8, 10)

    def test_unknown_capability_skips(self):
        with pytest.raises(pytest.skip.Exception):
            assert_threshold("unknown_capability", 5, 5)
