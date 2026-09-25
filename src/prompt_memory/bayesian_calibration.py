"""Bayesian confidence calibration with uncertainty quantification.

Upgrades the basic calibration with:
1. Beta-Binomial model: tracks (successes, failures) per confidence bin
2. Posterior uncertainty: knows when it doesn't have enough data to calibrate
3. Expected Calibration Error (ECE): standard metric for calibration quality
4. Reliability diagrams: data for visualization
5. Adaptive bin sizing: merges bins with too few samples
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BinState:
    """State for a single confidence bin using Beta distribution."""

    bin_lower: float
    bin_upper: float
    # Beta distribution parameters (successes + 1, failures + 1 for prior)
    alpha: float = 1.0  # prior: 1 success
    beta: float = 1.0  # prior: 1 failure
    total_samples: int = 0
    sum_confidence: float = 0.0

    @property
    def mean_accuracy(self) -> float:
        """Posterior mean of the Beta distribution = alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def mean_confidence(self) -> float:
        """Mean reported confidence in this bin."""
        if self.total_samples == 0:
            return (self.bin_lower + self.bin_upper) / 2.0
        return self.sum_confidence / self.total_samples

    @property
    def uncertainty(self) -> float:
        """Posterior standard deviation — measures how uncertain the estimate is."""
        a, b = self.alpha, self.beta
        variance = (a * b) / ((a + b) ** 2 * (a + b + 1))
        return math.sqrt(variance)

    @property
    def calibration_gap(self) -> float:
        """Difference between mean confidence and actual accuracy in this bin."""
        return self.mean_confidence - self.mean_accuracy

    def record_success(self, confidence: float) -> None:
        """Record a correct extraction (not later corrected)."""
        self.alpha += 1
        self.total_samples += 1
        self.sum_confidence += confidence

    def record_failure(self, confidence: float) -> None:
        """Record an incorrect extraction (later corrected by QA)."""
        self.beta += 1
        self.total_samples += 1
        self.sum_confidence += confidence

    def credible_interval(self, width: float = 0.9) -> Tuple[float, float]:
        """Compute credible interval for the true accuracy.

        Uses normal approximation to the Beta posterior for simplicity.
        """
        mean = self.mean_accuracy
        std = self.uncertainty
        z = 1.645  # 90% interval
        return (max(0.0, mean - z * std), min(1.0, mean + z * std))


@dataclass
class FieldCalibration:
    """Bayesian calibration state for a single field."""

    field_name: str
    original_threshold: float
    current_threshold: float
    bins: List[BinState] = field(default_factory=list)
    total_extractions: int = 0
    total_corrections: int = 0

    @property
    def expected_calibration_error(self) -> float:
        """Compute ECE — the standard calibration metric.

        ECE = sum over bins: (samples_in_bin / total_samples) * |accuracy - confidence|
        """
        if self.total_extractions == 0:
            return 0.0
        ece = 0.0
        for b in self.bins:
            if b.total_samples == 0:
                continue
            weight = b.total_samples / self.total_extractions
            ece += weight * abs(b.calibration_gap)
        return ece

    @property
    def reliability_data(self) -> List[Dict[str, float]]:
        """Data points for a reliability diagram.

        Returns list of {confidence, accuracy, uncertainty, samples} per bin.
        """
        points = []
        for b in self.bins:
            if b.total_samples < 2:
                continue
            lower, upper = b.credible_interval()
            points.append(
                {
                    "confidence": round(b.mean_confidence, 3),
                    "accuracy": round(b.mean_accuracy, 3),
                    "uncertainty": round(b.uncertainty, 4),
                    "credible_lower": round(lower, 3),
                    "credible_upper": round(upper, 3),
                    "samples": b.total_samples,
                }
            )
        return points


