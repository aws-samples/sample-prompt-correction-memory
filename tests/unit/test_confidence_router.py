"""Unit tests for the confidence router."""

from unittest.mock import MagicMock, patch

from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition
from src.prompt_memory.confidence_router import ConfidenceRouter


def _make_field(threshold=0.7):
    return FieldDefinition(
        field_name="payment_terms",
        description="Payment terms in days",
        prompt="Extract payment terms as number of days.",
        confidence_threshold=threshold,
    )


def _make_result(confidence=0.9, value="30", self_healed=False):
    return ExtractionResult(
        field_name="payment_terms",
        value=value,
        confidence_score=confidence,
        self_healed=self_healed,
    )


class TestConfidenceRouter:
    def test_high_confidence_accepted_without_prompt_memory(self):
        """Results above threshold pass through without correction lookup."""
        extractor = MagicMock()
        extractor.extract_field.return_value = _make_result(confidence=0.9)

        correction_store = MagicMock()

        router = ConfidenceRouter(
            extractor=extractor,
            correction_store=correction_store,
        )

        result = router.extract_with_self_healing("doc text", _make_field())

        assert result.confidence_score == 0.9
        assert result.self_healed is False
        correction_store.get_corrections.assert_not_called()

    def test_low_confidence_triggers_correction_lookup(self):
        """Results below threshold trigger a correction log query."""
        extractor = MagicMock()
        extractor.extract_field.return_value = _make_result(
            confidence=0.4, value="quarterly"
        )

        correction_store = MagicMock()
        correction_store.get_corrections.return_value = [
            CorrectionRecord(
                field_name="payment_terms",
                document_type="",
                original_value="quarterly",
                corrected_value="30",
                correction_reason="Quarterly is billing frequency, not payment terms.",
                document_excerpt="Payment due within 30 days.",
            )
        ]

        # The retry should return a better result
        better_result = _make_result(confidence=0.88, value="30", self_healed=True)
        with patch(
            "src.prompt_memory.confidence_router.retry_with_corrections",
            return_value=better_result,
        ):
            router = ConfidenceRouter(
                extractor=extractor,
                correction_store=correction_store,
            )
            result = router.extract_with_self_healing("doc text", _make_field())

        assert result.confidence_score == 0.88
        assert result.value == "30"
        assert result.self_healed is True

    def test_no_corrections_returns_original(self):
        """When correction log is empty, returns original low-confidence result."""
        extractor = MagicMock()
        extractor.extract_field.return_value = _make_result(confidence=0.4)

        correction_store = MagicMock()
        correction_store.get_corrections.return_value = []

        router = ConfidenceRouter(
            extractor=extractor,
            correction_store=correction_store,
        )
        result = router.extract_with_self_healing("doc text", _make_field())

        assert result.confidence_score == 0.4
        assert result.self_healed is False

    def test_prompt_memory_no_improvement_keeps_original(self):
        """If retry doesn't improve confidence, keeps the original result."""
        extractor = MagicMock()
        extractor.extract_field.return_value = _make_result(confidence=0.5)

        correction_store = MagicMock()
        correction_store.get_corrections.return_value = [
            CorrectionRecord(
                field_name="payment_terms",
                document_type="",
                original_value="x",
                corrected_value="y",
                correction_reason="reason",
                document_excerpt="excerpt",
            )
        ]

        worse_result = _make_result(confidence=0.3, self_healed=True)
        with patch(
            "src.prompt_memory.confidence_router.retry_with_corrections",
            return_value=worse_result,
        ):
            router = ConfidenceRouter(
                extractor=extractor,
                correction_store=correction_store,
            )
            result = router.extract_with_self_healing("doc text", _make_field())

        assert result.confidence_score == 0.5
        assert result.self_healed is False
