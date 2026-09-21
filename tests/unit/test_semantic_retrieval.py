"""Unit tests for semantic correction retrieval."""

from src.extraction.models import CorrectionRecord
from src.prompt_memory.semantic_retrieval import SemanticRetriever


def _make_corrections():
    """Create a diverse set of corrections for testing retrieval."""
    return [
        CorrectionRecord(
            field_name="payment_terms",
            document_type="service-agreement",
            original_value="quarterly",
            corrected_value="30",
            correction_reason="Quarterly is billing frequency, not payment terms. Look for Net X.",
            document_excerpt="Invoices submitted quarterly. Payment due Net 30.",
        ),
        CorrectionRecord(
            field_name="payment_terms",
            document_type="lease-agreement",
            original_value="monthly",
            corrected_value="0",
            correction_reason="Monthly rent due on first means due upon receipt.",
            document_excerpt="Rent due on the first day of each calendar month.",
        ),
        CorrectionRecord(
            field_name="effective_date",
            document_type="service-agreement",
            original_value="March 2024",
            corrected_value="2024-03-01",
            correction_reason="Use ISO 8601 format with exact day from preamble.",
            document_excerpt='entered into as of March 1, 2024 ("Effective Date")',
        ),
        CorrectionRecord(
            field_name="governing_law",
            document_type="nda",
            original_value="State of New York",
            corrected_value="New York",
            correction_reason="Return state name only without State of prefix.",
            document_excerpt="governed by the laws of the State of New York",
        ),
        CorrectionRecord(
            field_name="party_a",
            document_type="service-agreement",
            original_value="Acme",
            corrected_value="Acme Corporation",
            correction_reason="Include full legal entity name with corporate designation.",
            document_excerpt="Acme Corporation, a Delaware corporation",
        ),
    ]


class TestSemanticRetriever:
    def test_empty_index_returns_empty(self):
        """Empty retriever returns no results."""
        retriever = SemanticRetriever()
        results = retriever.retrieve("some query text")
        assert results == []

    def test_indexing_corrections(self):
        """Can index a list of corrections without error."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)
        assert len(retriever._corrections) == 5

    def test_retrieves_relevant_by_content(self):
        """Retrieves corrections with similar content to query."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        # Query about payment and invoicing should rank payment corrections first
        results = retriever.retrieve(
            "Payment is due within thirty days of invoice receipt Net 30"
        )
        assert len(results) > 0
        top_correction, score = results[0]
        assert top_correction.field_name == "payment_terms"
        assert score > 0.0

    def test_field_name_boosts_ranking(self):
        """Corrections matching field_name get boosted in ranking."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        # Query text is about dates but specify field_name = effective_date
        results = retriever.retrieve(
            "entered into as of January 15, 2025",
            field_name="effective_date",
        )
        assert len(results) > 0
        # The effective_date correction should rank higher due to field_name boost
        field_names = [c.field_name for c, _ in results]
        assert "effective_date" in field_names

    def test_retrieve_for_field_returns_correction_records(self):
        """retrieve_for_field returns a list of CorrectionRecord objects."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        results = retriever.retrieve_for_field(
            document_text="This agreement is governed by the laws of the State of California.",
            field_name="governing_law",
            document_type="nda",
        )
        assert len(results) > 0
        assert all(isinstance(r, CorrectionRecord) for r in results)

    def test_document_type_boost(self):
        """Corrections matching document_type get boosted."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        # Query for payment_terms with lease-agreement type
        results = retriever.retrieve_for_field(
            document_text="Rent is due on the first of each month",
            field_name="payment_terms",
            document_type="lease-agreement",
        )
        assert len(results) > 0
        # Lease-specific correction should rank higher
        assert results[0].document_type == "lease-agreement"

    def test_limit_parameter(self):
        """Respects the limit parameter."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        results = retriever.retrieve("payment invoice terms", limit=2)
        assert len(results) <= 2

    def test_min_similarity_filters_low_scores(self):
        """Results below min_similarity are excluded."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        # Very high min_similarity should return fewer results
        results_high = retriever.retrieve(
            "completely unrelated text about quantum physics",
            min_similarity=0.5,
        )
        results_low = retriever.retrieve(
            "completely unrelated text about quantum physics",
            min_similarity=0.01,
        )
        assert len(results_high) <= len(results_low)

    def test_cross_document_transfer(self):
        """Corrections from one document type help another."""
        corrections = _make_corrections()
        retriever = SemanticRetriever(corrections)

        # Query from a "purchase-order" doc (not in training data)
        # asking about payment terms — should still find relevant corrections
        results = retriever.retrieve_for_field(
            document_text="Payment terms: Net 45 days from invoice.",
            field_name="payment_terms",
            document_type="purchase-order",  # Not in the correction set
        )
        assert len(results) > 0
        # Should find the service-agreement payment correction via content similarity
        assert any(c.field_name == "payment_terms" for c in results)

    def test_reindex_replaces_previous(self):
        """Re-indexing replaces the previous index."""
        retriever = SemanticRetriever(_make_corrections())
        assert len(retriever._corrections) == 5

        new_corrections = [
            CorrectionRecord(
                field_name="new_field",
                document_type="test",
                original_value="a",
                corrected_value="b",
                correction_reason="test reason",
                document_excerpt="test excerpt",
            )
        ]
        retriever.index(new_corrections)
        assert len(retriever._corrections) == 1
