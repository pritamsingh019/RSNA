"""Validate the exact submission contract and write without overwriting files."""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import ContractError, STUDY_ID, TARGETS, _numeric, validate_identifier
from .inputs import safe_absolute

SUBMISSION_COLUMNS = (STUDY_ID, *TARGETS)


def validate_submission(
    rows: Sequence[Mapping[str, Any]],
    test_ids: Iterable[str],
    columns: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Require each test study once and twelve finite probabilities in order."""
    expected_ids = [validate_identifier(uid) for uid in test_ids]
    if not expected_ids:
        raise ContractError("test identifier set must not be empty")
    if len(set(expected_ids)) != len(expected_ids):
        raise ContractError("test identifier list contains duplicates")
    if not rows:
        raise ContractError("submission must contain at least one row")
    if not isinstance(rows[0], Mapping):
        raise ContractError("submission row 0 must be a mapping")
    actual_columns = tuple(columns) if columns is not None else tuple(rows[0])
    if actual_columns != SUBMISSION_COLUMNS:
        raise ContractError("submission columns must exactly match the identifier and target order")
    observed_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != set(SUBMISSION_COLUMNS):
            raise ContractError(f"submission row {index} has missing or extra cells")
        uid = validate_identifier(row[STUDY_ID])
        if uid in observed_ids:
            raise ContractError(f"duplicate submission identifier at row {index}")
        observed_ids.add(uid)
        for target in TARGETS:
            value = row[target]
            if not _numeric(value):
                raise ContractError(f"submission row {index} has a nonnumeric probability for {target}")
            try:
                probability = float(value)
            except (ValueError, OverflowError) as error:
                raise ContractError(f"submission row {index} has an invalid probability for {target}") from error
            if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
                raise ContractError(f"submission row {index} has an invalid probability for {target}")
    if observed_ids != set(expected_ids):
        raise ContractError("submission identifier set differs from the exact test identifier set")
    return {
        "status": "PASS", "row_count": len(rows), "unique_study_count": len(observed_ids),
        "columns": list(SUBMISSION_COLUMNS), "probabilities_per_row": len(TARGETS),
    }


def write_submission(
    rows: Sequence[Mapping[str, Any]],
    test_ids: Iterable[str],
    path: str | Path,
    columns: Sequence[str] = SUBMISSION_COLUMNS,
) -> dict[str, Any]:
    """Validate, reject protected/link paths, and refuse any existing output."""
    validation = validate_submission(rows, test_ids, columns)
    destination = safe_absolute(path)
    with destination.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)
    return {**validation, "path": str(destination), "size_bytes": destination.stat().st_size}
