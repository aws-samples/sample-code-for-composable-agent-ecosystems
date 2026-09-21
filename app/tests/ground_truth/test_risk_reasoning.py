"""Ground truth tests for Risk Reasoning ecosystem.

These tests validate AI capabilities against known-correct datasets.
Each test loads a dataset, runs the capability, and asserts threshold.
"""

import pytest
from tests.ground_truth.conftest import assert_threshold


class TestRiskCatalogIngestion:
    """Threshold: 100% — deterministic parsing."""

    @pytest.mark.skip(reason="Dataset not yet created — implement in Phase 2")
    def test_veris_ingestion_completeness(self):
        # TODO: Load VERIS JSON, run ingestion, verify all entries parsed
        correct = 0
        total = 0
        assert_threshold("finding_normalization", correct, total)


class TestRiskMatching:
    """Threshold: 100% — deterministic lookup."""

    @pytest.mark.skip(reason="Dataset not yet created — implement in Phase 2")
    def test_service_to_risk_matching(self):
        # TODO: 50 service-to-risk pairs verified by security team
        correct = 0
        total = 50
        assert_threshold("risk_correlation", correct, total)


class TestFAIRCalculation:
    """Threshold: ±15% of expert value."""

    @pytest.mark.skip(reason="Dataset not yet created — implement in Phase 2")
    def test_fair_ale_accuracy(self):
        # TODO: 20 risks with expert-calculated ALE
        correct = 0
        total = 20
        assert_threshold("fair_calculation", correct, total)
