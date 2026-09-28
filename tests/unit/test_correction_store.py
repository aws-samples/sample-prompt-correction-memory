"""Unit tests for the correction store."""

from unittest.mock import MagicMock

from src.extraction.models import CorrectionRecord
from src.prompt_memory.correction_store import CorrectionStore


class TestCorrectionStore:
    def test_get_corrections_queries_by_field_name(self):
        """Queries DynamoDB with field_name as the partition key."""
        table = MagicMock()
        table.query.return_value = {
            "Items": [
                {
                    "field_name": "payment_terms",
                    "timestamp": "2024-06-01T00:00:00Z",
                    "document_type": "MSA",
                    "original_value": "quarterly",
                    "corrected_value": "30",
                    "correction_reason": "Billing freq != payment terms",
                    "document_excerpt": "Payment due within 30 days.",
                }
            ]
        }

        store = CorrectionStore.__new__(CorrectionStore)
        store._table = table

        corrections = store.get_corrections("payment_terms", limit=3)

        assert len(corrections) == 1
        assert corrections[0].field_name == "payment_terms"
        assert corrections[0].corrected_value == "30"

        # Verify query params
        call_kwargs = table.query.call_args[1]
        assert call_kwargs["ScanIndexForward"] is False
        assert call_kwargs["Limit"] == 3

    def test_get_corrections_filters_by_document_type(self):
        """Adds FilterExpression when document_type is provided."""
        table = MagicMock()
        table.query.return_value = {"Items": []}

        store = CorrectionStore.__new__(CorrectionStore)
        store._table = table

        store.get_corrections("effective_date", document_type="Amendment")

        call_kwargs = table.query.call_args[1]
        assert "FilterExpression" in call_kwargs
        assert ":dt" in str(call_kwargs.get("ExpressionAttributeValues", {}))

    def test_put_correction(self):
        """Writes a correction record to DynamoDB."""
        table = MagicMock()

        store = CorrectionStore.__new__(CorrectionStore)
        store._table = table

        correction = CorrectionRecord(
            field_name="governing_law",
            document_type="NDA",
            original_value="New York",
            corrected_value="State of New York",
            correction_reason="Include 'State of' prefix for consistency.",
            document_excerpt="Governed by the laws of the State of New York.",
            timestamp="2024-06-15T12:00:00Z",
        )

        store.put_correction(correction)

        table.put_item.assert_called_once()
        item = table.put_item.call_args[1]["Item"]
        assert item["field_name"] == "governing_law"
        assert item["corrected_value"] == "State of New York"

    def test_empty_corrections_returns_empty_list(self):
        """Returns empty list when no corrections exist."""
        table = MagicMock()
        table.query.return_value = {"Items": []}

        store = CorrectionStore.__new__(CorrectionStore)
        store._table = table

        corrections = store.get_corrections("nonexistent_field")
        assert corrections == []
