"""Tests for QA correction validation (feedback.schema)."""

from src.feedback.schema import (
    MAX_TEXT_LEN,
    MAX_VALUE_LEN,
    validate_correction,
)

KNOWN = {"payment_terms", "effective_date", "party_a"}


def _valid_correction():
    return {
        "field_name": "payment_terms",
        "document_type": "MSA",
        "original_value": "quarterly",
        "corrected_value": "30",
        "correction_reason": "Billing frequency is not the payment term.",
        "document_excerpt": "Payment is due within thirty (30) days of receipt.",
    }


def test_valid_correction_passes():
    assert validate_correction(_valid_correction(), known_field_names=KNOWN) == []


def test_missing_required_field():
    data = _valid_correction()
    del data["corrected_value"]
    errors = validate_correction(data, known_field_names=KNOWN)
    assert any("corrected_value" in e for e in errors)


def test_non_string_value_rejected():
    data = _valid_correction()
    data["corrected_value"] = 30  # not a string
    errors = validate_correction(data, known_field_names=KNOWN)
    assert any("must be a string" in e for e in errors)


def test_empty_value_rejected():
    data = _valid_correction()
    data["original_value"] = "   "
    errors = validate_correction(data, known_field_names=KNOWN)
    assert any("Empty required field" in e for e in errors)


def test_value_length_cap():
    data = _valid_correction()
    data["corrected_value"] = "x" * (MAX_VALUE_LEN + 1)
    errors = validate_correction(data, known_field_names=KNOWN)
    assert any("maximum length" in e for e in errors)


def test_text_length_cap():
    data = _valid_correction()
    data["document_excerpt"] = "x" * (MAX_TEXT_LEN + 1)
    errors = validate_correction(data, known_field_names=KNOWN)
    assert any("maximum length" in e for e in errors)


def test_unknown_field_name_rejected():
    data = _valid_correction()
    data["field_name"] = "not_a_real_field"
    errors = validate_correction(data, known_field_names=KNOWN)
    assert any("Unknown field_name" in e for e in errors)


def test_non_dict_rejected():
    errors = validate_correction("not a dict", known_field_names=KNOWN)
    assert errors and "JSON object" in errors[0]
