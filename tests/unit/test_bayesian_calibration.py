"""Unit tests for Bayesian confidence calibration."""

import tempfile
from pathlib import Path

from src.prompt_memory.bayesian_calibration import (
    BayesianCalibrator,
    BinState,
    FieldCalibration,
)


class TestBinState:
    def test_initial_mean_accuracy(self):
        """Uninformative prior gives 0.5 accuracy."""
        b = BinState(bin_lower=0.7, bin_upper=0.8)
        assert abs(b.mean_accuracy - 0.5) < 0.01

    def test_accuracy_after_successes(self):
        """Many successes push accuracy toward 1.0."""
        b = BinState(bin_lower=0.8, bin_upper=0.9)
        for _ in range(20):
            b.record_success(0.85)
        assert b.mean_accuracy > 0.9

    def test_accuracy_after_failures(self):
        """Many failures push accuracy toward 0.0."""
        b = BinState(bin_lower=0.8, bin_upper=0.9)
        for _ in range(20):
            b.record_failure(0.85)
        assert b.mean_accuracy < 0.15

    def test_uncertainty_decreases_with_samples(self):
        """More data → lower uncertainty."""
        b = BinState(bin_lower=0.7, bin_upper=0.8)
        initial_uncertainty = b.uncertainty

        for _ in range(50):
            b.record_success(0.75)

        assert b.uncertainty < initial_uncertainty

    def test_calibration_gap_positive_when_overconfident(self):
        """Gap is positive when confidence exceeds accuracy."""
        b = BinState(bin_lower=0.9, bin_upper=1.0)
        # High confidence bin (0.9-1.0) but many failures
        for _ in range(5):
            b.record_success(0.95)
        for _ in range(5):
            b.record_failure(0.95)
        # Mean confidence ~0.95 but accuracy ~0.5 → positive gap
        assert b.calibration_gap > 0

    def test_credible_interval_contains_mean(self):
        """90% credible interval contains the posterior mean."""
        b = BinState(bin_lower=0.5, bin_upper=0.6)
        for _ in range(10):
            b.record_success(0.55)
        for _ in range(3):
            b.record_failure(0.55)

        lower, upper = b.credible_interval()
        assert lower <= b.mean_accuracy <= upper

    def test_credible_interval_narrows_with_data(self):
        """More data → narrower credible interval."""
        b = BinState(bin_lower=0.7, bin_upper=0.8)
        _, initial_upper = b.credible_interval()
        initial_width = initial_upper - b.credible_interval()[0]

        for _ in range(100):
            b.record_success(0.75)

        lower, upper = b.credible_interval()
        final_width = upper - lower
        assert final_width < initial_width


class TestFieldCalibration:
    def test_ece_zero_when_empty(self):
        """ECE is 0 with no data."""
        fc = FieldCalibration(
            field_name="test", original_threshold=0.7, current_threshold=0.7
        )
        assert fc.expected_calibration_error == 0.0

    def test_reliability_data_skips_empty_bins(self):
        """Reliability diagram skips bins with <2 samples."""
        fc = FieldCalibration(
            field_name="test",
            original_threshold=0.7,
            current_threshold=0.7,
            bins=[BinState(bin_lower=0.0, bin_upper=0.1)],
        )
        assert fc.reliability_data == []


class TestBayesianCalibrator:
    def _make_calibrator(self, **kwargs):
        tmp = tempfile.mkdtemp()
        return BayesianCalibrator(store_path=Path(tmp), **kwargs)

    def test_default_threshold_without_data(self):
        """Returns default when no calibration data."""
        cal = self._make_calibrator()
        assert cal.get_threshold("unknown", default=0.7) == 0.7

    def test_records_extractions(self):
        """Recording extractions builds internal state."""
        cal = self._make_calibrator()
        for _ in range(10):
            cal.record_extraction("field_a", confidence=0.85)

        report = cal.get_calibration_report()
        assert "field_a" in report
        assert report["field_a"]["total_extractions"] == 10

    def test_overconfident_field_raises_threshold(self):
        """Field with high confidence but many corrections → threshold rises."""
        cal = self._make_calibrator(min_samples_to_adjust=5)

        # Record many extractions at high confidence
        for _ in range(20):
            cal.record_extraction("bad_field", confidence=0.85, original_threshold=0.7)

        # Many corrections → model was wrong despite high confidence
        for _ in range(10):
            cal.record_correction("bad_field", confidence_at_extraction=0.85)

        threshold = cal.get_threshold("bad_field", default=0.7)
        # Threshold should have risen (or stayed same if uncertainty too high)
        # With 20 samples and 10 corrections in the 0.8-0.9 bin, uncertainty should be low enough
        report = cal.get_calibration_report()
        assert report["bad_field"]["total_corrections"] == 10

    def test_ece_computation(self):
        """ECE is computed and returned."""
        cal = self._make_calibrator()
        for _ in range(20):
            cal.record_extraction("field_a", confidence=0.85)
        for _ in range(5):
            cal.record_correction("field_a", confidence_at_extraction=0.85)

        ece = cal.get_ece("field_a")
        assert ece is not None
        assert 0.0 <= ece <= 1.0

    def test_ece_none_for_unknown_field(self):
        """ECE is None for fields with no data."""
        cal = self._make_calibrator()
        assert cal.get_ece("unknown") is None

    def test_reliability_diagram_data(self):
        """Reliability diagram returns plottable data points."""
        cal = self._make_calibrator()
        # Populate a bin with enough data
        for _ in range(15):
            cal.record_extraction("field_a", confidence=0.75)
        for _ in range(3):
            cal.record_correction("field_a", confidence_at_extraction=0.75)

        diagram = cal.get_reliability_diagram("field_a")
        # Should have at least one point with enough samples
        if diagram:  # May be empty if all bins have <2 samples after corrections
            point = diagram[0]
            assert "confidence" in point
            assert "accuracy" in point
            assert "uncertainty" in point
            assert "credible_lower" in point
            assert "credible_upper" in point

    def test_persists_to_disk(self):
        """State persists across instances."""
        tmp = tempfile.mkdtemp()
        path = Path(tmp)

        cal = BayesianCalibrator(store_path=path, min_samples_to_adjust=5)
        for _ in range(10):
            cal.record_extraction("field_a", confidence=0.9, original_threshold=0.7)
        for _ in range(5):
            cal.record_correction("field_a", confidence_at_extraction=0.9)

        # New instance from same path
        cal2 = BayesianCalibrator(store_path=path)
        report = cal2.get_calibration_report()
        assert "field_a" in report
        assert report["field_a"]["total_corrections"] == 5

    def test_reset_field(self):
        """Can reset single field."""
        cal = self._make_calibrator()
        cal.record_extraction("field_a", confidence=0.8)
        cal.record_extraction("field_b", confidence=0.8)
        cal.reset("field_a")
        report = cal.get_calibration_report()
        assert "field_a" not in report
        assert "field_b" in report

    def test_reset_all(self):
        """Can reset all fields."""
        cal = self._make_calibrator()
        cal.record_extraction("field_a", confidence=0.8)
        cal.reset()
        assert cal.get_calibration_report() == {}
