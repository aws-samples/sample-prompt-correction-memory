"""Schema validation for incoming QA correction files.

Corrections are untrusted input: their text is later injected into LLM prompts
as few-shot examples (see prompt_memory/retry_with_examples.py). This module
enforces types, length caps, and an optional field-name allow-list so a
malicious or malformed correction cannot smuggle unbounded or unexpected
content into a prompt. See OWASP LLM01 (Prompt Injection).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REQUIRED_FIELDS = [
    "field_name",
    "original_value",
    "corrected_value",
    "correction_reason",
    "document_excerpt",
]

# Length caps (characters) for untrusted values before they reach a prompt.
MAX_VALUE_LEN = 200
MAX_TEXT_LEN = 2000

_FIELD_LEN_CAPS = {
    "field_name": MAX_VALUE_LEN,
    "document_type": MAX_VALUE_LEN,
    "original_value": MAX_VALUE_LEN,
    "corrected_value": MAX_VALUE_LEN,
    "correction_reason": MAX_TEXT_LEN,
    "document_excerpt": MAX_TEXT_LEN,
}


def load_known_field_names(
    definitions_path: Optional[Path] = None,
) -> Optional[Set[str]]:
    """Load the set of known field names from field-definitions.json.

    Returns None if the file is unavailable, in which case field_name is not
    checked against an allow-list (callers may still pass an explicit set).
    """
    if definitions_path is None:
        definitions_path = (
            Path(__file__).resolve().parents[2]
            / "sample_data"
            / "field-definitions.json"
        )
    try:
        with open(definitions_path, encoding="utf-8") as f:
            defs = json.load(f)
        return {d["field_name"] for d in defs if "field_name" in d}
    except (OSError, ValueError, KeyError):
        return None


def validate_correction(
    data: Dict[str, Any],
    known_field_names: Optional[Set[str]] = None,
) -> List[str]:
    """Validate a correction payload. Returns a list of errors (empty if valid).

    Args:
        data: The parsed correction object.
        known_field_names: Optional allow-list of valid field names. If not
            provided, it is loaded from field-definitions.json when available.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return ["Correction must be a JSON object"]

    for field in REQUIRED_FIELDS:
        value = data.get(field)
        if value is None:
            errors.append(f"Missing required field: '{field}'")
            continue
        if not isinstance(value, str):
            errors.append(f"Field '{field}' must be a string")
            continue
        if not value.strip():
            errors.append(f"Empty required field: '{field}'")
            continue
        cap = _FIELD_LEN_CAPS.get(field, MAX_VALUE_LEN)
        if len(value) > cap:
            errors.append(f"Field '{field}' exceeds maximum length of {cap} characters")

    # Optional string field
    doc_type = data.get("document_type")
    if doc_type is not None and not isinstance(doc_type, str):
        errors.append("Field 'document_type' must be a string")
    elif isinstance(doc_type, str) and len(doc_type) > MAX_VALUE_LEN:
        errors.append(
            f"Field 'document_type' exceeds maximum length of {MAX_VALUE_LEN} characters"
        )

    # Allow-list check on field_name (only if we have a known set and no prior
    # error already flagged field_name).
    if known_field_names is None:
        known_field_names = load_known_field_names()
    field_name = data.get("field_name")
    if (
        known_field_names
        and isinstance(field_name, str)
        and field_name.strip()
        and field_name not in known_field_names
    ):
        errors.append(
            f"Unknown field_name '{field_name}'; "
            f"expected one of: {sorted(known_field_names)}"
        )

    return errors
