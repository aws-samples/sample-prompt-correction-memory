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


def test_values_match_rejects_containment_and_empty():
    """Rule validation uses exact/numeric equality, not containment (S2)."""
    from src.prompt_memory.rule_engine import RuleEngine

    match = RuleEngine._values_match
    # Exact and normalized-numeric equality still match.
    assert match("New York", "New York") is True
    assert match("30", "30.0") is True
    # Containment must NOT match.
    assert match("New", "New York") is False
    assert match("New York City", "New York") is False
    # Empty extraction never matches.
    assert match("", "New York") is False
    assert match("   ", "anything") is False


def test_graduated_rule_apply_regex_and_lookup_and_transformation():
    """The three rule types apply correctly to matching text."""
    from src.prompt_memory.rule_engine import GraduatedRule

    regex_rule = GraduatedRule(
        field_name="payment_terms",
        document_type="MSA",
        rule_type="regex",
        pattern=r"[Nn]et\s+(\d+)",
        replacement=r"\1",
        source_corrections=3,
        accuracy_on_source=1.0,
    )
    assert regex_rule.apply("Terms are Net 30 from receipt") == "30"
    assert regex_rule.apply("no terms here") is None

    lookup_rule = GraduatedRule(
        field_name="governing_law",
        document_type="MSA",
        rule_type="lookup",
        pattern=r"laws of the State of New York",
        replacement="New York",
        source_corrections=3,
        accuracy_on_source=1.0,
    )
    assert (
        lookup_rule.apply("governed by the laws of the State of New York") == "New York"
    )
    assert lookup_rule.apply("laws of California") is None

    xform_rule = GraduatedRule(
        field_name="party_a",
        document_type="MSA",
        rule_type="transformation",
        pattern=r"between (\w+) and",
        replacement=r"\1",
        source_corrections=3,
        accuracy_on_source=1.0,
    )
    assert xform_rule.apply("entered between Acme and Beta") == "Acme"


def test_graduated_rule_invalid_regex_does_not_crash():
    """An invalid stored/synthesized pattern is treated as a non-match (robustness)."""
    from src.prompt_memory.rule_engine import GraduatedRule

    for rule_type in ("regex", "lookup", "transformation"):
        bad = GraduatedRule(
            field_name="f",
            document_type="",
            rule_type=rule_type,
            pattern=r"(unclosed",  # invalid regex
            replacement="x",
            source_corrections=3,
            accuracy_on_source=1.0,
        )
        # Must not raise; returns None so the pipeline falls back to the LLM.
        assert bad.apply("some text") is None


def test_graduate_from_corrections_synthesizes_numeric_rule(tmp_path):
    """Repeated numeric corrections graduate into a working Net-X regex rule."""
    from src.extraction.models import CorrectionRecord, FieldDefinition
    from src.prompt_memory.rule_engine import RuleEngine

    engine = RuleEngine(store_path=tmp_path, graduation_threshold=3)
    corrections = [
        CorrectionRecord(
            field_name="payment_terms",
            document_type="MSA",
            original_value="quarterly",
            corrected_value="30",
            correction_reason="Net 30",
            document_excerpt=f"Invoice {i}: payable Net 30 from receipt.",
        )
        for i in range(3)
    ]
    new_rules = engine.graduate_from_corrections(corrections)
    assert len(new_rules) == 1

    field = FieldDefinition(
        field_name="payment_terms",
        description="",
        prompt="",
        data_type="float",
    )
    result = engine.apply_rule("Payment is Net 30 net.", field, "MSA")
    assert result is not None
    assert result.value == "30"
    assert result.model_id == "deterministic"
    assert result.cost_estimate == 0.0


def test_rules_persist_and_reload(tmp_path):
    """Graduated rules survive a fresh RuleEngine load from the same store."""
    from src.extraction.models import CorrectionRecord
    from src.prompt_memory.rule_engine import RuleEngine

    engine = RuleEngine(store_path=tmp_path, graduation_threshold=3)
    corrections = [
        CorrectionRecord(
            field_name="payment_terms",
            document_type="MSA",
            original_value="q",
            corrected_value="30",
            correction_reason="Net 30",
            document_excerpt="payable Net 30 from receipt.",
        )
        for _ in range(3)
    ]
    engine.graduate_from_corrections(corrections)

    reloaded = RuleEngine(store_path=tmp_path, graduation_threshold=3)
    assert reloaded.get_rule("payment_terms", "MSA") is not None


def test_below_threshold_does_not_graduate(tmp_path):
    from src.extraction.models import CorrectionRecord
    from src.prompt_memory.rule_engine import RuleEngine

    engine = RuleEngine(store_path=tmp_path, graduation_threshold=5)
    corrections = [
        CorrectionRecord(
            field_name="payment_terms",
            document_type="MSA",
            original_value="q",
            corrected_value="30",
            correction_reason="Net 30",
            document_excerpt="payable Net 30 from receipt.",
        )
        for _ in range(2)
    ]
    assert engine.graduate_from_corrections(corrections) == []
