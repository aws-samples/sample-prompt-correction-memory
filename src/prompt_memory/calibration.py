"""Confidence calibration: auto-adjusts thresholds based on outcome tracking.

Tracks actual accuracy vs. predicted confidence for each field. Fields where
the model is overconfident (reports high confidence but QA frequently corrects)
get their thresholds raised. Fields where the model is underconfident (reports
low confidence but QA rarely corrects) get their thresholds lowered.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OutcomeRecord:
    """Records whether an extraction at a given confidence was later corrected."""

    field_name: str
    document_type: str
    confidence_score: float
    was_corrected: bool
    timestamp: str = ""


@dataclass
class CalibrationState:
    """Per-field calibration state tracking confidence vs. accuracy."""

    field_name: str
    original_threshold: float
    current_threshold: float
    total_extractions: int = 0
    corrections_received: int = 0
    confidence_sum: float = 0.0
    # Binned accuracy: confidence bucket → (correct_count, total_count)
    bins: Dict[str, List[int]] = field(default_factory=dict)

    @property
    def actual_accuracy(self) -> float:
        """Fraction of extractions that were NOT corrected."""
        if self.total_extractions == 0:
            return 1.0
        return 1.0 - (self.corrections_received / self.total_extractions)

    @property
    def mean_confidence(self) -> float:
        """Average reported confidence across all extractions."""
        if self.total_extractions == 0:
            return 0.0
        return self.confidence_sum / self.total_extractions

    @property
    def calibration_error(self) -> float:
        """Difference between mean confidence and actual accuracy.

        Positive = overconfident (model thinks it's better than it is)
        Negative = underconfident (model is better than it reports)
        """
        return self.mean_confidence - self.actual_accuracy

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CalibrationState:
        return cls(
            field_name=data["field_name"],
            original_threshold=data["original_threshold"],
            current_threshold=data["current_threshold"],
            total_extractions=data.get("total_extractions", 0),
            corrections_received=data.get("corrections_received", 0),
            confidence_sum=data.get("confidence_sum", 0.0),
            bins=data.get("bins", {}),
        )


class ConfidenceCalibrator:
    """Tracks extraction outcomes and adjusts confidence thresholds per field.

    The calibrator maintains per-field state:
    - How often the model reports high confidence vs. how often QA corrects
    - Adjusts thresholds up for overconfident fields, down for underconfident

    Adjustment logic:
    - If actual_accuracy < mean_confidence - margin → raise threshold (overconfident)
    - If actual_accuracy > mean_confidence + margin → lower threshold (underconfident)
    - Adjustments are bounded to prevent runaway drift
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        adjustment_rate: float = 0.02,
        min_samples: int = 10,
        margin: float = 0.1,
        min_threshold: float = 0.3,
        max_threshold: float = 0.95,
    ):
        self._store_path = store_path or Path.home() / ".prompt_memory" / "calibration"
        self._store_path.mkdir(parents=True, exist_ok=True)
        self._adjustment_rate = adjustment_rate
        self._min_samples = min_samples
        self._margin = margin
        self._min_threshold = min_threshold
        self._max_threshold = max_threshold
        self._states: Dict[str, CalibrationState] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Load calibration state from disk."""
        state_file = self._store_path / "calibration_state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)
                for item in data:
                    state = CalibrationState.from_dict(item)
                    self._states[state.field_name] = state
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to load calibration state: %s", exc)

    def _save_state(self) -> None:
        """Persist calibration state to disk."""
        state_file = self._store_path / "calibration_state.json"
        data = [state.to_dict() for state in self._states.values()]
        with open(state_file, "w") as f:
            json.dump(data, f, indent=2)

    def get_threshold(self, field_name: str, default: float = 0.7) -> float:
        """Get the calibrated threshold for a field.

        Returns the adjusted threshold if calibration data exists,
        otherwise returns the default.
        """
        state = self._states.get(field_name)
        if state is None:
            return default
        return state.current_threshold

    def record_extraction(
        self, field_name: str, confidence: float, original_threshold: float = 0.7
    ) -> None:
        """Record that an extraction occurred at a given confidence level."""
        state = self._get_or_create_state(field_name, original_threshold)
        state.total_extractions += 1
        state.confidence_sum += confidence

        # Bin the confidence
        bin_key = self._confidence_bin(confidence)
        if bin_key not in state.bins:
            state.bins[bin_key] = [0, 0]  # [correct_count, total_count]
        state.bins[bin_key][1] += 1
        state.bins[bin_key][0] += 1  # Assume correct until corrected

    def record_correction(
        self, field_name: str, confidence_at_extraction: float
    ) -> None:
        """Record that a previously extracted field was corrected by QA.

        This means the extraction was wrong — update calibration accordingly.
        """
        state = self._states.get(field_name)
        if state is None:
            return

        state.corrections_received += 1

        # Update the bin — one fewer correct in the relevant bin
        bin_key = self._confidence_bin(confidence_at_extraction)
        if bin_key in state.bins and state.bins[bin_key][0] > 0:
            state.bins[bin_key][0] -= 1

        # Recalibrate if we have enough samples
        if state.total_extractions >= self._min_samples:
            self._recalibrate(state)

        self._save_state()

    def _recalibrate(self, state: CalibrationState) -> None:
        """Adjust threshold based on calibration error."""
        error = state.calibration_error

        if error > self._margin:
            # Overconfident: model reports high confidence but accuracy is lower
            # Raise threshold so more fields get escalated
            adjustment = self._adjustment_rate * (error - self._margin)
            state.current_threshold = min(
                state.current_threshold + adjustment,
                self._max_threshold,
            )
            logger.info(
                "Field '%s': overconfident (error=%.3f), raised threshold to %.3f",
                state.field_name,
                error,
                state.current_threshold,
            )
        elif error < -self._margin:
            # Underconfident: model reports low confidence but accuracy is high
            # Lower threshold so fewer fields get unnecessarily escalated
            adjustment = self._adjustment_rate * abs(error + self._margin)
            state.current_threshold = max(
                state.current_threshold - adjustment,
                self._min_threshold,
            )
            logger.info(
                "Field '%s': underconfident (error=%.3f), lowered threshold to %.3f",
                state.field_name,
                error,
                state.current_threshold,
            )

    def _get_or_create_state(
        self, field_name: str, original_threshold: float
    ) -> CalibrationState:
        """Get existing state or create new one."""
        if field_name not in self._states:
            self._states[field_name] = CalibrationState(
                field_name=field_name,
                original_threshold=original_threshold,
                current_threshold=original_threshold,
            )
        return self._states[field_name]

    @staticmethod
    def _confidence_bin(confidence: float) -> str:
        """Bin confidence into 0.1-wide buckets."""
        bucket = int(confidence * 10) / 10.0
        return f"{bucket:.1f}"

    def get_calibration_report(self) -> Dict[str, Dict[str, Any]]:
        """Get a report of all calibrated fields with their metrics."""
        report = {}
        for field_name, state in self._states.items():
            report[field_name] = {
                "original_threshold": state.original_threshold,
                "current_threshold": state.current_threshold,
                "total_extractions": state.total_extractions,
                "corrections_received": state.corrections_received,
                "actual_accuracy": round(state.actual_accuracy, 4),
                "mean_confidence": round(state.mean_confidence, 4),
                "calibration_error": round(state.calibration_error, 4),
                "threshold_delta": round(
                    state.current_threshold - state.original_threshold, 4
                ),
            }
        return report

    def reset(self, field_name: Optional[str] = None) -> None:
        """Reset calibration state for a field (or all fields)."""
        if field_name:
            self._states.pop(field_name, None)
        else:
            self._states = {}
        self._save_state()
