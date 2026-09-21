"""Meta-rule learning: uses LLM to synthesize complex extraction rules from corrections.

When simple regex synthesis fails but a correction pattern clearly exists,
the LLM is asked to generate a rule (regex + transformation logic) that
captures the pattern. The generated rule is then validated against all
source corrections before graduation.

This is "meta-learning" — the LLM teaches itself rules that eliminate future
LLM calls for that pattern. The system pays one LLM call to save thousands.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.extraction.models import CorrectionRecord

logger = logging.getLogger(__name__)

RULE_SYNTHESIS_PROMPT = """You are a rule synthesis engine. Given a set of extraction corrections, generate a deterministic Python regex rule that correctly extracts the field value from document text.

FIELD: {field_name}
DOCUMENT TYPE: {document_type}

CORRECTIONS (these are past errors and their fixes):
{corrections_text}

YOUR TASK:
Analyze these corrections and produce a single regex pattern that, when applied to document text, correctly extracts the value.

Requirements:
1. The regex MUST use a capturing group for the extracted value
2. The regex MUST be case-insensitive compatible
3. The regex MUST correctly extract the corrected_value from each document_excerpt above
4. If a post-processing transformation is needed (e.g., strip prefix, convert format), describe it

Respond using the extract_rule tool with your regex pattern and any transformation."""

RULE_SYNTHESIS_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "extract_rule",
                "description": "Define a deterministic extraction rule from correction patterns",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "reasoning": {
                                "type": "string",
                                "description": "Why this pattern captures the corrections correctly",
                            },
                            "regex_pattern": {
                                "type": "string",
                                "description": "Python regex with capturing group(s) for the value",
                            },
                            "post_processing": {
                                "type": "string",
                                "description": "Post-processing to apply: 'none', 'strip_prefix:<prefix>', 'to_integer', 'date_normalize', or 'lowercase'",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "How confident you are this rule generalizes (0.0-1.0)",
                            },
                        },
                        "required": [
                            "reasoning",
                            "regex_pattern",
                            "post_processing",
                            "confidence",
                        ],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": "extract_rule"}},
}


class MetaRuleLearner:
    """Uses an LLM to synthesize complex extraction rules from corrections.

    This is meta-learning: the LLM generates rules that eliminate future LLM calls.
    One LLM call → saves thousands of future calls for that pattern.

    Flow:
    1. Receive a group of corrections that couldn't be handled by simple regex synthesis
    2. Ask the LLM to analyze the pattern and generate a regex + transformation
    3. Validate the generated rule against all source corrections
    4. If validation passes → graduate the rule
    """

    def __init__(
        self, bedrock_client: Optional[Any] = None, model_id: Optional[str] = None
    ):
        self._client = bedrock_client
        self._model_id = model_id or "us.anthropic.claude-sonnet-4-20250514-v1:0"

    def synthesize_rule(
        self, corrections: List[CorrectionRecord]
    ) -> Optional[Dict[str, Any]]:
        """Ask the LLM to synthesize a rule from a group of corrections.

        Args:
            corrections: Grouped corrections for the same field/document_type.

        Returns:
            Dict with 'regex_pattern', 'post_processing', 'confidence', 'reasoning'
            or None if synthesis fails or client unavailable.
        """
        if not self._client or not corrections:
            return None

        field_name = corrections[0].field_name
        document_type = corrections[0].document_type

        corrections_text = self._format_corrections(corrections)

        prompt = RULE_SYNTHESIS_PROMPT.format(
            field_name=field_name,
            document_type=document_type,
            corrections_text=corrections_text,
        )

        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=[
                    {
                        "text": "You are a precise rule synthesis engine. Generate regex patterns that correctly capture extraction values."
                    }
                ],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                toolConfig=RULE_SYNTHESIS_TOOL_CONFIG,
            )
        except Exception as exc:
            logger.error("Meta-rule synthesis LLM call failed: %s", exc)
            return None

        # Parse tool response
        rule_data = self._parse_tool_response(response)
        if not rule_data:
            return None

        # Validate the generated regex compiles
        pattern = rule_data.get("regex_pattern", "")
        try:
            re.compile(pattern)
        except re.error as exc:
            logger.warning("LLM generated invalid regex '%s': %s", pattern, exc)
            return None

        # Validate against source corrections
        accuracy = self._validate_rule(
            pattern, rule_data.get("post_processing", "none"), corrections
        )
        rule_data["validation_accuracy"] = accuracy

        if accuracy < 0.8:
            logger.info(
                "Meta-synthesized rule for '%s' failed validation (%.2f accuracy)",
                field_name,
                accuracy,
            )
            return None

        logger.info(
            "Meta-synthesized rule for '%s': pattern='%s', accuracy=%.2f",
            field_name,
            pattern,
            accuracy,
        )
        return rule_data

    def _format_corrections(self, corrections: List[CorrectionRecord]) -> str:
        """Format corrections for the LLM prompt."""
        parts = []
        for i, c in enumerate(corrections, 1):
            parts.append(
                f"Correction {i}:\n"
                f'  Document excerpt: "{c.document_excerpt}"\n'
                f'  Wrong extraction: "{c.original_value}"\n'
                f'  Correct value: "{c.corrected_value}"\n'
                f"  Reason: {c.correction_reason}"
            )
        return "\n\n".join(parts)

    def _validate_rule(
        self, pattern: str, post_processing: str, corrections: List[CorrectionRecord]
    ) -> float:
        """Validate generated rule against source corrections."""
        correct = 0
        for c in corrections:
            match = re.search(pattern, c.document_excerpt, re.IGNORECASE)
            if match:
                groups = match.groups()
                extracted = groups[0] if groups else match.group(0)
                processed = self._apply_post_processing(extracted, post_processing)
                if self._values_match(processed, c.corrected_value):
                    correct += 1
        return correct / len(corrections) if corrections else 0.0

    @staticmethod
    def _apply_post_processing(value: str, processing: str) -> str:
        """Apply post-processing transformation to extracted value."""
        if processing == "none" or not processing:
            return value
        if processing == "to_integer":
            try:
                return str(int(float(value)))
            except ValueError:
                return value
        if processing == "lowercase":
            return value.lower()
        if processing.startswith("strip_prefix:"):
            prefix = processing[len("strip_prefix:") :]
            return re.sub(prefix, "", value, flags=re.IGNORECASE).strip()
        if processing == "date_normalize":
            # Basic date normalization attempt
            return value.strip()
        return value

    @staticmethod
    def _values_match(extracted: str, expected: str) -> bool:
        """Compare values with normalization."""
        ext = extracted.strip().lower()
        exp = expected.strip().lower()
        if ext == exp:
            return True
        try:
            return abs(float(ext) - float(exp)) < 0.01
        except ValueError:
            pass
        return exp in ext or ext in exp

    @staticmethod
    def _parse_tool_response(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract tool use input from Bedrock response."""
        output = response.get("output", {})
        message = output.get("message", {})
        for block in message.get("content", []):
            if "toolUse" in block:
                return block["toolUse"].get("input", {})
        return None
