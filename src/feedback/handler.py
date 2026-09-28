"""Lambda handler: ingests QA correction files from S3 into the correction log."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from src.extraction.models import CorrectionRecord
from src.feedback.schema import validate_correction
from src.prompt_memory.correction_store import CorrectionStore

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def handler(event, context):
    """Process S3 event containing a QA correction JSON file.

    Expected S3 object format:
    {
        "field_name": "payment_terms",
        "document_type": "Service Agreement",
        "original_value": "quarterly",
        "corrected_value": "30",
        "correction_reason": "'Quarterly billing' refers to billing frequency, not payment terms. The actual payment terms are Net 30.",
        "document_excerpt": "Invoices shall be submitted quarterly. Payment is due within thirty (30) days of receipt."
    }
    """
    s3_client = boto3.client("s3")
    correction_store = CorrectionStore()

    records_processed = 0
    errors = []

    # Handle EventBridge direct invocation (no "Records" wrapper)
    if "detail" in event and "Records" not in event:
        records = [event]
    else:
        records = event.get("Records", [])

    for record in records:
        # Extract S3 info from EventBridge or S3 event
        bucket, key = _extract_s3_info(record)
        if not bucket or not key:
            errors.append("Could not extract S3 bucket/key from event record")
            continue

        # Download and parse the correction file
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            body = json.loads(response["Body"].read().decode("utf-8"))
        except Exception as exc:
            errors.append(f"Failed to read s3://{bucket}/{key}: {exc}")
            continue

        # Support single correction or array of corrections
        corrections = body if isinstance(body, list) else [body]

        for correction_data in corrections:
            validation_errors = validate_correction(correction_data)
            if validation_errors:
                errors.extend(validation_errors)
                continue

            record_obj = CorrectionRecord(
                field_name=correction_data["field_name"],
                document_type=correction_data.get("document_type", ""),
                original_value=correction_data["original_value"],
                corrected_value=correction_data["corrected_value"],
                correction_reason=correction_data["correction_reason"],
                document_excerpt=correction_data["document_excerpt"],
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            )

            try:
                correction_store.put_correction(record_obj)
                records_processed += 1
                logger.info(
                    "Ingested correction for field '%s' from %s",
                    record_obj.field_name,
                    key,
                )
            except Exception as exc:
                errors.append(
                    f"DynamoDB write failed for '{record_obj.field_name}': {exc}"
                )

    return {
        "status": "success" if not errors else "partial",
        "records_processed": records_processed,
        "errors": errors,
    }


def _extract_s3_info(record: dict) -> tuple:
    """Extract bucket and key from S3 or EventBridge event record."""
    # EventBridge format (from S3 notifications)
    detail = record.get("detail", {})
    if detail:
        bucket = detail.get("bucket", {}).get("name", "")
        key = detail.get("object", {}).get("key", "")
        if bucket and key:
            return bucket, key

    # Direct S3 event format
    s3_info = record.get("s3", {})
    bucket = s3_info.get("bucket", {}).get("name", "")
    key = s3_info.get("object", {}).get("key", "")
    return bucket, key
