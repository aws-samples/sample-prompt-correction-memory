"""Integration test: end-to-end sample-prompt-correction-memory extraction pipeline.

Tests the full flow from document → extraction → self-healing → rule graduation
using mocked Bedrock responses. No AWS credentials needed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from src.extraction.extractor import Extractor
from src.extraction.models import CorrectionRecord, FieldDefinition
from src.prompt_memory.calibration import ConfidenceCalibrator
from src.prompt_memory.confidence_router import ConfidenceRouter
from src.prompt_memory.rule_engine import RuleEngine
from src.prompt_memory.semantic_retrieval import SemanticRetriever

# ---------------------------------------------------------------------------
# Mock Bedrock Client
# ---------------------------------------------------------------------------


class MockBedrock:
    """Configurable mock that returns different results based on prompt content."""

    def __init__(self):
        self.call_count = 0

    def converse(self, **kwargs) -> Dict[str, Any]:
        self.call_count += 1
        messages = kwargs.get("messages", [])
        has_corrections = any(
            "LEARN FROM PAST CORRECTIONS" in block.get("text", "")
            for msg in messages
            for block in msg.get("content", [])
        )

        if has_corrections:
            return self._response("30", 0.92)
        else:
            return self._response("quarterly", 0.45)

    def _response(self, value: str, confidence: float) -> Dict[str, Any]:
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "input": {
                                    "chain_of_thought": "analysis",
                                    "value": value,
                                    "reasoning": "reasoning",
                                    "confidence_score": confidence,
                                }
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 400, "outputTokens": 80},
        }


# ---------------------------------------------------------------------------
# Mock Correction Store
# ---------------------------------------------------------------------------


class MockCorrectionStore:
    def __init__(self, corrections: List[CorrectionRecord]):
        self._corrections = corrections

    def get_corrections(
        self, field_name: str, document_type: Optional[str] = None, limit: int = 3
    ) -> List[CorrectionRecord]:
        matching = [c for c in self._corrections if c.field_name == field_name]
        if document_type:
            typed = [c for c in matching if c.document_type == document_type]
            if typed:
                matching = typed
        return matching[:limit]


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

DOCUMENT_TEXT = """
MASTER SERVICE AGREEMENT
This Agreement is entered into as of March 1, 2024 by and between
Acme Corporation and GlobalTech Solutions Inc.
Payment is due within thirty (30) days of receipt of each invoice (Net 30).
Governed by the laws of the State of Delaware.
"""

CORRECTIONS = [
    CorrectionRecord(
        field_name="payment_terms",
        document_type="service-agreement",
        original_value="quarterly",
        corrected_value="30",
        correction_reason="Quarterly is billing frequency, not payment terms. Look for Net X.",
        document_excerpt="Payment due Net 30 from invoice date.",
        timestamp="2024-01-01T10:00:00Z",
    ),
    CorrectionRecord(
        field_name="payment_terms",
        document_type="service-agreement",
        original_value="monthly",
        corrected_value="30",
        correction_reason="Monthly is billing cycle. Payment terms are Net 30.",
        document_excerpt="Invoiced monthly. Payment due Net 30.",
        timestamp="2024-01-02T10:00:00Z",
    ),
    CorrectionRecord(
        field_name="payment_terms",
        document_type="lease-agreement",
        original_value="thirty days",
        corrected_value="30",
        correction_reason="Return numeric value only.",
        document_excerpt="Payment within thirty (30) days. Net 30 terms.",
        timestamp="2024-01-03T10:00:00Z",
    ),
    CorrectionRecord(
        field_name="payment_terms",
        document_type="service-agreement",
        original_value="net thirty",
        corrected_value="30",
        correction_reason="Convert text to integer.",
        document_excerpt="Terms are Net 30 days from invoice.",
        timestamp="2024-01-04T10:00:00Z",
    ),
    CorrectionRecord(
        field_name="payment_terms",
        document_type="service-agreement",
        original_value="net 30",
        corrected_value="30",
        correction_reason="Remove 'Net' prefix, return just the number.",
        document_excerpt="Payment due Net 30.",
        timestamp="2024-01-05T10:00:00Z",
    ),
]

FIELD = FieldDefinition(
    field_name="payment_terms",
    description="Payment terms in days",
    prompt="Extract payment terms as number of days.",
    data_type="float",
    confidence_threshold=0.7,
)


class TestEndToEndPromptMemory:
    """Tests the complete self-healing flow without any AWS calls."""

    def test_basic_self_healing_flow(self):
        """Low confidence triggers self-healing and produces correct result."""
        mock_bedrock = MockBedrock()
        extractor = Extractor(bedrock_client=mock_bedrock)
        store = MockCorrectionStore(CORRECTIONS[:3])

        router = ConfidenceRouter(
            extractor=extractor,
            correction_store=store,
            enable_rules=False,
            enable_calibration=False,
            enable_semantic_retrieval=False,
        )

        result = router.extract_with_self_healing(
            DOCUMENT_TEXT, FIELD, "service-agreement"
        )

        assert result.value == "30"
        assert result.self_healed is True
        assert result.confidence_score == 0.92
        # Two calls: initial (low confidence) + retry (with corrections)
        assert mock_bedrock.call_count == 2

    def test_rule_graduation_eliminates_llm_calls(self):
        """After graduation, extraction happens without any LLM call."""
        tmp = tempfile.mkdtemp()
        rule_engine = RuleEngine(store_path=Path(tmp), graduation_threshold=4)

        # Use only corrections with same document_type to meet threshold
        same_type = [c for c in CORRECTIONS if c.document_type == "service-agreement"]
        # Ensure we have enough
        assert len(same_type) >= 4

        new_rules = rule_engine.graduate_from_corrections(same_type)
        assert len(new_rules) >= 1

        # Now create router with rule engine
        mock_bedrock = MockBedrock()
        extractor = Extractor(bedrock_client=mock_bedrock)

        router = ConfidenceRouter(
            extractor=extractor,
            correction_store=MockCorrectionStore([]),
            rule_engine=rule_engine,
            enable_calibration=False,
            enable_semantic_retrieval=False,
        )

        result = router.extract_with_self_healing(
            DOCUMENT_TEXT, FIELD, "service-agreement"
        )

        # Rule should fire — zero LLM calls
        assert result.value == "30"
        assert result.model_id == "deterministic"
        assert result.cost_estimate == 0.0
        assert mock_bedrock.call_count == 0

    def test_semantic_retrieval_finds_cross_type_corrections(self):
        """Semantic retrieval surfaces corrections from other document types."""
        corrections = CORRECTIONS[:3]
        retriever = SemanticRetriever(corrections)

        # Query from a new document type not in corrections
        results = retriever.retrieve_for_field(
            document_text="Payment due within Net 45 days of invoice.",
            field_name="payment_terms",
            document_type="purchase-order",  # Not in correction set
        )

        # Should find relevant corrections via content similarity
        assert len(results) > 0
        assert all(c.field_name == "payment_terms" for c in results)

    def test_calibration_adjusts_threshold_over_time(self):
        """Calibrator raises threshold for overconfident fields."""
        tmp = tempfile.mkdtemp()
        calibrator = ConfidenceCalibrator(
            store_path=Path(tmp),
            min_samples=5,
            adjustment_rate=0.05,
            margin=0.05,
        )

        # Simulate: model reports 0.85 confidence but is corrected 40% of the time
        for _ in range(10):
            calibrator.record_extraction("bad_field", 0.85, original_threshold=0.7)
        for _ in range(4):
            calibrator.record_correction("bad_field", 0.85)

        # Threshold should have increased above original 0.7
        new_threshold = calibrator.get_threshold("bad_field", default=0.7)
        assert new_threshold > 0.7

    def test_full_pipeline_with_all_features(self):
        """All features work together: rules → calibration → semantic → self-heal."""
        tmp = tempfile.mkdtemp()

        # Setup all components
        rule_engine = RuleEngine(
            store_path=Path(tmp) / "rules", graduation_threshold=10
        )
        calibrator = ConfidenceCalibrator(store_path=Path(tmp) / "cal", min_samples=20)
        retriever = SemanticRetriever(CORRECTIONS)

        mock_bedrock = MockBedrock()
        extractor = Extractor(bedrock_client=mock_bedrock)
        store = MockCorrectionStore(CORRECTIONS)

        router = ConfidenceRouter(
            extractor=extractor,
            correction_store=store,
            rule_engine=rule_engine,
            calibrator=calibrator,
            semantic_retriever=retriever,
        )

        # No graduated rules yet (threshold=10, only 5 corrections)
        # Should fall through to LLM → self-heal
        result = router.extract_with_self_healing(
            DOCUMENT_TEXT, FIELD, "service-agreement"
        )

        assert result.value == "30"
        assert result.self_healed is True
        assert mock_bedrock.call_count == 2

    def test_feedback_handler_processes_eventbridge_event(self):
        """Feedback handler correctly processes EventBridge direct invocation."""
        import json
        from unittest.mock import patch

        # Simulate EventBridge event format
        event = {
            "version": "0",
            "detail-type": "Object Created",
            "source": "aws.s3",
            "detail": {
                "bucket": {"name": "test-bucket"},
                "object": {"key": "corrections/test.json"},
            },
        }

        correction_json = json.dumps(
            {
                "field_name": "payment_terms",
                "document_type": "MSA",
                "original_value": "quarterly",
                "corrected_value": "30",
                "correction_reason": "Billing frequency != payment terms",
                "document_excerpt": "Payment due Net 30.",
            }
        )

        with (
            patch("src.feedback.handler.boto3") as mock_boto3,
            patch("src.feedback.handler.CorrectionStore") as mock_store_cls,
        ):

            # Mock S3 client
            mock_s3 = MagicMock()
            mock_s3.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=correction_json.encode()))
            }
            mock_boto3.client.return_value = mock_s3

            # Mock correction store
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store

            from src.feedback.handler import handler

            result = handler(event, None)

            assert result["status"] == "success"
            assert result["records_processed"] == 1
            mock_store.put_correction.assert_called_once()
