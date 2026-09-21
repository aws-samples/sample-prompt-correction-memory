"""Unit tests for retry_with_examples."""

from unittest.mock import MagicMock

from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition
from src.prompt_memory.retry_with_examples import (
    format_corrections_as_examples,
    retry_with_corrections,
)


def test_format_corrections_as_examples():
    """Formats corrections into numbered examples."""
    corrections = [
        CorrectionRecord(
            field_name="payment_terms",
            document_type="MSA",
            original_value="quarterly",
            corrected_value="30",
            correction_reason="Billing frequency != payment terms.",
            document_excerpt="Payment due within 30 days.",
        ),
        CorrectionRecord(
            field_name="payment_terms",
            document_type="SOW",
            original_value="monthly",
            corrected_value="15",
            correction_reason="Monthly invoicing != payment deadline.",
            document_excerpt="Payment due within fifteen (15) days.",
        ),
    ]

    text = format_corrections_as_examples(corrections)

    assert "Example 1:" in text
    assert "Example 2:" in text
    assert "quarterly" in text
    assert "Correct answer: 30" in text
    assert "Correct answer: 15" in text


def test_retry_with_corrections_marks_self_healed():
    """Retry result has self_healed=True."""
    extractor = MagicMock()
    extractor.extract_field.return_value = ExtractionResult(
        field_name="payment_terms",
        value="30",
        confidence_score=0.88,
    )

    field = FieldDefinition(
        field_name="payment_terms",
        description="Payment terms",
        prompt="Extract payment terms.",
    )

    corrections = [
        CorrectionRecord(
            field_name="payment_terms",
            document_type="",
            original_value="quarterly",
            corrected_value="30",
            correction_reason="reason",
            document_excerpt="excerpt",
        )
    ]

    result = retry_with_corrections(
        extractor=extractor,
        document_text="some doc",
        field=field,
        corrections=corrections,
        model_id="sonnet-4",
    )

    assert result.self_healed is True
    assert result.value == "30"

    # Verify the prompt was enhanced
    call_kwargs = extractor.extract_field.call_args[1]
    assert "LEARN FROM PAST CORRECTIONS" in call_kwargs["prompt_override"]
    assert call_kwargs["model_id"] == "sonnet-4"
