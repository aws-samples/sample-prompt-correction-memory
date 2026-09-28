"""Unit tests for the extractor."""

from unittest.mock import MagicMock

from src.extraction.extractor import Extractor
from src.extraction.models import FieldDefinition


def _make_field():
    return FieldDefinition(
        field_name="effective_date",
        description="Agreement effective date",
        prompt="Extract the effective date in YYYY-MM-DD format.",
        data_type="date",
    )


def _mock_bedrock_response(value="2024-03-01", confidence=0.95):
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "input": {
                                "chain_of_thought": "Found in preamble.",
                                "value": value,
                                "reasoning": "Stated explicitly.",
                                "confidence_score": confidence,
                            }
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": 500, "outputTokens": 80},
    }


class TestExtractor:
    def test_successful_extraction(self):
        """Parses a successful Bedrock tool use response."""
        client = MagicMock()
        client.converse.return_value = _mock_bedrock_response()

        extractor = Extractor(bedrock_client=client)
        result = extractor.extract_field("document text", _make_field())

        assert result.value == "2024-03-01"
        assert result.confidence_score == 0.95
        assert result.field_name == "effective_date"
        assert result.input_tokens == 500
        assert result.output_tokens == 80
        assert result.cost_estimate > 0

    def test_extraction_reraises_bedrock_client_error(self):
        """On a Bedrock client error, the exception propagates so the message
        is retried and routed to the DLQ (not silently stored as an empty
        successful result)."""
        import pytest
        from botocore.exceptions import ClientError

        client = MagicMock()
        client.converse.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "Converse",
        )

        extractor = Extractor(bedrock_client=client)
        with pytest.raises(ClientError):
            extractor.extract_field("document text", _make_field())

    def test_prompt_override(self):
        """prompt_override replaces the field prompt in the request."""
        client = MagicMock()
        client.converse.return_value = _mock_bedrock_response()

        extractor = Extractor(bedrock_client=client)
        extractor.extract_field(
            "doc text", _make_field(), prompt_override="Custom prompt"
        )

        call_args = client.converse.call_args
        messages = call_args[1]["messages"]
        user_content = " ".join(b["text"] for b in messages[0]["content"])
        assert "Custom prompt" in user_content

    def test_model_override(self):
        """model_id parameter overrides the default model."""
        client = MagicMock()
        client.converse.return_value = _mock_bedrock_response()

        extractor = Extractor(bedrock_client=client)
        result = extractor.extract_field(
            "doc text", _make_field(), model_id="custom-model"
        )

        call_args = client.converse.call_args
        assert call_args[1]["modelId"] == "custom-model"
        assert result.model_id == "custom-model"


def test_infer_document_type_word_boundaries():
    """Classifier matches on word boundaries and aligns with sample docs (S5)."""
    from src.extraction.handler import _infer_document_type

    # "calendar" contains the substring "nda" but must NOT classify as NDA.
    assert _infer_document_type("Rent is due on the first calendar day.") == "Agreement"
    assert _infer_document_type("COMMERCIAL LEASE AGREEMENT ... lease") == "Lease"
    assert _infer_document_type("MUTUAL NON-DISCLOSURE AGREEMENT") == "NDA"
    assert _infer_document_type("MASTER SERVICE AGREEMENT") == "MSA"
    assert _infer_document_type("This First Amendment amends ...") == "Amendment"
