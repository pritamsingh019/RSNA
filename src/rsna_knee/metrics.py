"""Observed-label ROC AUC with explicit support and undefined macro metrics."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .contracts import ContractError, STUDY_ID, TARGETS, _numeric, parse_label, validate_identifier
from .submission import validate_submission


def binary_auc(labels: Iterable[Any], scores: Iterable[Any]) -> float | None:
    """Compute rank AUC, averaging tied ranks; return None for missing classes."""
    observed_labels = list(labels)
    observed_scores = list(scores)
    if len(observed_labels) != len(observed_scores):
        raise ContractError("AUC labels and scores must have identical lengths")
    pairs = []
    for label, score in zip(observed_labels, observed_scores):
        if not _numeric(label) or parse_label(label)[1] != "observed":
            raise ContractError("AUC requires observed numeric binary labels")
        if not _numeric(score):
            raise ContractError("AUC requires numeric scores")
        try:
            numeric_score = float(score)
        except (OverflowError, ValueError) as error:
            raise ContractError("AUC requires finite scores") from error
        if not math.isfinite(numeric_score):
            raise ContractError("AUC requires finite scores")
        pairs.append((numeric_score, float(label)))
    positive_count = sum(label == 1.0 for _, label in pairs)
    negative_count = len(pairs) - positive_count
    if not positive_count or not negative_count:
        return None
    pairs.sort(key=lambda pair: pair[0])
    positive_rank_sum = 0.0
    start = 0
    while start < len(pairs):
        end = start + 1
        while end < len(pairs) and pairs[end][0] == pairs[start][0]:
            end += 1
        mean_rank = (start + 1 + end) / 2.0
        positive_rank_sum += mean_rank * sum(label == 1.0 for _, label in pairs[start:end])
        start = end
    return (positive_rank_sum - positive_count * (positive_count + 1) / 2.0) / (positive_count * negative_count)


def evaluate_predictions(
    label_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Match studies exactly and evaluate each target on observed labels only."""
    if not label_rows:
        raise ContractError("reference label table must not be empty")
    references: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(label_rows):
        if not isinstance(row, Mapping) or not set((STUDY_ID, *TARGETS)).issubset(row):
            raise ContractError(f"reference row {index} is missing required cells")
        uid = validate_identifier(row[STUDY_ID])
        if uid in references:
            raise ContractError(f"duplicate reference identifier at row {index}")
        references[uid] = row
    validate_submission(prediction_rows, references)
    predictions = {row[STUDY_ID]: row for row in prediction_rows}
    per_target = {}
    for target in TARGETS:
        labels = []
        scores = []
        unknown_count = 0
        for uid, row in references.items():
            label, state = parse_label(row[target])
            if state == "invalid":
                raise ContractError(f"reference contains an invalid label for {target}")
            if state == "unknown":
                unknown_count += 1
                continue
            labels.append(label)
            scores.append(predictions[uid][target])
        positives = sum(label == 1.0 for label in labels)
        per_target[target] = {
            "observed": len(labels), "positive": positives,
            "negative": len(labels) - positives, "unknown": unknown_count,
            "auc": binary_auc(labels, scores),
        }
    defined = [stats["auc"] for stats in per_target.values() if stats["auc"] is not None]
    complete = len(defined) == len(TARGETS)
    return {
        "status": "FULL_METRICS_AVAILABLE" if complete else "INCOMPLETE_METRICS",
        "row_count": len(references), "per_target": per_target,
        "macro_auc": sum(defined) / len(TARGETS) if complete else None,
        "diagnostic_defined_target_macro_auc": sum(defined) / len(defined) if defined else None,
        "defined_target_count": len(defined), "required_target_count": len(TARGETS),
    }
