"""Aggregate report-quality indicators without exporting report content.

The script categories below are character heuristics, not language detection.
Input rows and target labels are never changed or converted into weak labels.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import math
import statistics
import unicodedata
from typing import Any


SHORT_REPORT_CHARACTERS = 20
LONG_REPORT_CHARACTERS = 10_000
SCRIPT_CATEGORIES = (
    "latin_only", "devanagari_only", "latin_and_devanagari",
    "latin_and_other_letters", "devanagari_and_other_letters",
    "latin_devanagari_and_other_letters", "other_letters_only", "no_letters",
)


def _percentile(values: list[int], fraction: float) -> float | None:
    """Linearly interpolate sorted values at (n - 1) * fraction."""
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _distribution(values: list[int]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    return {
        "count": len(ordered), "min": ordered[0] if ordered else None,
        "median": statistics.median(ordered) if ordered else None,
        "p90": _percentile(ordered, 0.90), "p99": _percentile(ordered, 0.99),
        "max": ordered[-1] if ordered else None,
    }


def _script_category(latin: bool, devanagari: bool, other: bool) -> str:
    return {
        (True, False, False): "latin_only",
        (False, True, False): "devanagari_only",
        (True, True, False): "latin_and_devanagari",
        (True, False, True): "latin_and_other_letters",
        (False, True, True): "devanagari_and_other_letters",
        (True, True, True): "latin_devanagari_and_other_letters",
        (False, False, True): "other_letters_only",
        (False, False, False): "no_letters",
    }[(latin, devanagari, other)]


def aggregate_reports(train_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Inspect all supplied Report cells; return fixed categories and counts.

    The caller bounds manifest size before parsing. Duplicate normalization is
    NFKC, case folding and whitespace collapse, used in memory only. Empty
    reports do not contribute to duplicate or length distributions.
    """
    normalized: Counter[str] = Counter()
    scripts = {name: 0 for name in SCRIPT_CATEGORIES}
    flags = {name: 0 for name in (
        "unexpected_control_characters", "format_characters", "replacement_characters",
        "private_use_characters", "surrogate_characters", "unassigned_characters",
        "other_symbols", "unusual_characters", "very_short_reports", "very_long_reports",
    )}
    lengths: list[int] = []
    words: list[int] = []
    missing = empty = invalid = latin_reports = devanagari_reports = 0
    latin_letters = devanagari_letters = other_letters = 0
    latin_characters = devanagari_characters = 0
    for row in train_rows:
        if not isinstance(row, Mapping):
            invalid += 1
            continue
        report = row.get("Report")
        if report is None:
            missing += 1
            continue
        if not isinstance(report, str):
            invalid += 1
            continue
        if not report.strip():
            empty += 1
            continue
        lengths.append(len(report))
        words.append(len(report.split()))
        normalized[" ".join(unicodedata.normalize("NFKC", report).casefold().split())] += 1
        encountered: set[str] = set()
        latin = devanagari = other = False
        for character in report:
            category = unicodedata.category(character)
            name = unicodedata.name(character, "")
            if "LATIN" in name:
                latin = True
                latin_characters += 1
            elif "DEVANAGARI" in name:
                devanagari = True
                devanagari_characters += 1
            if category.startswith("L"):
                if "LATIN" in name:
                    latin_letters += 1
                elif "DEVANAGARI" in name:
                    devanagari_letters += 1
                else:
                    other = True
                    other_letters += 1
            if category == "Cc" and character not in "\t\r\n":
                encountered.add("unexpected_control_characters")
            if category == "Cf":
                encountered.add("format_characters")
            if character == "\ufffd":
                encountered.add("replacement_characters")
            for expected, flag in (("Co", "private_use_characters"), ("Cs", "surrogate_characters"),
                                   ("Cn", "unassigned_characters"), ("So", "other_symbols")):
                if category == expected:
                    encountered.add(flag)
        for flag in encountered:
            flags[flag] += 1
        flags["unusual_characters"] += bool(encountered)
        flags["very_short_reports"] += len(report.strip()) < SHORT_REPORT_CHARACTERS
        flags["very_long_reports"] += len(report) > LONG_REPORT_CHARACTERS
        latin_reports += latin
        devanagari_reports += devanagari
        scripts[_script_category(latin, devanagari, other)] += 1
    duplicate_groups = sum(count > 1 for count in normalized.values())
    duplicates = sum(count - 1 for count in normalized.values())
    return {
        "status": "FAIL" if invalid else "PASS", "scope": "All supplied training Report cells",
        "row_count": len(train_rows), "nonempty_report_count": len(lengths),
        "missing_report_count": missing, "empty_report_count": empty,
        "empty_or_missing_report_count": missing + empty, "invalid_report_type_count": invalid,
        "normalized_duplicate_report_count": duplicates, "normalized_duplicate_group_count": duplicate_groups,
        "reports_in_duplicate_groups": sum(count for count in normalized.values() if count > 1),
        "normalization": "NFKC, casefold, whitespace collapse; nonempty reports only",
        "character_count": _distribution(lengths), "whitespace_word_count": _distribution(words),
        "percentile_method": "Linear interpolation at (n - 1) * quantile",
        "unicode_script_indicators": {
            "latin_present_report_count": latin_reports,
            "devanagari_present_report_count": devanagari_reports,
            "latin_character_count": latin_characters,
            "devanagari_character_count": devanagari_characters,
            "latin_letter_count": latin_letters, "devanagari_letter_count": devanagari_letters,
            "other_letter_count": other_letters, "heuristic_report_categories": scripts,
        },
        "quality_flag_report_counts": flags,
        "flag_thresholds": {"very_short_trimmed_characters_less_than": SHORT_REPORT_CHARACTERS,
                            "very_long_original_characters_greater_than": LONG_REPORT_CHARACTERS},
        "quality_review_required": bool(missing or empty or invalid or duplicates or any(flags.values())),
        "limitations": [
            "Unicode character names indicate scripts, not reliable language identification.",
            "Latin does not establish English; Devanagari does not establish Hindi.",
            "Whitespace tokens are not linguistic words and may undercount scripts without word spaces.",
            "Format characters and symbols can be legitimate; quality flags require human review.",
            "Normalized duplicates do not establish patient identity or report correctness.",
            "No report text, normalized strings, report-derived labels or identifiers are exported.",
        ],
        "weak_labels_created": False, "target_labels_changed": False,
    }
