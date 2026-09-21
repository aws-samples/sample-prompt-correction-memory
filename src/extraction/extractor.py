"""Core extraction logic using Amazon Bedrock Converse API."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import boto3

from src.extraction.models import ExtractionResult, FieldDefinition
from src.extraction.schema_builder import build_tool_config

logger = logging.getLogger(__name__)

# Approximate pricing per 1M tokens (input, output)
_PRICING: Dict[str, tuple] = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.0, 5.0),
    "us.anthropic.claude-sonnet-4-20250514-v1:0": (3.0, 15.0),
    "anthropic.claude-3-haiku-20240307-v1:0": (0.25, 1.25),
}

SYSTEM_PROMPT = (
    "You are a document extraction assistant. "
    "Use the extract_field tool to return your structured answer. "
    "Always provide chain_of_thought, value, reasoning, and confidence_score. "
    "If the field is not found, set value to empty string and confidence_score to 0.0."
)


class Extractor:
    """Extracts structured fields from documents via Bedrock."""

    def __init__(
        self,
        model_id: Optional[str] = None,
        region: Optional[str] = None,
        bedrock_client: Optional[Any] = None,
    ):
        self._model_id = model_id or os.environ.get(
            "EXTRACTION_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )
        self._region = region or os.environ.get("AWS_REGION", "us-west-2")
        self._client = bedrock_client or boto3.client(
            "bedrock-runtime", region_name=self._region
        )

    def extract_field(
        self,
        document_text: str,
        field: FieldDefinition,
        prompt_override: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> ExtractionResult:
        """Extract a single field from document text.

        Args:
            document_text: Full text of the document.
            field: Field definition with prompt and metadata.
            prompt_override: Optional prompt to use instead of field.prompt.
            model_id: Optional model override.

        Returns:
            ExtractionResult with value, confidence, and token usage.
        """
        effective_model = model_id or self._model_id
        prompt = prompt_override or field.prompt
        tool_config = build_tool_config(field)

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": f"<document>\n{document_text}\n</document>"},
                    {"text": f"--- FIELD INSTRUCTIONS ---\n{prompt}"},
                    {
                        "text": f"Extract the field '{field.field_name}' from the document above."
                    },
                ],
            }
        ]

        try:
            response = self._client.converse(
                modelId=effective_model,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig=tool_config,
            )
        except Exception as exc:
            logger.error("Bedrock call failed for '%s': %s", field.field_name, exc)
            return ExtractionResult(
                field_name=field.field_name,
                value="",
                confidence_score=0.0,
                reasoning=f"Extraction failed: {exc}",
                model_id=effective_model,
            )

        # Parse structured response
        content = self._parse_tool_response(response)
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        pricing = _PRICING.get(effective_model, (1.0, 5.0))
        cost = (input_tokens * pricing[0] + output_tokens * pricing[1]) / 1_000_000

        return ExtractionResult(
            field_name=field.field_name,
            value=str(content.get("value", "")),
            confidence_score=float(content.get("confidence_score", 0.0)),
            chain_of_thought=str(content.get("chain_of_thought", "")),
            reasoning=str(content.get("reasoning", "")),
            model_id=effective_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )

    @staticmethod
    def _parse_tool_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the tool use input from a Bedrock Converse response."""
        output = response.get("output", {})
        message = output.get("message", {})
        for block in message.get("content", []):
            if "toolUse" in block:
                return block["toolUse"].get("input", {})
        return {}
