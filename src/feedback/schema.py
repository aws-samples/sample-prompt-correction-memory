"""Schema validation for incoming QA correction files."""

from __future__ import annotations

from typing import Any, Dict, List

REQUIRED_FIELDS = [
    "field_name",
    "original_value",
    "corrected_value",
    "correction_reason",
    "document_excerpt",
]


def validate_correction(data: Dict[str, Any]) -> List[str]:
    """Validate a correction payload. Returns list of errors (empty if valid)."""
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in data or not str(data[field]).strip():
            errors.append(f"Missing or empty required field: '{field}'")
    return errors
