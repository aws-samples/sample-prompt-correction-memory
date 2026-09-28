"""Local end-to-end pipeline test — no AWS account required.

Exercises the real extraction -> confidence-gate -> self-healing loop and the
real DynamoDB-backed CorrectionStore using:
  - a scripted fake Bedrock Converse client (deterministic, offline), and
  - moto's in-memory DynamoDB.

This provides a way to test the full workflow (the thing the sample
demonstrates) without deploying to AWS or calling a real LLM.
"""

from __future__ import annotations

import json

import boto3
import pytest

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from src.extraction.extractor import Extractor  # noqa: E402
from src.extraction.models import CorrectionRecord, FieldDefinition  # noqa: E402
from src.prompt_memory.confidence_router import ConfidenceRouter  # noqa: E402
from src.prompt_memory.correction_store import CorrectionStore  # noqa: E402

TABLE_NAME = "correction-log-test"


def _tool_response(value, confidence, input_tokens=400, output_tokens=80):
    """Build a Bedrock Converse response carrying a tool-use payload."""
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "input": {
                                "value": value,
                                "confidence_score": confidence,
                                "chain_of_thought": "scripted",
                                "reasoning": "scripted",
                            }
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


class ScriptedBedrockClient:
    """A fake bedrock-runtime client whose converse() returns queued responses.

    The response chosen depends on whether the prompt already contains
    correction examples (i.e. a self-healing retry), so we can model the
    "low confidence first, high confidence after corrections" behaviour.
    """

    def __init__(self, first, healed):
        self._first = first
        self._healed = healed
        self.calls = []

    def converse(self, **kwargs):
        # Detect a self-heal retry by the correction-example delimiter.
        text_blocks = json.dumps(kwargs.get("messages", []))
        self.calls.append(text_blocks)
        if (
            "<correction_example" in text_blocks
            or "LEARN FROM PAST CORRECTIONS" in text_blocks
        ):
            return self._healed
        return self._first


def _make_table(dynamodb):
    dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "field_name", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "field_name", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _field():
    return FieldDefinition(
        field_name="effective_date",
        description="Agreement effective date",
        prompt="Extract the effective date in YYYY-MM-DD format.",
        data_type="date",
        confidence_threshold=0.7,
    )


@mock_aws
def test_high_confidence_is_accepted_without_self_heal():
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    _make_table(dynamodb)
    store = CorrectionStore(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    client = ScriptedBedrockClient(
        first=_tool_response("2024-03-01", 0.95),
        healed=_tool_response("SHOULD_NOT_BE_USED", 0.99),
    )
    router = ConfidenceRouter(
        extractor=Extractor(bedrock_client=client),
        correction_store=store,
        enable_rules=False,
        enable_calibration=False,
        enable_semantic_retrieval=False,
    )

    result = router.extract_with_self_healing("... as of March 1, 2024 ...", _field())

    assert result.value == "2024-03-01"
    assert result.self_healed is False
    assert len(client.calls) == 1  # no retry


@mock_aws
def test_low_confidence_triggers_self_heal_using_stored_correction():
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    _make_table(dynamodb)
    store = CorrectionStore(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    # Seed a correction the pipeline can retrieve for self-healing.
    store.put_correction(
        CorrectionRecord(
            field_name="effective_date",
            document_type="MSA",
            original_value="March 2024",
            corrected_value="2024-03-01",
            correction_reason="Return ISO 8601 dates.",
            document_excerpt="effective as of March 1, 2024",
            timestamp="2026-01-01T00:00:00.000Z",
        )
    )

    client = ScriptedBedrockClient(
        first=_tool_response("March 2024", 0.45),
        healed=_tool_response("2024-03-01", 0.95),
    )
    router = ConfidenceRouter(
        extractor=Extractor(bedrock_client=client),
        correction_store=store,
        enable_rules=False,
        enable_calibration=False,
        enable_semantic_retrieval=False,
    )

    result = router.extract_with_self_healing(
        "... effective as of March 1, 2024 ...", _field(), document_type="MSA"
    )

    assert result.value == "2024-03-01"
    assert result.self_healed is True
    assert len(client.calls) == 2  # first + healed retry


@mock_aws
def test_self_heal_with_no_corrections_returns_original():
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    _make_table(dynamodb)
    store = CorrectionStore(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    client = ScriptedBedrockClient(
        first=_tool_response("March 2024", 0.45),
        healed=_tool_response("2024-03-01", 0.95),
    )
    router = ConfidenceRouter(
        extractor=Extractor(bedrock_client=client),
        correction_store=store,
        enable_rules=False,
        enable_calibration=False,
        enable_semantic_retrieval=False,
    )

    result = router.extract_with_self_healing("no date here", _field())

    # No corrections stored -> keeps the original low-confidence result.
    assert result.value == "March 2024"
    assert result.self_healed is False
    assert len(client.calls) == 1


@mock_aws
def test_correction_store_roundtrip_and_document_type_filter():
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    _make_table(dynamodb)
    store = CorrectionStore(table_name=TABLE_NAME, dynamodb_resource=dynamodb)

    for i, dt in enumerate(["MSA", "NDA"]):
        store.put_correction(
            CorrectionRecord(
                field_name="party_a",
                document_type=dt,
                original_value=f"o{i}",
                corrected_value=f"c{i}",
                correction_reason="r",
                document_excerpt="e",
                timestamp=f"2026-01-0{i + 1}T00:00:00.000Z",
            )
        )

    all_corr = store.get_corrections("party_a", limit=10)
    assert len(all_corr) == 2

    msa_only = store.get_corrections("party_a", document_type="MSA", limit=10)
    assert len(msa_only) == 1
    assert msa_only[0].document_type == "MSA"
