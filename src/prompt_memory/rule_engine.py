"""Rule graduation engine: synthesizes deterministic rules from recurring corrections.

When the same correction pattern occurs N+ times for a field, the engine
generates a regex, lookup, or transformation rule that handles extraction
without any LLM call.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition

logger = logging.getLogger(__name__)


@dataclass
class GraduatedRule:
    """A deterministic extraction rule synthesized from corrections."""

    field_name: str
    document_type: str
    rule_type: str  # "regex", "lookup", "transformation"
    pattern: str  # regex pattern or lookup key
    replacement: str  # output template or lookup value
    source_corrections: int  # how many corrections generated this rule
    accuracy_on_source: float  # validation accuracy against source corrections
    created_at: str = ""

    def apply(self, document_text: str) -> Optional[str]:
        """Apply this rule to extract a value from document text.

        Returns the extracted value, or None if the rule doesn't match.
        """
        if self.rule_type == "regex":
            return self._apply_regex(document_text)
        elif self.rule_type == "lookup":
            return self._apply_lookup(document_text)
        elif self.rule_type == "transformation":
            return self._apply_transformation(document_text)
        return None

    def _apply_regex(self, text: str) -> Optional[str]:
        """Apply regex pattern and return first captured group."""
        match = re.search(self.pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            if groups:
                return groups[0]
            return match.group(0)
        return None

    def _apply_lookup(self, text: str) -> Optional[str]:
        """Check if the lookup key exists in text, return replacement."""
        if re.search(self.pattern, text, re.IGNORECASE):
            return self.replacement
        return None

    def _apply_transformation(self, text: str) -> Optional[str]:
        """Apply regex extraction with replacement template."""
        match = re.search(self.pattern, text, re.IGNORECASE)
        if match:
            try:
                return match.expand(self.replacement)
            except (re.error, IndexError):
                return match.group(1) if match.groups() else None
        return None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GraduatedRule:
        fields = {f.name for f in dataclass_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


class RuleEngine:
    """Manages graduated rules and synthesizes new ones from correction patterns.

    The engine:
    1. Stores graduated rules as JSON files
    2. Checks rules before LLM extraction (Tier 1)
    3. Periodically scans corrections for graduation candidates
    4. Validates synthesized rules against source corrections
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        graduation_threshold: int = 5,
    ):
        self._store_path = store_path or Path.home() / ".prompt_memory" / "rules"
        self._store_path.mkdir(parents=True, exist_ok=True)
        self._graduation_threshold = graduation_threshold
        self._rules: Dict[str, List[GraduatedRule]] = {}
        self._load_rules()

    def _load_rules(self) -> None:
        """Load all graduated rules from disk."""
        self._rules = {}
        rules_file = self._store_path / "graduated_rules.json"
        if rules_file.exists():
            try:
                with open(rules_file) as f:
                    data = json.load(f)
                for rule_data in data:
                    rule = GraduatedRule.from_dict(rule_data)
                    key = self._rule_key(rule.field_name, rule.document_type)
                    self._rules.setdefault(key, []).append(rule)
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to load rules: %s", exc)

    def _save_rules(self) -> None:
        """Persist all rules to disk."""
        all_rules = []
        for rule_list in self._rules.values():
            for rule in rule_list:
                all_rules.append(rule.to_dict())
        rules_file = self._store_path / "graduated_rules.json"
        with open(rules_file, "w") as f:
            json.dump(all_rules, f, indent=2)

    @staticmethod
    def _rule_key(field_name: str, document_type: str) -> str:
        return f"{field_name}##{document_type or '*'}"

    def get_rule(
        self, field_name: str, document_type: str = ""
    ) -> Optional[GraduatedRule]:
        """Look up a graduated rule for a field/document-type pair.

        Checks document-type-specific rules first, then wildcard rules.
        """
        # Check specific document type first
        key = self._rule_key(field_name, document_type)
        rules = self._rules.get(key, [])
        if rules:
            return rules[0]

        # Fall back to wildcard (any document type)
        wildcard_key = self._rule_key(field_name, "")
        rules = self._rules.get(wildcard_key, [])
        if rules:
            return rules[0]

        return None

    def apply_rule(
        self, document_text: str, field: FieldDefinition, document_type: str = ""
    ) -> Optional[ExtractionResult]:
        """Attempt to extract a field using a graduated rule.

        Returns an ExtractionResult if a rule matches, None otherwise.
        """
        rule = self.get_rule(field.field_name, document_type)
        if rule is None:
            return None

        value = rule.apply(document_text)
        if value is None:
            return None

        return ExtractionResult(
            field_name=field.field_name,
            value=value,
            confidence_score=0.98,
            reasoning=f"rule-graduated ({rule.rule_type})",
            model_id="deterministic",
            input_tokens=0,
            output_tokens=0,
            cost_estimate=0.0,
        )

    def graduate_from_corrections(
        self,
        corrections: List[CorrectionRecord],
        meta_learner: Optional[Any] = None,
    ) -> List[GraduatedRule]:
        """Analyze corrections and graduate recurring patterns into rules.

        Groups corrections by (field_name, document_type), checks if any
        group exceeds the graduation threshold, and attempts to synthesize
        a deterministic rule.

        Strategy:
        1. Try simple regex synthesis (deterministic, zero-cost)
        2. If that fails and meta_learner is provided, ask the LLM to
           synthesize a more complex rule (one LLM call saves thousands)

        Args:
            corrections: All available corrections to analyze.
            meta_learner: Optional MetaRuleLearner for LLM-assisted synthesis.

        Returns list of newly graduated rules.
        """
        # Group corrections by field + document type
        groups: Dict[str, List[CorrectionRecord]] = defaultdict(list)
        for c in corrections:
            key = self._rule_key(c.field_name, c.document_type)
            groups[key].append(c)

        new_rules = []
        for key, group in groups.items():
            if len(group) < self._graduation_threshold:
                continue

            # Skip if we already have a rule for this key
            if key in self._rules:
                continue

            # Strategy 1: Simple regex synthesis
            rule = self._synthesize_rule(group)

            # Strategy 2: LLM-assisted meta-rule synthesis (if simple fails)
            if rule is None and meta_learner is not None:
                rule = self._meta_synthesize_rule(group, meta_learner)

            if rule is None:
                continue

            # Validate against source corrections
            accuracy = self._validate_rule(rule, group)
            if accuracy < 0.9:
                logger.info(
                    "Rule for '%s' failed validation (%.2f accuracy), skipping.",
                    group[0].field_name,
                    accuracy,
                )
                continue

            rule.accuracy_on_source = accuracy
            rule.source_corrections = len(group)
            self._rules.setdefault(key, []).append(rule)
            new_rules.append(rule)
            logger.info(
                "Graduated rule for field '%s' (type: %s, pattern: %s)",
                rule.field_name,
                rule.rule_type,
                rule.pattern,
            )

        if new_rules:
            self._save_rules()

        return new_rules

    def _meta_synthesize_rule(
        self, corrections: List[CorrectionRecord], meta_learner: Any
    ) -> Optional[GraduatedRule]:
        """Use LLM meta-learner to synthesize a complex rule.

        One LLM call here saves thousands of future LLM calls for this pattern.
        """
        try:
            rule_data = meta_learner.synthesize_rule(corrections)
        except Exception as exc:
            logger.warning("Meta-rule synthesis failed: %s", exc)
            return None

        if rule_data is None:
            return None

        pattern = rule_data.get("regex_pattern", "")
        post_processing = rule_data.get("post_processing", "none")

        # Determine rule type based on post-processing
        rule_type = "regex" if post_processing == "none" else "transformation"

        return GraduatedRule(
            field_name=corrections[0].field_name,
            document_type=corrections[0].document_type,
            rule_type=rule_type,
            pattern=pattern,
            replacement=post_processing,
            source_corrections=len(corrections),
            accuracy_on_source=rule_data.get("validation_accuracy", 0.0),
        )

    def _synthesize_rule(
        self, corrections: List[CorrectionRecord]
    ) -> Optional[GraduatedRule]:
        """Attempt to synthesize a rule from a group of corrections.

        Tries multiple strategies in order of specificity:
        1. Regex extraction pattern (if corrected values are extractable from excerpts)
        2. Lookup/normalization (if corrections map consistent patterns)
        3. Transformation (if a regex + replacement template works)
        """
        field_name = corrections[0].field_name
        document_type = corrections[0].document_type

        # Strategy 1: Check if all corrected values are numeric and extractable
        rule = self._try_numeric_regex(corrections, field_name, document_type)
        if rule:
            return rule

        # Strategy 2: Check for consistent prefix/suffix removal patterns
        rule = self._try_normalization_rule(corrections, field_name, document_type)
        if rule:
            return rule

        # Strategy 3: Check if corrected values appear literally in excerpts
        rule = self._try_literal_extraction(corrections, field_name, document_type)
        if rule:
            return rule

        return None

    def _try_numeric_regex(
        self, corrections: List[CorrectionRecord], field_name: str, document_type: str
    ) -> Optional[GraduatedRule]:
        """Try to build a regex for numeric field extraction."""
        # Check if all corrected values are numeric
        numeric_values = []
        for c in corrections:
            try:
                numeric_values.append(float(c.corrected_value))
            except ValueError:
                return None

        # All corrections produce numeric values — look for "Net X" or "within X days" patterns
        patterns_to_try = [
            (r"[Nn]et\s+(\d+)", "Net X pattern"),
            (r"within\s+\w+\s*\((\d+)\)\s*days", "within X days pattern"),
            (r"within\s+(\d+)\s*days", "within N days pattern"),
            (r"(\d+)\s*days?\s*(?:of|from|after)", "N days of/from/after"),
        ]

        for pattern, desc in patterns_to_try:
            matches_all = True
            for c in corrections:
                match = re.search(pattern, c.document_excerpt, re.IGNORECASE)
                if not match:
                    matches_all = False
                    break
                extracted = match.group(1)
                try:
                    if abs(float(extracted) - float(c.corrected_value)) > 0.01:
                        matches_all = False
                        break
                except ValueError:
                    matches_all = False
                    break

            if matches_all:
                return GraduatedRule(
                    field_name=field_name,
                    document_type=document_type,
                    rule_type="regex",
                    pattern=pattern,
                    replacement=r"\1",
                    source_corrections=len(corrections),
                    accuracy_on_source=0.0,
                )

        return None

    def _try_normalization_rule(
        self, corrections: List[CorrectionRecord], field_name: str, document_type: str
    ) -> Optional[GraduatedRule]:
        """Try to build a normalization rule (e.g., 'State of X' → 'X')."""
        # Check if all corrections share a consistent transformation
        # e.g., removing "State of " prefix
        prefixes_to_try = [
            (r"(?:State\s+of\s+|Commonwealth\s+of\s+)", ""),
            (r"(?:the\s+)", ""),
        ]

        for prefix_pattern, replacement in prefixes_to_try:
            matches_all = True
            for c in corrections:
                # Check: does original have the prefix and corrected doesn't?
                orig_stripped = re.sub(
                    prefix_pattern, "", c.original_value, flags=re.IGNORECASE
                ).strip()
                if orig_stripped.lower() != c.corrected_value.lower():
                    matches_all = False
                    break

            if matches_all and len(corrections) >= self._graduation_threshold:
                # Build extraction pattern from document excerpts
                # Look for "governed by the laws of X" or similar
                governing_patterns = [
                    r"(?:governed\s+by\s+(?:the\s+)?laws?\s+of\s+(?:the\s+)?)"
                    r"(?:State\s+of\s+|Commonwealth\s+of\s+)?([A-Z][\w\s]+?)(?:\.|,|\s+without)",
                    r"(?:jurisdiction\s+of\s+(?:the\s+)?(?:State\s+of\s+)?)"
                    r"([A-Z][\w\s]+?)(?:\.|,)",
                ]
                for gp in governing_patterns:
                    all_match = all(
                        re.search(gp, c.document_excerpt, re.IGNORECASE)
                        for c in corrections
                    )
                    if all_match:
                        return GraduatedRule(
                            field_name=field_name,
                            document_type=document_type,
                            rule_type="regex",
                            pattern=gp,
                            replacement=r"\1",
                            source_corrections=len(corrections),
                            accuracy_on_source=0.0,
                        )

        return None

    def _try_literal_extraction(
        self, corrections: List[CorrectionRecord], field_name: str, document_type: str
    ) -> Optional[GraduatedRule]:
        """Try to build a rule that extracts corrected values literally from text."""
        # Check if corrected values appear verbatim in their respective excerpts
        all_present = all(
            c.corrected_value.lower() in c.document_excerpt.lower() for c in corrections
        )
        if not all_present:
            return None

        # Try to find a common context pattern around the corrected values
        # e.g., "as of <DATE>" or "effective <DATE>"
        if corrections[0].field_name == "effective_date":
            date_patterns = [
                r"(?:as\s+of|effective|dated)\s+(\w+\s+\d{1,2},?\s*\d{4})",
                r"(?:effective|commencement)\s+(?:date[:\s]+)?(\d{4}-\d{2}-\d{2})",
            ]
            for dp in date_patterns:
                all_match = all(
                    re.search(dp, c.document_excerpt, re.IGNORECASE)
                    for c in corrections
                )
                if all_match:
                    return GraduatedRule(
                        field_name=field_name,
                        document_type=document_type,
                        rule_type="regex",
                        pattern=dp,
                        replacement=r"\1",
                        source_corrections=len(corrections),
                        accuracy_on_source=0.0,
                    )

        return None

    def _validate_rule(
        self, rule: GraduatedRule, corrections: List[CorrectionRecord]
    ) -> float:
        """Validate a synthesized rule against its source corrections.

        Returns accuracy (0.0 to 1.0). Rule must achieve 1.0 to graduate.
        """
        correct = 0
        for c in corrections:
            extracted = rule.apply(c.document_excerpt)
            if extracted is not None:
                # Normalize for comparison
                if self._values_match(extracted, c.corrected_value):
                    correct += 1
        return correct / len(corrections) if corrections else 0.0

    @staticmethod
    def _values_match(extracted: str, expected: str) -> bool:
        """Compare extracted value against expected with normalization."""
        ext = extracted.strip().lower()
        exp = expected.strip().lower()
        if ext == exp:
            return True
        # Numeric comparison
        try:
            return abs(float(ext) - float(exp)) < 0.01
        except ValueError:
            pass
        # Containment check
        return exp in ext or ext in exp

    def list_rules(self) -> List[GraduatedRule]:
        """Return all graduated rules."""
        all_rules = []
        for rule_list in self._rules.values():
            all_rules.extend(rule_list)
        return all_rules

    def delete_rule(self, field_name: str, document_type: str = "") -> bool:
        """Delete a graduated rule. Returns True if found and deleted."""
        key = self._rule_key(field_name, document_type)
        if key in self._rules:
            del self._rules[key]
            self._save_rules()
            return True
        return False

    def clear(self) -> None:
        """Remove all graduated rules."""
        self._rules = {}
        rules_file = self._store_path / "graduated_rules.json"
        if rules_file.exists():
            rules_file.unlink()
