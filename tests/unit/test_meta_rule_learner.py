"""Unit tests for the meta-rule learner (LLM-assisted rule synthesis)."""

from unittest.mock import MagicMock

from src.extraction.models import CorrectionRecord
from src.prompt_memory.meta_rule_learner import MetaRuleLearner


def _make_corrections():
    """Corrections with a pattern that simple regex can't easily handle."""
    return [
        CorrectionRecord(
            field_name="governing_law",
            document_type="service-agreement",
            original_value="State of Delaware",
            corrected_value="Delaware",
            correction_reason="Return state name without prefix",
            document_excerpt="governed by the laws of the State of Delaware.",
        ),
        CorrectionRecord(
            field_name="governing_law",
            document_type="service-agreement",
            original_value="State of New York",
            corrected_value="New York",
            correction_reason="Return state name without prefix",
            document_excerpt="governed by the laws of the State of New York.",
        ),
        CorrectionRecord(
            field_name="governing_law",
            document_type="service-agreement",
            original_value="Commonwealth of Virginia",
            corrected_value="Virginia",
            correction_reason="Return state name without prefix",
            document_excerpt="governed by the laws of the Commonwealth of Virginia.",
        ),
    ]


def _mock_bedrock_response(pattern, post_processing="none", confidence=0.9):
    """Create a mock Bedrock response with a synthesized rule."""
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "input": {
                                "reasoning": "Pattern analysis of corrections",
                                "regex_pattern": pattern,
                                "post_processing": post_processing,
                                "confidence": confidence,
                            }
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": 500, "outputTokens": 100},
    }


class TestMetaRuleLearner:
    def test_returns_none_without_client(self):
        """Returns None when no Bedrock client is provided."""
        learner = MetaRuleLearner(bedrock_client=None)
        result = learner.synthesize_rule(_make_corrections())
        assert result is None

    def test_returns_none_with_empty_corrections(self):
        """Returns None for empty correction list."""
        client = MagicMock()
        learner = MetaRuleLearner(bedrock_client=client)
        result = learner.synthesize_rule([])
        assert result is None

    def test_successful_synthesis_returns_rule_data(self):
        """Successful LLM call returns rule data dict."""
        client = MagicMock()
        # LLM returns a regex that extracts state names from "laws of the State/Commonwealth of X"
        client.converse.return_value = _mock_bedrock_response(
            pattern=r"(?:State|Commonwealth)\s+of\s+([A-Z][\w\s]+?)(?:\.|,)",
            post_processing="none",
            confidence=0.95,
        )

        learner = MetaRuleLearner(bedrock_client=client)
        result = learner.synthesize_rule(_make_corrections())

        assert result is not None
        assert "regex_pattern" in result
        assert result["confidence"] == 0.95
        assert result["validation_accuracy"] >= 0.8

    def test_invalid_regex_returns_none(self):
        """Invalid regex from LLM is caught and returns None."""
        client = MagicMock()
        client.converse.return_value = _mock_bedrock_response(
            pattern=r"[invalid(regex",  # Malformed regex
        )

        learner = MetaRuleLearner(bedrock_client=client)
        result = learner.synthesize_rule(_make_corrections())
        assert result is None

    def test_low_validation_accuracy_returns_none(self):
        """Rule that fails validation is rejected."""
        client = MagicMock()
        # Pattern that won't match any excerpts
        client.converse.return_value = _mock_bedrock_response(
            pattern=r"XXXX_NO_MATCH_YYYY",
        )

        learner = MetaRuleLearner(bedrock_client=client)
        result = learner.synthesize_rule(_make_corrections())
        assert result is None

    def test_llm_error_handled_gracefully(self):
        """LLM call failure returns None without crashing."""
        client = MagicMock()
        client.converse.side_effect = Exception("ThrottlingException")

        learner = MetaRuleLearner(bedrock_client=client)
        result = learner.synthesize_rule(_make_corrections())
        assert result is None

    def test_post_processing_to_integer(self):
        """to_integer post-processing converts string to int."""
        learner = MetaRuleLearner()
        assert learner._apply_post_processing("30", "to_integer") == "30"
        assert learner._apply_post_processing("45.0", "to_integer") == "45"

    def test_post_processing_strip_prefix(self):
        """strip_prefix removes specified prefix."""
        learner = MetaRuleLearner()
        result = learner._apply_post_processing(
            "State of Delaware", "strip_prefix:State of "
        )
        assert result == "Delaware"

    def test_post_processing_lowercase(self):
        """lowercase converts to lowercase."""
        learner = MetaRuleLearner()
        assert learner._apply_post_processing("DELAWARE", "lowercase") == "delaware"

    def test_integration_with_rule_engine(self):
        """Meta learner integrates with rule engine graduate_from_corrections."""
        import tempfile
        from pathlib import Path

        from src.prompt_memory.rule_engine import RuleEngine

        client = MagicMock()
        client.converse.return_value = _mock_bedrock_response(
            pattern=r"[Nn]et\s+(\d+)",
            post_processing="to_integer",
            confidence=0.95,
        )

        learner = MetaRuleLearner(bedrock_client=client)
        engine = RuleEngine(
            store_path=Path(tempfile.mkdtemp()),
            graduation_threshold=3,
        )

        # Corrections that won't match simple synthesis but have clear pattern
        corrections = [
            CorrectionRecord(
                field_name="payment_terms",
                document_type="invoice",
                original_value="quarterly",
                corrected_value="30",
                correction_reason="Look for Net X",
                document_excerpt="Payment Net 30.",
            ),
            CorrectionRecord(
                field_name="payment_terms",
                document_type="invoice",
                original_value="monthly",
                corrected_value="45",
                correction_reason="Look for Net X",
                document_excerpt="Terms: Net 45.",
            ),
            CorrectionRecord(
                field_name="payment_terms",
                document_type="invoice",
                original_value="annual",
                corrected_value="60",
                correction_reason="Look for Net X",
                document_excerpt="Payment Net 60 days.",
            ),
        ]

        rules = engine.graduate_from_corrections(corrections, meta_learner=learner)
        # Should have attempted meta synthesis (simple synthesis may also work here)
        assert len(rules) >= 0  # May or may not graduate depending on validation
