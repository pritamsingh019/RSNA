"""Small, explicit contracts for competition identifiers and supervision.

Unknown labels never become negatives. These functions inspect tabular values;
they neither decode images nor infer labels from reports.
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from numbers import Real
from typing import Any

TARGETS = (
    "ACL", "MCL", "Medial Meniscus", "Lateral Meniscus", "Medial OA",
    "Lateral OA", "PF OA", "Effusion", "Synovitis", "Baker's",
    "Contusion", "Fracture",
)
STUDY_ID = "StudyInstanceUID"
SERIES_ID = "SeriesInstanceUID"
SERIES_COLUMNS = (
    STUDY_ID, SERIES_ID, "Anatomical_Plane", "Fluid_Sensitive", "Fat_Suppression",
)


class ContractError(ValueError):
    """A table violates a declared data contract."""


def validate_identifier(value: str) -> str:
    """Return an unchanged safe identifier without imposing a numeric UID format."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractError("identifier must be a nonempty, unpadded string")
    if value in {".", ".."} or any(char in value for char in "/\\:"):
        raise ContractError("identifier must be a single safe path component")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ContractError("identifier contains a control character")
    return value


def _numeric(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (Real, Decimal))


def parse_label(value: Any) -> tuple[float | None, str]:
    """Classify an observed binary label, an unknown cell, or an invalid cell.

    Accepted text is deliberately limited to 0, 1, 0.0 and 1.0. A text token
    such as 'NaN' is invalid; an actual floating NaN represents a missing cell.
    """
    if value is None:
        return None, "unknown"
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None, "unknown"
        if token in {"0", "0.0", "1", "1.0"}:
            return float(token), "observed"
        return None, "invalid"
    if isinstance(value, Real) and not isinstance(value, bool):
        try:
            if math.isnan(float(value)):
                return None, "unknown"
        except (OverflowError, ValueError):
            return None, "invalid"
    if _numeric(value):
        try:
            numeric = float(value)
        except (OverflowError, ValueError):
            return None, "invalid"
        if math.isfinite(numeric) and numeric in (0.0, 1.0):
            return numeric, "observed"
    return None, "invalid"


def _required(row: Mapping[str, Any], required: tuple[str, ...], index: int) -> None:
    if not isinstance(row, Mapping):
        raise ContractError(f"row {index} must be a mapping")
    missing = [key for key in required if key not in row]
    if missing:
        raise ContractError(f"row {index} is missing required columns: {', '.join(missing)}")


