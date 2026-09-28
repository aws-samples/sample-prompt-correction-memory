"""Build Bedrock tool_config from field definitions."""

from __future__ import annotations

from typing import Any, Dict

from src.extraction.models import FieldDefinition


def build_tool_config(field: FieldDefinition) -> Dict[str, Any]:
    """Generate a Bedrock Converse API toolConfig for structured extraction.

    Forces the model to return a JSON object with chain_of_thought,
    value, reasoning, and confidence_score via tool use.
    """
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": "extract_field",
                    "description": f"Extract '{field.field_name}': {field.description}",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "chain_of_thought": {
                                    "type": "string",
                                    "description": "Step-by-step reasoning for the extraction.",
                                },
                                "value": {
                                    "type": "string",
                                    "description": f"Extracted value for {field.field_name}. Empty string if not found.",
                                },
                                "reasoning": {
                                    "type": "string",
                                    "description": "Brief explanation of why this value was extracted.",
                                },
                                "confidence_score": {
                                    "type": "number",
                                    "description": "Confidence between 0.0 and 1.0.",
                                },
                            },
                            "required": [
                                "chain_of_thought",
                                "value",
                                "reasoning",
                                "confidence_score",
                            ],
                        }
                    },
                }
            }
        ],
        "toolChoice": {"tool": {"name": "extract_field"}},
    }
