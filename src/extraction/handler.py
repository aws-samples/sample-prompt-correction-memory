"""Lambda handler: extracts fields from documents uploaded to S3."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3

from src.extraction.models import FieldDefinition
from src.prompt_memory.confidence_router import ConfidenceRouter

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def handler(event, context):
    """Process an S3 event or SQS message containing document upload info.

    Extracts all configured fields from the document and writes results
    to the extraction-results DynamoDB table.
    """
    s3_client = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")
    results_table = dynamodb.Table(
        os.environ.get("RESULTS_TABLE", "extraction-results")
    )
    router = ConfidenceRouter()

    # Load field definitions
    fields = _load_field_definitions()

    processed = []

    for record in _get_records(event):
        bucket, key = _extract_s3_info(record)
        if not bucket or not key:
            continue

        # Download document text
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            document_text = response["Body"].read().decode("utf-8")
        except Exception as exc:
            logger.error("Failed to read s3://%s/%s: %s", bucket, key, exc)
            continue

        document_id = hashlib.sha256(document_text.encode()).hexdigest()
        document_type = _infer_document_type(document_text)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        logger.info(
            "Extracting %d fields from s3://%s/%s (doc_id=%s)",
            len(fields),
            bucket,
            key,
            document_id[:12],
        )

        # Extract each field with self-healing
        for field in fields:
            result = router.extract_with_self_healing(
                document_text=document_text,
                field=field,
                document_type=document_type,
            )

            # Write result to DynamoDB
            item = result.to_dynamodb_item(document_id)
            item["timestamp"] = timestamp
            item["s3_key"] = f"s3://{bucket}/{key}"

            try:
                results_table.put_item(Item=item)
            except Exception as exc:
                logger.error(
                    "Failed to write result for '%s': %s", field.field_name, exc
                )

            processed.append(
                {
                    "field": field.field_name,
                    "value": result.value,
                    "confidence": result.confidence_score,
                    "self_healed": result.self_healed,
                }
            )

    return {
        "status": "success",
        "document_count": len(set(r.get("s3_key", "") for r in _get_records(event))),
        "fields_extracted": len(processed),
        "results": processed,
    }


def _load_field_definitions() -> List[FieldDefinition]:
    """Load field definitions from environment or local file."""
    defs_path = os.environ.get(
        "FIELD_DEFINITIONS_PATH", "sample_data/field-definitions.json"
    )
    try:
        with open(defs_path) as f:
            data = json.load(f)
        return [FieldDefinition.from_dict(d) for d in data]
    except FileNotFoundError:
        logger.warning("Field definitions not found at %s, using defaults", defs_path)
        return _default_fields()


def _default_fields() -> List[FieldDefinition]:
    """Minimal field set for demo purposes."""
    return [
        FieldDefinition(
            field_name="effective_date",
            description="Agreement effective date",
            prompt="Extract the effective date in YYYY-MM-DD format.",
            data_type="date",
        ),
        FieldDefinition(
            field_name="party_a",
            description="First named party",
            prompt="Extract the full legal name of the first party.",
            data_type="str",
        ),
        FieldDefinition(
            field_name="party_b",
            description="Second named party",
            prompt="Extract the full legal name of the second party.",
            data_type="str",
        ),
        FieldDefinition(
            field_name="payment_terms",
            description="Payment terms in days",
            prompt="Extract payment terms as number of days. Convert 'Net 30' to 30.",
            data_type="float",
        ),
        FieldDefinition(
            field_name="governing_law",
            description="Governing law jurisdiction",
            prompt="Extract the governing law jurisdiction (state or country).",
            data_type="str",
        ),
    ]


def _infer_document_type(text: str) -> str:
    """Simple heuristic to classify document type from text."""
    text_lower = text[:2000].lower()
    if "non-disclosure" in text_lower or "nda" in text_lower:
        return "NDA"
    if "statement of work" in text_lower or "sow" in text_lower:
        return "SOW"
    if "master service" in text_lower or "msa" in text_lower:
        return "MSA"
    if "amendment" in text_lower or "change order" in text_lower:
        return "Amendment"
    if "lease" in text_lower:
        return "Lease"
    return "Agreement"


def _get_records(event: Dict[str, Any]) -> List[Dict]:
    """Extract records from S3 event, EventBridge, or SQS wrapper."""
    # Direct records
    if "Records" in event:
        records = event["Records"]
        # SQS wraps the S3 event in 'body'
        unwrapped = []
        for r in records:
            if "body" in r:
                try:
                    body = json.loads(r["body"])
                    unwrapped.extend(body.get("Records", [body]))
                except (json.JSONDecodeError, TypeError):
                    unwrapped.append(r)
            else:
                unwrapped.append(r)
        return unwrapped

    # EventBridge detail
    if "detail" in event:
        return [event]

    return [event]


def _extract_s3_info(record: dict) -> tuple:
    """Extract bucket and key from various event formats."""
    detail = record.get("detail", {})
    if detail:
        bucket = detail.get("bucket", {}).get("name", "")
        key = detail.get("object", {}).get("key", "")
        if bucket and key:
            return bucket, key

    s3_info = record.get("s3", {})
    bucket = s3_info.get("bucket", {}).get("name", "")
    key = s3_info.get("object", {}).get("key", "")
    return bucket, key
