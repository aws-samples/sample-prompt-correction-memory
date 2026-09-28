"""Confidence-based routing: accept result or trigger self-healing.

Integrates all tiers:
- Tier 1: Graduated rules (deterministic, zero LLM cost)
- Tier 2: Cloud LLM extraction (standard cost)
- Tier 3: Self-healing with correction memory (escalated model + few-shot)

Also integrates:
- Confidence calibration (auto-adjusting thresholds)
- Semantic correction retrieval (similarity-based, not just key lookup)
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from src.extraction.extractor import Extractor
from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition
from src.prompt_memory.correction_store import CorrectionStore
from src.prompt_memory.retry_with_examples import retry_with_corrections

logger = logging.getLogger(__name__)


class ConfidenceRouter:
    """Routes extraction through tiered pipeline with self-healing.

    Flow:
        1. Check graduated rules (Tier 1) — zero cost if rule matches
        2. Extract field using standard model (Tier 2)
        3. If confidence >= calibrated threshold → accept
        4. If confidence < threshold → semantic retrieval of corrections → retry (Tier 3)
        5. Record outcome for calibration tracking
    """

    def __init__(
        self,
        extractor: Optional[Extractor] = None,
        correction_store: Optional[CorrectionStore] = None,
        prompt_memory_model: Optional[str] = None,
        max_corrections: int = 3,
        rule_engine: Optional[object] = None,
        calibrator: Optional[object] = None,
        semantic_retriever: Optional[object] = None,
        enable_rules: bool = True,
        enable_calibration: bool = True,
        enable_semantic_retrieval: bool = True,
    ):
        self._extractor = extractor or Extractor()
        self._corrections = correction_store or CorrectionStore()
        self._prompt_memory_model = prompt_memory_model or os.environ.get(
            "SELF_HEALING_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        )
        self._max_corrections = max_corrections

        # Feature flags for novel components
        self._enable_rules = enable_rules
        self._enable_calibration = enable_calibration
        self._enable_semantic_retrieval = enable_semantic_retrieval

        # Lazy initialization of novel components
        self._rule_engine = rule_engine
        self._calibrator = calibrator
        self._semantic_retriever = semantic_retriever

    @property
    def rule_engine(self):
        """Lazy-load rule engine to avoid import cost when not needed."""
        if self._rule_engine is None and self._enable_rules:
            try:
                from src.prompt_memory.rule_engine import RuleEngine

                self._rule_engine = RuleEngine()
            except ImportError:
                self._enable_rules = False
        return self._rule_engine

    @property
    def calibrator(self):
        """Lazy-load calibrator."""
        if self._calibrator is None and self._enable_calibration:
            try:
                from src.prompt_memory.calibration import ConfidenceCalibrator

                self._calibrator = ConfidenceCalibrator()
            except ImportError:
                self._enable_calibration = False
        return self._calibrator

    @property
    def semantic_retriever(self):
        """Lazy-load semantic retriever."""
        if self._semantic_retriever is None and self._enable_semantic_retrieval:
            try:
                from src.prompt_memory.semantic_retrieval import SemanticRetriever

                self._semantic_retriever = SemanticRetriever()
            except ImportError:
                self._enable_semantic_retrieval = False
        return self._semantic_retriever

    def extract_with_self_healing(
        self,
        document_text: str,
        field: FieldDefinition,
        document_type: str = "",
    ) -> ExtractionResult:
        """Extract a field using the full tiered pipeline.

        Args:
            document_text: Full document text.
            field: Field definition.
            document_type: Optional document type for targeted corrections.

        Returns:
            ExtractionResult (may have self_healed=True if retry was used).
        """
        # ── Tier 1: Check graduated rules ──
        if self._enable_rules and self.rule_engine is not None:
            rule_result = self.rule_engine.apply_rule(
                document_text, field, document_type
            )
            if rule_result is not None:
                logger.info(
                    "Field '%s': graduated rule matched, value='%s' (zero LLM cost)",
                    field.field_name,
                    rule_result.value,
                )
                return rule_result

        # ── Tier 2: Standard LLM extraction ──
        result = self._extractor.extract_field(document_text, field)

        # Get calibrated threshold (or use field default)
        threshold = field.confidence_threshold
        if self._enable_calibration and self.calibrator is not None:
            threshold = self.calibrator.get_threshold(
                field.field_name, default=field.confidence_threshold
            )
            # Record extraction for calibration tracking
            self.calibrator.record_extraction(
                field.field_name, result.confidence_score, field.confidence_threshold
            )

        # ── Check confidence against (calibrated) threshold ──
        if result.confidence_score >= threshold:
            logger.info(
                "Field '%s': confidence %.2f >= %.2f (threshold), accepting.",
                field.field_name,
                result.confidence_score,
                threshold,
            )
            return result

        logger.info(
            "Field '%s': confidence %.2f < %.2f, attempting self-healing.",
            field.field_name,
            result.confidence_score,
            threshold,
        )

        # ── Tier 3: Self-healing with corrections ──
        corrections = self._retrieve_corrections(
            document_text, field.field_name, document_type
        )

        if not corrections:
            logger.info(
                "Field '%s': no corrections available, returning original result.",
                field.field_name,
            )
            return result

        # Retry with few-shot examples from correction log
        healed_result = retry_with_corrections(
            extractor=self._extractor,
            document_text=document_text,
            field=field,
            corrections=corrections,
            model_id=self._prompt_memory_model,
        )

        # Use healed result if it improved confidence
        if healed_result.confidence_score > result.confidence_score:
            logger.info(
                "Field '%s': self-healing improved confidence %.2f → %.2f",
                field.field_name,
                result.confidence_score,
                healed_result.confidence_score,
            )
            return healed_result

        logger.info(
            "Field '%s': self-healing did not improve (%.2f → %.2f), keeping original.",
            field.field_name,
            result.confidence_score,
            healed_result.confidence_score,
        )
        return result

    def _retrieve_corrections(
        self,
        document_text: str,
        field_name: str,
        document_type: str,
    ) -> List[CorrectionRecord]:
        """Retrieve relevant corrections using semantic retrieval or key lookup.

        Tries semantic retrieval first (if enabled and indexed), falls back
        to key-based DynamoDB lookup.
        """
        # Try semantic retrieval first
        if self._enable_semantic_retrieval and self.semantic_retriever is not None:
            try:
                semantic_results = self.semantic_retriever.retrieve_for_field(
                    document_text=document_text,
                    field_name=field_name,
                    document_type=document_type,
                    limit=self._max_corrections,
                )
                if semantic_results:
                    logger.info(
                        "Field '%s': semantic retrieval returned %d corrections.",
                        field_name,
                        len(semantic_results),
                    )
                    return semantic_results
            except Exception as exc:
                logger.debug(
                    "Semantic retrieval failed for '%s': %s, falling back to key lookup.",
                    field_name,
                    exc,
                )

        # Fall back to key-based lookup
        return self._corrections.get_corrections(
            field_name=field_name,
            document_type=document_type or None,
            limit=self._max_corrections,
        )

    def record_correction_outcome(
        self, field_name: str, confidence_at_extraction: float
    ) -> None:
        """Record that a field was corrected by QA (for calibration).

        Call this when a correction is ingested to update the calibrator's
        understanding of model confidence vs. actual accuracy.
        """
        if self._enable_calibration and self.calibrator is not None:
            self.calibrator.record_correction(field_name, confidence_at_extraction)

    def trigger_rule_graduation(
        self, corrections: Optional[List[CorrectionRecord]] = None
    ) -> int:
        """Attempt to graduate correction patterns into deterministic rules.

        Args:
            corrections: List of corrections to analyze. If None, fetches from store.

        Returns:
            Number of new rules graduated.
        """
        if not self._enable_rules or self.rule_engine is None:
            return 0

        if corrections is None:
            # Fetch all corrections from store for analysis
            # In production, this would be a scheduled job, not per-request
            logger.info("Rule graduation requires corrections to be passed explicitly.")
            return 0

        new_rules = self.rule_engine.graduate_from_corrections(corrections)
        if new_rules:
            logger.info("Graduated %d new rules.", len(new_rules))
        return len(new_rules)