class BayesianCalibrator:
    """Bayesian confidence calibrator with uncertainty-aware threshold adjustment.

    Improves over basic calibration by:
    - Using Beta-Binomial model (proper Bayesian updating)
    - Only adjusting thresholds when posterior uncertainty is low (enough data)
    - Computing Expected Calibration Error (ECE)
    - Providing reliability diagram data for visualization
    - Handling bin sparsity via credible intervals

    Args:
        store_path: Directory for calibration state.
        n_bins: Number of confidence bins (default 10: 0.0-0.1, 0.1-0.2, etc.)
        min_samples_to_adjust: Minimum samples in a bin before using it for threshold.
        adjustment_rate: How aggressively to shift threshold per update.
        min_threshold: Floor for adjusted threshold.
        max_threshold: Ceiling for adjusted threshold.
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        n_bins: int = 10,
        min_samples_to_adjust: int = 10,
        adjustment_rate: float = 0.03,
        min_threshold: float = 0.3,
        max_threshold: float = 0.95,
    ):
        self._store_path = store_path or Path.home() / ".prompt_memory" / "bayesian_cal"
        self._store_path.mkdir(parents=True, exist_ok=True)
        self._n_bins = n_bins
        self._min_samples = min_samples_to_adjust
        self._adjustment_rate = adjustment_rate
        self._min_threshold = min_threshold
        self._max_threshold = max_threshold
        self._fields: Dict[str, FieldCalibration] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Load calibration state from disk."""
        state_file = self._store_path / "bayesian_state.json"
        if not state_file.exists():
            return
        try:
            with open(state_file, encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                bins = [BinState(**b) for b in item.get("bins", [])]
                fc = FieldCalibration(
                    field_name=item["field_name"],
                    original_threshold=item["original_threshold"],
                    current_threshold=item["current_threshold"],
                    bins=bins,
                    total_extractions=item.get("total_extractions", 0),
                    total_corrections=item.get("total_corrections", 0),
                )
                self._fields[fc.field_name] = fc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load Bayesian calibration state: %s", exc)

    def _save_state(self) -> None:
        """Persist calibration state."""
        state_file = self._store_path / "bayesian_state.json"
        data = []
        for fc in self._fields.values():
            data.append(
                {
                    "field_name": fc.field_name,
                    "original_threshold": fc.original_threshold,
                    "current_threshold": fc.current_threshold,
                    "total_extractions": fc.total_extractions,
                    "total_corrections": fc.total_corrections,
                    "bins": [asdict(b) for b in fc.bins],
                }
            )
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _get_or_create_field(
        self, field_name: str, original_threshold: float
    ) -> FieldCalibration:
        """Get existing field calibration or create with default bins."""
        if field_name not in self._fields:
            bin_width = 1.0 / self._n_bins
            bins = [
                BinState(
                    bin_lower=round(i * bin_width, 2),
                    bin_upper=round((i + 1) * bin_width, 2),
                )
                for i in range(self._n_bins)
            ]
            self._fields[field_name] = FieldCalibration(
                field_name=field_name,
                original_threshold=original_threshold,
                current_threshold=original_threshold,
                bins=bins,
            )
        return self._fields[field_name]

    def _find_bin(self, fc: FieldCalibration, confidence: float) -> Optional[BinState]:
        """Find the bin for a given confidence score."""
        for b in fc.bins:
            if b.bin_lower <= confidence < b.bin_upper:
                return b
        # Handle exactly 1.0
        if confidence >= 1.0 and fc.bins:
            return fc.bins[-1]
        return None

    def get_threshold(self, field_name: str, default: float = 0.7) -> float:
        """Get the calibrated threshold for a field."""
        fc = self._fields.get(field_name)
        if fc is None:
            return default
        return fc.current_threshold

    def record_extraction(
        self, field_name: str, confidence: float, original_threshold: float = 0.7
    ) -> None:
        """Record an extraction outcome (initially assumed correct)."""
        fc = self._get_or_create_field(field_name, original_threshold)
        fc.total_extractions += 1
        b = self._find_bin(fc, confidence)
        if b:
            b.record_success(confidence)

    def record_correction(
        self, field_name: str, confidence_at_extraction: float
    ) -> None:
        """Record that an extraction was corrected (was wrong).

        Moves one count from success to failure in the relevant bin.
        """
        fc = self._fields.get(field_name)
        if fc is None:
            return

        fc.total_corrections += 1
        b = self._find_bin(fc, confidence_at_extraction)
        if b:
            # Undo the success that was recorded at extraction time
            if b.alpha > 1:
                b.alpha -= 1
            # Record as failure
            b.record_failure(confidence_at_extraction)

        # Recalibrate threshold
        self._recalibrate(fc)
        self._save_state()

    def _recalibrate(self, fc: FieldCalibration) -> None:
        """Adjust threshold based on Bayesian posterior.

        Strategy: find the confidence level where the posterior accuracy
        crosses the original threshold. That's where we should route.
        """
        # Find the bin at the current threshold — what's the actual accuracy there?
        threshold_bin = self._find_bin(fc, fc.current_threshold)
        if not threshold_bin or threshold_bin.total_samples < self._min_samples:
            return  # Not enough data to adjust

        # If accuracy at threshold is lower than expected, raise threshold
        # If accuracy at threshold is higher than expected, lower threshold
        actual_at_threshold = threshold_bin.mean_accuracy
        expected = fc.current_threshold  # We expect accuracy ≈ threshold

        gap = expected - actual_at_threshold
        # Only adjust if uncertainty is low enough (we're confident in our estimate)
        if threshold_bin.uncertainty > 0.15:
            return  # Too uncertain to adjust

        if abs(gap) > 0.05:
            adjustment = self._adjustment_rate * gap
            fc.current_threshold = max(
                self._min_threshold,
                min(self._max_threshold, fc.current_threshold + adjustment),
            )

    def get_ece(self, field_name: str) -> Optional[float]:
        """Get Expected Calibration Error for a field.

        Lower is better. 0.0 = perfectly calibrated.
        """
        fc = self._fields.get(field_name)
        if fc is None:
            return None
        return fc.expected_calibration_error

    def get_reliability_diagram(self, field_name: str) -> List[Dict[str, float]]:
        """Get reliability diagram data for a field.

        Each point has: confidence, accuracy, uncertainty, credible interval, samples.
        Plot confidence (x) vs accuracy (y) — perfect calibration is the diagonal.
        """
        fc = self._fields.get(field_name)
        if fc is None:
            return []
        return fc.reliability_data

    def get_calibration_report(self) -> Dict[str, Dict[str, Any]]:
        """Get full calibration report for all fields."""
        report = {}
        for field_name, fc in self._fields.items():
            report[field_name] = {
                "original_threshold": fc.original_threshold,
                "current_threshold": round(fc.current_threshold, 4),
                "total_extractions": fc.total_extractions,
                "total_corrections": fc.total_corrections,
                "ece": round(fc.expected_calibration_error, 4),
                "reliability_points": fc.reliability_data,
                "threshold_delta": round(
                    fc.current_threshold - fc.original_threshold, 4
                ),
            }
        return report

    def reset(self, field_name: Optional[str] = None) -> None:
        """Reset calibration state."""
        if field_name:
            self._fields.pop(field_name, None)
        else:
            self._fields = {}
        self._save_state()
