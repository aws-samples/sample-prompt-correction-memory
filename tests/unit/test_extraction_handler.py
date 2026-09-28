"""Tests for extraction handler helpers (event parsing, classification, defs)."""

from __future__ import annotations

import json

from src.extraction.handler import (
    _extract_s3_info,
    _get_records,
    _infer_document_type,
    _load_field_definitions,
)


def test_get_records_unwraps_sqs_body():
    s3_event = {
        "Records": [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "documents/x.txt"}}}
        ]
    }
    event = {"Records": [{"body": json.dumps(s3_event)}]}
    records = _get_records(event)
    assert len(records) == 1
    bucket, key = _extract_s3_info(records[0])
    assert bucket == "b"
    assert key == "documents/x.txt"


def test_get_records_handles_eventbridge_detail():
    event = {"detail": {"bucket": {"name": "b"}, "object": {"key": "documents/y.txt"}}}
    records = _get_records(event)
    assert len(records) == 1
    bucket, key = _extract_s3_info(records[0])
    assert (bucket, key) == ("b", "documents/y.txt")


def test_get_records_handles_malformed_sqs_body():
    event = {"Records": [{"body": "not-json"}]}
    records = _get_records(event)
    # Falls back to treating the record itself as the record (no crash).
    assert len(records) == 1


def test_extract_s3_info_direct_s3_event():
    record = {"s3": {"bucket": {"name": "docs"}, "object": {"key": "documents/z.txt"}}}
    assert _extract_s3_info(record) == ("docs", "documents/z.txt")


def test_extract_s3_info_missing_returns_empty():
    assert _extract_s3_info({}) == ("", "")


def test_infer_document_type_classifies_known_types():
    assert _infer_document_type("MUTUAL NON-DISCLOSURE AGREEMENT") == "NDA"
    assert _infer_document_type("MASTER SERVICE AGREEMENT") == "MSA"
    assert _infer_document_type("COMMERCIAL LEASE AGREEMENT for lease") == "Lease"
    assert _infer_document_type("This First Amendment amends the MSA") == "Amendment"
    assert _infer_document_type("Statement of Work (SOW)") == "SOW"
    assert _infer_document_type("some generic contract") == "Agreement"


def test_infer_document_type_word_boundary_no_false_positive():
    # "calendar" contains "nda" as a substring but must not classify as NDA.
    assert _infer_document_type("payment due on the first calendar day") == "Agreement"


def test_load_field_definitions_from_sample_file():
    fields = _load_field_definitions()
    names = {f.field_name for f in fields}
    # Sample field-definitions.json ships these fields.
    assert "payment_terms" in names
    assert "effective_date" in names


def test_load_field_definitions_falls_back_when_missing(monkeypatch):
    monkeypatch.setenv("FIELD_DEFINITIONS_PATH", "/nonexistent/path.json")
    fields = _load_field_definitions()
    # Falls back to the built-in default set rather than crashing.
    assert len(fields) >= 1
    assert all(f.field_name for f in fields)
