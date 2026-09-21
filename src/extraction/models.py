"""Data models for extraction fields and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FieldDefinition:
    """A field to extract from a document."""

    field_name: str
    description: str
    prompt: str
    data_type: str = "str"
    confidence_threshold: float = 0.7

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FieldDefinition:
        return cls(
            field_name=data["field_name"],
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            data_type=data.get("data_type", "str"),
            confidence_threshold=float(data.get("confidence_threshold", 0.7)),
        )


@dataclass
class ExtractionResult:
    """Result of extracting a single field from a document."""

    field_name: str
    value: str
    confidence_score: float
    chain_of_thought: str = ""
    reasoning: str = ""
    self_healed: bool = False
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    def to_dynamodb_item(self, document_id: str) -> Dict[str, Any]:
        """Serialize to a DynamoDB item."""
        return {
            "document_id": document_id,
            "field_name": self.field_name,
            "value": self.value,
            "confidence_score": str(self.confidence_score),
            "chain_of_thought": self.chain_of_thought,
            "reasoning": self.reasoning,
            "self_healed": self.self_healed,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_estimate": str(self.cost_estimate),
        }


@dataclass
class CorrectionRecord:
    """A human-provided correction for a past extraction error."""

    field_name: str
    document_type: str
    original_value: str
    corrected_value: str
    correction_reason: str
    document_excerpt: str
    timestamp: str = ""

    def to_few_shot_example(self) -> str:
        """Format as a few-shot example for prompt injection."""
        return (
            f"Document excerpt: {self.document_excerpt}\n"
            f"Incorrect answer: {self.original_value}\n"
            f"Correct answer: {self.corrected_value}\n"
            f"Reason for correction: {self.correction_reason}"
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CorrectionRecord:
        return cls(
            field_name=data["field_name"],
            document_type=data.get("document_type", ""),
            original_value=data.get("original_value", ""),
            corrected_value=data.get("corrected_value", ""),
            correction_reason=data.get("correction_reason", ""),
            document_excerpt=data.get("document_excerpt", ""),
            timestamp=data.get("timestamp", ""),
        )
