"""Unit tests for confidence calibration."""

import tempfile
from pathlib import Path

from src.prompt_memory.calibration import CalibrationState, ConfidenceCalibrator


class TestCalibrationState:
    def test_initial_accuracy_is_one(self):
        """No corrections means 100% accuracy."""
        state = CalibrationState(
            field_name="test", original_threshold=0.7, current_threshold=0.7
        )
        assert state.actual_accuracy == 1.0

    def test_accuracy_decreases_with_corrections(self):
        """Corrections reduce actual accuracy."""
        state = CalibrationState(
            field_name="test",
            original_threshold=0.7,
            current_threshold=0.7,
            total_extractions=10,
            corrections_received=3,
        )
        assert state.actual_accuracy == 0.7

    def test_calibration_error_overconfident(self):
        """Positive error means model is overconfident."""
        state = CalibrationState(
            field_name="test",
            original_threshold=0.7,
            current_threshold=0.7,
            total_extractions=10,
            corrections_received=4,  # actual accuracy = 0.6
            confidence_sum=8.5,  # mean confidence = 0.85
        )
        # error = 0.85 - 0.6 = 0.25 (overconfident)
        assert state.calibration_error > 0
        assert abs(state.calibration_error - 0.25) < 0.01

    def test_calibration_error_underconfident(self):
        """Negative error means model is underconfident."""
        state = CalibrationState(
            field_name="test",
            original_threshold=0.7,
            current_threshold=0.7,
            total_extractions=10,
            corrections_received=0,  # actual accuracy = 1.0
            confidence_sum=6.0,  # mean confidence = 0.6
        )
        # error = 0.6 - 1.0 = -0.4 (underconfident)
        assert state.calibration_error < 0

    def test_serialization_roundtrip(self):
        """State serializes and deserializes correctly."""
        state = CalibrationState(
            field_name="effective_date",
            original_threshold=0.7,
            current_threshold=0.75,
            total_extractions=50,
            corrections_received=8,
            confidence_sum=42.5,
        )
        data = state.to_dict()
        restored = CalibrationState.from_dict(data)
        assert restored.field_name == "effective_date"
        assert restored.current_threshold == 0.75
        assert restored.total_extractions == 50


class TestConfidenceCalibrator:
    def _make_calibrator(self, **kwargs):
        """Create calibrator with temp storage."""
        tmp = tempfile.mkdtemp()
        return ConfidenceCalibrator(store_path=Path(tmp), **kwargs)

    def test_default_threshold_without_data(self):
        """Returns default when no calibration data exists."""
        cal = self._make_calibrator()
        threshold = cal.get_threshold("unknown_field", default=0.7)
        assert threshold == 0.7

    def test_record_extraction_tracks_confidence(self):
        """Recording extractions updates internal state."""
        cal = self._make_calibrator()
        for _ in range(5):
            cal.record_extraction("payment_terms", confidence=0.85)

        report = cal.get_calibration_report()
        assert "payment_terms" in report
        assert report["payment_terms"]["total_extractions"] == 5
        assert abs(report["payment_terms"]["mean_confidence"] - 0.85) < 0.01

    def test_overconfident_field_raises_threshold(self):
        """Field with high confidence but frequent corrections gets threshold raised."""
        cal = self._make_calibrator(min_samples=5, adjustment_rate=0.05, margin=0.05)

        # Record 10 extractions at high confidence
        for _ in range(10):
            cal.record_extraction("bad_field", confidence=0.9, original_threshold=0.7)

        # Record 5 corrections (50% error rate despite 0.9 confidence)
        for _ in range(5):
            cal.record_correction("bad_field", confidence_at_extraction=0.9)

        # Threshold should have been raised
        new_threshold = cal.get_threshold("bad_field", default=0.7)
        assert new_threshold > 0.7

    def test_underconfident_field_lowers_threshold(self):
        """Field with low confidence but no corrections gets threshold lowered."""
        cal = self._make_calibrator(min_samples=5, adjustment_rate=0.05, margin=0.05)

        # Record 15 extractions at low confidence
        for _ in range(15):
            cal.record_extraction("good_field", confidence=0.55, original_threshold=0.7)

        # Record ZERO corrections (model is actually accurate despite reporting low confidence)
        # Trigger recalibration by recording a "correction" event that doesn't exist
        # Actually: the calibration happens in record_correction, so we need to
        # manually trigger it. Let's record one correction to trigger recalibration.
        cal.record_correction("good_field", confidence_at_extraction=0.55)

        # Even with 1 correction out of 15 (93% accuracy), mean confidence is 0.55
        # calibration_error = 0.55 - 0.93 = -0.38 (underconfident)
        # Threshold should be lowered
        new_threshold = cal.get_threshold("good_field", default=0.7)
        assert new_threshold < 0.7

    def test_threshold_bounded_by_max(self):
        """Threshold never exceeds max_threshold."""
        cal = self._make_calibrator(
            min_samples=5, adjustment_rate=0.5, margin=0.0, max_threshold=0.95
        )

        # Record extractions at extremely high confidence with many corrections
        for _ in range(10):
            cal.record_extraction("field", confidence=0.99, original_threshold=0.7)
        for _ in range(8):
            cal.record_correction("field", confidence_at_extraction=0.99)

        threshold = cal.get_threshold("field", default=0.7)
        assert threshold <= 0.95

    def test_threshold_bounded_by_min(self):
        """Threshold never falls below min_threshold."""
        cal = self._make_calibrator(
            min_samples=5, adjustment_rate=0.5, margin=0.0, min_threshold=0.3
        )

        # Record extractions at very low confidence with no corrections
        for _ in range(10):
            cal.record_extraction("field", confidence=0.2, original_threshold=0.7)
        # One correction to trigger recalibration
        cal.record_correction("field", confidence_at_extraction=0.2)

        threshold = cal.get_threshold("field", default=0.7)
        assert threshold >= 0.3

    def test_persists_to_disk(self):
        """Calibration state survives save/load cycle."""
        tmp = tempfile.mkdtemp()
        path = Path(tmp)

        cal = ConfidenceCalibrator(store_path=path, min_samples=3, margin=0.0)
        for _ in range(5):
            cal.record_extraction("field_a", confidence=0.9, original_threshold=0.7)
        for _ in range(3):
            cal.record_correction("field_a", confidence_at_extraction=0.9)

        # Load a new instance from the same path
        cal2 = ConfidenceCalibrator(store_path=path)
        report = cal2.get_calibration_report()
        assert "field_a" in report
        assert report["field_a"]["corrections_received"] == 3

    def test_reset_field(self):
        """Can reset calibration for a specific field."""
        cal = self._make_calibrator()
        cal.record_extraction("field_a", confidence=0.8)
        cal.record_extraction("field_b", confidence=0.8)

        cal.reset("field_a")

        report = cal.get_calibration_report()
        assert "field_a" not in report
        assert "field_b" in report

    def test_reset_all(self):
        """Can reset all calibration state."""
        cal = self._make_calibrator()
        cal.record_extraction("field_a", confidence=0.8)
        cal.record_extraction("field_b", confidence=0.8)

        cal.reset()

        report = cal.get_calibration_report()
        assert len(report) == 0
