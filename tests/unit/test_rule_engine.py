"""Unit tests for the rule graduation engine."""

import tempfile
from pathlib import Path

from src.extraction.models import CorrectionRecord, FieldDefinition
from src.prompt_memory.rule_engine import GraduatedRule, RuleEngine


class TestGraduatedRule:
    def test_regex_rule_extracts_numeric(self):
        """Regex rule extracts a numeric value from text."""
        rule = GraduatedRule(
            field_name="payment_terms",
            document_type="",
            rule_type="regex",
            pattern=r"[Nn]et\s+(\d+)",
            replacement=r"\1",
            source_corrections=5,
            accuracy_on_source=1.0,
        )
        result = rule.apply("Payment is due Net 30 from invoice date.")
        assert result == "30"

    def test_regex_rule_returns_none_on_no_match(self):
        """Returns None when pattern doesn't match."""
        rule = GraduatedRule(
            field_name="payment_terms",
            document_type="",
            rule_type="regex",
            pattern=r"[Nn]et\s+(\d+)",
            replacement=r"\1",
            source_corrections=5,
            accuracy_on_source=1.0,
        )
        result = rule.apply("Payment upon receipt of invoice.")
        assert result is None

    def test_regex_case_insensitive(self):
        """Regex matching is case insensitive."""
        rule = GraduatedRule(
            field_name="payment_terms",
            document_type="",
            rule_type="regex",
            pattern=r"[Nn]et\s+(\d+)",
            replacement=r"\1",
            source_corrections=5,
            accuracy_on_source=1.0,
        )
        assert rule.apply("NET 45 terms apply") == "45"
        assert rule.apply("net 60 days") == "60"

    def test_lookup_rule(self):
        """Lookup rule returns replacement when pattern matches."""
        rule = GraduatedRule(
            field_name="governing_law",
            document_type="",
            rule_type="lookup",
            pattern=r"laws\s+of.*Delaware",
            replacement="Delaware",
            source_corrections=5,
            accuracy_on_source=1.0,
        )
        result = rule.apply("governed by the laws of the State of Delaware")
        assert result == "Delaware"

    def test_lookup_no_match(self):
        """Lookup rule returns None when pattern doesn't match."""
        rule = GraduatedRule(
            field_name="governing_law",
            document_type="",
            rule_type="lookup",
            pattern=r"laws\s+of.*Delaware",
            replacement="Delaware",
            source_corrections=5,
            accuracy_on_source=1.0,
        )
        result = rule.apply("governed by the laws of California")
        assert result is None

    def test_serialization_roundtrip(self):
        """Rule serializes to dict and back without loss."""
        rule = GraduatedRule(
            field_name="payment_terms",
            document_type="MSA",
            rule_type="regex",
            pattern=r"Net\s+(\d+)",
            replacement=r"\1",
            source_corrections=7,
            accuracy_on_source=1.0,
            created_at="2024-06-01",
        )
        data = rule.to_dict()
        restored = GraduatedRule.from_dict(data)
        assert restored.field_name == rule.field_name
        assert restored.pattern == rule.pattern
        assert restored.source_corrections == 7


class TestRuleEngine:
    def _make_engine(self, threshold=5):
        """Create engine with temp storage."""
        tmp = tempfile.mkdtemp()
        return RuleEngine(store_path=Path(tmp), graduation_threshold=threshold)

    def _make_payment_corrections(self, count=5):
        """Create N corrections for payment_terms with 'Net X' pattern."""
        corrections = []
        for i in range(count):
            corrections.append(
                CorrectionRecord(
                    field_name="payment_terms",
                    document_type="service-agreement",
                    original_value="quarterly" if i % 2 == 0 else "monthly",
                    corrected_value=str(30 + i * 5),
                    correction_reason="Extract numeric value from Net X pattern",
                    document_excerpt=f"Payment due Net {30 + i * 5} from invoice date.",
                    timestamp=f"2024-01-{i + 1:02d}T10:00:00Z",
                )
            )
        return corrections

    def test_apply_rule_returns_none_when_no_rules(self):
        """Returns None when no rules exist."""
        engine = self._make_engine()
        field = FieldDefinition(field_name="payment_terms", description="", prompt="")
        result = engine.apply_rule("some text", field)
        assert result is None

    def test_graduation_below_threshold_skipped(self):
        """Corrections below threshold don't graduate."""
        engine = self._make_engine(threshold=5)
        corrections = self._make_payment_corrections(count=3)
        new_rules = engine.graduate_from_corrections(corrections)
        assert len(new_rules) == 0

    def test_graduation_at_threshold_produces_rule(self):
        """Corrections at threshold produce a graduated rule."""
        engine = self._make_engine(threshold=5)
        corrections = self._make_payment_corrections(count=5)
        new_rules = engine.graduate_from_corrections(corrections)
        assert len(new_rules) == 1
        assert new_rules[0].field_name == "payment_terms"
        assert new_rules[0].rule_type == "regex"

    def test_graduated_rule_applies_correctly(self):
        """A graduated rule extracts the correct value."""
        engine = self._make_engine(threshold=5)
        corrections = self._make_payment_corrections(count=5)
        engine.graduate_from_corrections(corrections)

        field = FieldDefinition(field_name="payment_terms", description="", prompt="")
        result = engine.apply_rule(
            "Payment is due Net 45 from receipt.", field, "service-agreement"
        )
        assert result is not None
        assert result.value == "45"
        assert result.confidence_score == 0.98
        assert result.model_id == "deterministic"
        assert result.cost_estimate == 0.0

    def test_rule_persists_to_disk(self):
        """Rules survive save/load cycle."""
        tmp = tempfile.mkdtemp()
        path = Path(tmp)

        engine = RuleEngine(store_path=path, graduation_threshold=5)
        corrections = self._make_payment_corrections(count=5)
        engine.graduate_from_corrections(corrections)

        # Create new engine instance pointing at same path
        engine2 = RuleEngine(store_path=path, graduation_threshold=5)
        field = FieldDefinition(field_name="payment_terms", description="", prompt="")
        result = engine2.apply_rule("Net 30 payment terms.", field, "service-agreement")
        assert result is not None
        assert result.value == "30"

    def test_duplicate_graduation_prevented(self):
        """Same correction group doesn't graduate twice."""
        engine = self._make_engine(threshold=5)
        corrections = self._make_payment_corrections(count=5)
        first = engine.graduate_from_corrections(corrections)
        second = engine.graduate_from_corrections(corrections)
        assert len(first) == 1
        assert len(second) == 0

    def test_delete_rule(self):
        """Can delete a graduated rule."""
        engine = self._make_engine(threshold=5)
        corrections = self._make_payment_corrections(count=5)
        engine.graduate_from_corrections(corrections)

        deleted = engine.delete_rule("payment_terms", "service-agreement")
        assert deleted is True

        field = FieldDefinition(field_name="payment_terms", description="", prompt="")
        assert engine.apply_rule("Net 30", field, "service-agreement") is None

    def test_list_rules(self):
        """Lists all graduated rules."""
        engine = self._make_engine(threshold=5)
        corrections = self._make_payment_corrections(count=5)
        engine.graduate_from_corrections(corrections)
        rules = engine.list_rules()
        assert len(rules) == 1
        assert rules[0].field_name == "payment_terms"
