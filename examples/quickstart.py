"""Quickstart: sample-prompt-correction-memory

Demonstrates the self-healing loop with mocked data. No AWS credentials needed.

Run:
    python examples/quickstart.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extraction.extractor import Extractor
from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition
from src.prompt_memory.confidence_router import ConfidenceRouter
from src.prompt_memory.correction_store import CorrectionStore
from src.prompt_memory.retry_with_examples import retry_with_corrections

# ---------------------------------------------------------------------------
# Mock Bedrock Client (no AWS credentials needed)
# ---------------------------------------------------------------------------


class MockBedrockClient:
    """Simulates Bedrock Converse API responses.

    First call returns a low-confidence result with a common error.
    Second call (with corrections in prompt) returns the correct value.
    """

    def __init__(self):
        self._call_count = 0

    def converse(self, **kwargs) -> Dict[str, Any]:
        self._call_count += 1
        messages = kwargs.get("messages", [])
        has_corrections = any(
            "LEARN FROM PAST CORRECTIONS" in block.get("text", "")
            for msg in messages
            for block in msg.get("content", [])
        )

        # Simulate: without corrections, model makes common mistakes
        if not has_corrections:
            return self._low_confidence_response()
        else:
            return self._high_confidence_response()

    def _low_confidence_response(self) -> Dict[str, Any]:
        """Simulates initial extraction with common error."""
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "input": {
                                    "chain_of_thought": "Looking for effective date in document preamble",
                                    "value": "March 2024",  # Common error: missing day
                                    "reasoning": "Found 'March 1, 2024' but unclear if referring to effective date",
                                    "confidence_score": 0.55,
                                }
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 450, "outputTokens": 80},
        }

    def _high_confidence_response(self) -> Dict[str, Any]:
        """Simulates self-healed extraction with correction examples."""
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "input": {
                                    "chain_of_thought": "Past corrections indicate ISO 8601 format with exact day is required",
                                    "value": "2024-03-01",  # Correct!
                                    "reasoning": "Applied lesson from corrections: use exact date in YYYY-MM-DD format",
                                    "confidence_score": 0.95,
                                }
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 650, "outputTokens": 85},
        }


# ---------------------------------------------------------------------------
# Mock Correction Store
# ---------------------------------------------------------------------------


class MockCorrectionStore:
    """In-memory correction store for demonstration."""

    def __init__(self, corrections: List[CorrectionRecord]):
        self._corrections = corrections

    def get_corrections(
        self,
        field_name: str,
        document_type: Optional[str] = None,
        limit: int = 3,
    ) -> List[CorrectionRecord]:
        matching = [c for c in self._corrections if c.field_name == field_name]
        if document_type:
            typed = [c for c in matching if c.document_type == document_type]
            if typed:
                matching = typed
        return matching[:limit]


# ---------------------------------------------------------------------------
# Main Demo
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("sample-prompt-correction-memory — Quickstart Demo")
    print("=" * 60)
    print()

    # 1. Define a field to extract
    field_def = FieldDefinition(
        field_name="effective_date",
        description="Agreement effective date",
        prompt=(
            "Extract the effective date when this agreement becomes active. "
            "Return in YYYY-MM-DD format. Look in the preamble, recitals, or signature block."
        ),
        data_type="date",
        confidence_threshold=0.7,
    )

    # 2. Sample document text
    document_text = """
    MASTER SERVICE AGREEMENT

    This Master Service Agreement ("Agreement") is entered into as of
    March 1, 2024 ("Effective Date") by and between:

    Acme Corporation, a Delaware corporation ("Client")
    and
    GlobalTech Solutions Inc., a California corporation ("Provider")
    """

    # 3. Seed corrections from past QA reviews
    corrections = [
        CorrectionRecord(
            field_name="effective_date",
            document_type="service-agreement",
            original_value="March 2024",
            corrected_value="2024-03-01",
            correction_reason="Use ISO 8601 format (YYYY-MM-DD) with exact day from preamble",
            document_excerpt='entered into as of March 1, 2024 ("Effective Date")',
            timestamp="2024-02-15T10:00:00Z",
        ),
        CorrectionRecord(
            field_name="effective_date",
            document_type="lease-agreement",
            original_value="July 2024",
            corrected_value="2024-07-15",
            correction_reason="Extract exact day, not just month/year approximation",
            document_excerpt="made effective as of July 15, 2024",
            timestamp="2024-02-20T14:30:00Z",
        ),
    ]

    # 4. Create components with mocked client
    mock_client = MockBedrockClient()
    extractor = Extractor(bedrock_client=mock_client)
    correction_store = MockCorrectionStore(corrections)

    # --- Step A: Extract WITHOUT self-healing (simulate no corrections available) ---
    print("Step 1: Initial extraction (no correction memory)")
    print("-" * 50)
    result_initial = extractor.extract_field(document_text, field_def)
    print(f"  Field:      {result_initial.field_name}")
    print(f"  Value:      {result_initial.value}")
    print(f"  Confidence: {result_initial.confidence_score:.2f}")
    print(
        f"  Correct?    {'❌ NO' if result_initial.value != '2024-03-01' else '✓ YES'}"
    )
    print()

    # --- Step B: Self-healing triggers (confidence < threshold) ---
    print("Step 2: Self-healing triggers (confidence 0.55 < threshold 0.70)")
    print("-" * 50)
    print(f"  Retrieving corrections for field '{field_def.field_name}'...")
    relevant_corrections = correction_store.get_corrections(
        field_name=field_def.field_name,
        document_type="service-agreement",
    )
    print(f"  Found {len(relevant_corrections)} relevant corrections:")
    for i, c in enumerate(relevant_corrections, 1):
        print(f'    {i}. "{c.original_value}" → "{c.corrected_value}"')
        print(f"       Reason: {c.correction_reason}")
    print()

    # --- Step C: Re-extract with corrections as few-shot examples ---
    print("Step 3: Re-extraction with correction memory (few-shot examples)")
    print("-" * 50)
    result_healed = retry_with_corrections(
        extractor=extractor,
        document_text=document_text,
        field=field_def,
        corrections=relevant_corrections,
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    )
    print(f"  Field:       {result_healed.field_name}")
    print(f"  Value:       {result_healed.value}")
    print(f"  Confidence:  {result_healed.confidence_score:.2f}")
    print(f"  Self-Healed: {result_healed.self_healed}")
    print(
        f"  Correct?     {'✓ YES' if result_healed.value == '2024-03-01' else '❌ NO'}"
    )
    print()

    # --- Summary ---
    print("=" * 60)
    print("Summary: The Self-Healing Loop")
    print("=" * 60)
    print()
    print(
        f'  Before corrections: "{result_initial.value}" (confidence {result_initial.confidence_score:.2f})'
    )
    print(
        f'  After corrections:  "{result_healed.value}" (confidence {result_healed.confidence_score:.2f})'
    )
    print()
    print("  What happened:")
    print("  1. Initial extraction returned 'March 2024' with low confidence (0.55)")
    print("  2. Confidence was below threshold (0.70), triggering self-healing")
    print("  3. System retrieved 2 relevant corrections from the correction log")
    print("  4. Re-extracted with corrections as few-shot examples in the prompt")
    print("  5. Model learned from past mistakes and returned '2024-03-01' (0.95)")
    print()
    print("  The correction that fixed this was uploaded by a QA analyst.")
    print("  No retraining. No redeployment. The fix is permanent and immediate.")
    print()


if __name__ == "__main__":
    main()
