"""Correction log storage backed by DynamoDB."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from src.extraction.models import CorrectionRecord


class CorrectionStore:
    """Read and write corrections from the DynamoDB correction-log table.

    Table schema:
        PK: field_name (S)
        SK: timestamp (S)
    """

    def __init__(
        self,
        table_name: Optional[str] = None,
        dynamodb_resource: Optional[Any] = None,
    ):
        self._table_name = table_name or os.environ.get(
            "CORRECTION_TABLE", "correction-log"
        )
        dynamodb = dynamodb_resource or boto3.resource("dynamodb")
        self._table = dynamodb.Table(self._table_name)

    def get_corrections(
        self,
        field_name: str,
        document_type: Optional[str] = None,
        limit: int = 3,
    ) -> List[CorrectionRecord]:
        """Query the most recent corrections for a field.

        Uses Query on PK (field_name) with ScanIndexForward=False
        to get the most recent corrections first.
        """
        params: Dict[str, Any] = {
            "KeyConditionExpression": Key("field_name").eq(field_name),
            "ScanIndexForward": False,
            "Limit": limit,
        }

        # Filter by document type if provided
        if document_type:
            params["FilterExpression"] = "document_type = :dt"
            params["ExpressionAttributeValues"] = {":dt": document_type}

        response = self._table.query(**params)
        items = response.get("Items", [])

        return [CorrectionRecord.from_dict(item) for item in items]

    def put_correction(self, correction: CorrectionRecord) -> None:
        """Write a correction record to the table."""
        self._table.put_item(
            Item={
                "field_name": correction.field_name,
                "timestamp": correction.timestamp,
                "document_type": correction.document_type,
                "original_value": correction.original_value,
                "corrected_value": correction.corrected_value,
                "correction_reason": correction.correction_reason,
                "document_excerpt": correction.document_excerpt,
            }
        )