def label_vector(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return twelve ordered values and an explicit observation mask.

    Unknown and invalid cells retain None; the state list records the
    distinction. Only verified binary observations receive a True mask.
    """
    _required(row, TARGETS, 0)
    parsed = [parse_label(row[target]) for target in TARGETS]
    return {
        "targets": list(TARGETS),
        "values": [value for value, _ in parsed],
        "observed_mask": [state == "observed" for _, state in parsed],
        "states": [state for _, state in parsed],
    }


def audit_studies(rows: list[dict[str, Any]], split: str) -> dict[str, Any]:
    """Audit study identities and, for training, labels and report coverage."""
    if split not in {"train", "test"}:
        raise ContractError("split must be train or test")
    if not rows:
        raise ContractError("study table must contain at least one row")
    required = (STUDY_ID, *TARGETS, "Report") if split == "train" else (STUDY_ID,)
    study_ids: list[str] = []
    seen: set[str] = set()
    statistics = {
        target: {"observed": 0, "positive": 0, "negative": 0, "unknown": 0, "invalid": 0}
        for target in TARGETS
    }
    label_states: list[dict[str, tuple[float | None, str]]] = []
    report_summary = {"present": 0, "empty_or_missing": 0, "invalid": 0}
    for index, row in enumerate(rows):
        _required(row, required, index)
        uid = validate_identifier(row[STUDY_ID])
        if uid in seen:
            raise ContractError(f"duplicate study identifier at row {index}")
        seen.add(uid)
        study_ids.append(uid)
        if split == "test":
            continue
        states = {target: parse_label(row[target]) for target in TARGETS}
        label_states.append(states)
        for target, (label, state) in states.items():
            statistics[target][state] += 1
            if state == "observed":
                statistics[target]["positive" if label == 1.0 else "negative"] += 1
        report = row["Report"]
        if report is None or (isinstance(report, str) and not report.strip()):
            report_summary["empty_or_missing"] += 1
        elif isinstance(report, str):
            report_summary["present"] += 1
        else:
            report_summary["invalid"] += 1
    result: dict[str, Any] = {
        "status": "PASS", "split": split, "row_count": len(rows),
        "unique_study_count": len(seen), "study_ids": study_ids,
    }
    if split == "test":
        return result
    for stats in statistics.values():
        observed = stats["observed"]
        stats["prevalence"] = stats["positive"] / observed if observed else None
        stats["observation_coverage"] = observed / len(rows)
    cooccurrence = []
    for first_index, first in enumerate(TARGETS):
        for second in TARGETS[first_index + 1:]:
            jointly_observed = both_positive = 0
            for states in label_states:
                first_label, first_state = states[first]
                second_label, second_state = states[second]
                if first_state == second_state == "observed":
                    jointly_observed += 1
                    both_positive += first_label == second_label == 1.0
            cooccurrence.append({
                "targets": [first, second], "jointly_observed": jointly_observed,
                "both_positive": both_positive,
                "both_positive_fraction": both_positive / jointly_observed if jointly_observed else None,
            })
    result.update({
        "target_statistics": statistics, "report_summary": report_summary,
        "observation_coverage": sum(stats["observed"] for stats in statistics.values()) / (len(rows) * len(TARGETS)),
        "jointly_observed_cooccurrence": cooccurrence,
    })
    if any(stats["invalid"] for stats in statistics.values()) or report_summary["invalid"]:
        result["status"] = "INVALID"
    return result


def _metadata_state(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "unknown"
    if isinstance(value, float):
        if math.isnan(float(value)):
            return "unknown"
        if not math.isfinite(float(value)):
            return "invalid"
    if isinstance(value, (str, bool, int, float)):
        return "observed"
    return "invalid"


def audit_series(rows: list[dict[str, Any]], study_ids: set[str]) -> dict[str, Any]:
    """Check series uniqueness, study references and exact metadata coverage.

    Category vocabularies must be established from real data. No plane or flag
    value is coerced here; typed value counts retain the observed representation.
    """
    if not rows:
        raise ContractError("series table must contain at least one row")
    validated_studies = {validate_identifier(uid) for uid in study_ids}
    seen: set[str] = set()
    referenced: set[str] = set()
    metadata_fields = SERIES_COLUMNS[2:]
    statistics = {field: {"observed": 0, "unknown": 0, "invalid": 0} for field in metadata_fields}
    counts: dict[str, Counter[tuple[str, Any]]] = {field: Counter() for field in metadata_fields}
    series_ids: list[str] = []
    for index, row in enumerate(rows):
        _required(row, SERIES_COLUMNS, index)
        study = validate_identifier(row[STUDY_ID])
        series = validate_identifier(row[SERIES_ID])
        if study not in validated_studies:
            raise ContractError(f"series row {index} references an absent study")
        if series in seen:
            raise ContractError(f"duplicate series identifier at row {index}")
        seen.add(series)
        series_ids.append(series)
        referenced.add(study)
        for field in metadata_fields:
            value = row[field]
            state = _metadata_state(value)
            statistics[field][state] += 1
            if state == "observed":
                counts[field][(type(value).__name__, value)] += 1
    for field in metadata_fields:
        statistics[field]["value_counts"] = [
            {"type": value_type, "value": value, "count": count}
            for (value_type, value), count in sorted(counts[field].items(), key=lambda item: (item[0][0], str(item[0][1])))
        ]
    return {
        "status": "INVALID" if any(stats["invalid"] for stats in statistics.values()) else "PASS",
        "row_count": len(rows), "unique_series_count": len(seen),
        "study_count_with_series": len(referenced), "study_count_without_series": len(validated_studies - referenced),
        "series_ids": series_ids, "metadata_statistics": statistics,
    }
