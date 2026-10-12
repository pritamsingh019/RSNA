"""Aggregate exploratory agreement of frozen report candidates with masked truth.

These are candidate-rule agreement diagnostics, not image-model performance.
Only verified observed binary cells participate in confusion counts. An
abstention remains UNKNOWN, including when an observed target is positive.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import ContractError, TARGETS, label_vector

RULE_VERSION = "rsna-report-rules-v1.0.0"
ENGINE_POLICY = "conservative-english-clause-v1"
LOW_SUPPORT_THRESHOLD = 10
MAX_EVALUATION_ROWS = 100_000
_STATES = frozenset(("POSITIVE", "NEGATIVE", "UNKNOWN"))
_IDENTITY_KEYS = frozenset(("version", "sha256", "engine_policy"))
_Z95 = 1.959963984540054


def wilson95(numerator: int, denominator: int) -> list[float] | None:
    """Nominal two-sided 95% Wilson score interval for a binomial proportion.

    This interval does not establish independence, provenance, or clinical
    reliability. Undefined proportions receive no interval.
    """
    if (
        type(numerator) is not int or type(denominator) is not int
        or denominator < 0 or numerator < 0 or numerator > denominator
    ):
        raise ContractError("agreement proportion requires valid integer counts")
    if denominator == 0:
        return None
    proportion = numerator / denominator
    z_squared = _Z95 * _Z95
    normalizer = 1.0 + z_squared / denominator
    center = (proportion + z_squared / (2.0 * denominator)) / normalizer
    radius = (
        _Z95 * math.sqrt(
            proportion * (1.0 - proportion) / denominator
            + z_squared / (4.0 * denominator * denominator)
        ) / normalizer
    )
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _proportion(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "wilson95": wilson95(numerator, denominator),
    }


def _identity(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, Mapping) or set(value) != _IDENTITY_KEYS
        or value.get("version") != RULE_VERSION
        or value.get("engine_policy") != ENGINE_POLICY
        or not isinstance(value.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
    ):
        raise ContractError("agreement requires a valid frozen rule identity")
    return {"version": RULE_VERSION, "sha256": value["sha256"], "engine_policy": ENGINE_POLICY}


def _validate_candidate(value: Any, identity: dict[str, str]) -> None:
    if not isinstance(value, Mapping) or _identity(value.get("rule_identity")) != identity:
        raise ContractError("agreement candidate rule identity mismatch")
    targets = value.get("targets")
    if not isinstance(targets, Mapping) or set(targets) != set(TARGETS):
        raise ContractError("agreement candidate target contract is invalid")
    for target in TARGETS:
        candidate = targets[target]
        if (
            not isinstance(candidate, Mapping)
            or not isinstance(candidate.get("state"), str)
            or candidate["state"] not in _STATES
            or type(candidate.get("conflict")) is not bool
            or (candidate["conflict"] and candidate["state"] != "UNKNOWN")
        ):
            raise ContractError("agreement candidate state contract is invalid")


def _warnings(support: dict[str, int], metrics: dict[str, Any]) -> list[str]:
    warnings = []
    if support["observed"] == 0:
        warnings.append("NO_OBSERVED_TRUTH: agreement is not assessed for this target.")
    if support["positive"] < LOW_SUPPORT_THRESHOLD:
        warnings.append("LOW_POSITIVE_SUPPORT: fewer than 10 observed positive cells.")
    if support["negative"] < LOW_SUPPORT_THRESHOLD:
        warnings.append("LOW_NEGATIVE_SUPPORT: fewer than 10 observed negative cells.")
    for name in (
        "agreement", "positive_predictive_value", "selective_sensitivity",
        "selective_specificity", "negative_predictive_value", "detection_sensitivity",
    ):
        if metrics[name]["denominator"] < LOW_SUPPORT_THRESHOLD:
            warnings.append(f"LOW_METRIC_DENOMINATOR: {name} uses fewer than 10 cells.")
    return warnings


def evaluate_candidates(
    train_rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    rule_identity: Mapping[str, str],
) -> dict[str, Any]:
    """Compare aligned private candidates to observed labels and emit counts only.

    The caller freezes the rule set before extraction and evaluation. Every
    candidate must carry the identical frozen identity. Alignment is by input
    order, not by copied study identifiers. Reports, identifiers, reason codes,
    arbitrary candidate fields, and per-study predictions never enter output.
    """
    identity = _identity(rule_identity)
    if (
        not isinstance(train_rows, Sequence) or isinstance(train_rows, (str, bytes))
        or not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes))
        or len(train_rows) != len(candidates)
        or len(train_rows) > MAX_EVALUATION_ROWS
    ):
        raise ContractError("agreement requires aligned bounded row and candidate sequences")
    # Check identity and candidate contracts before computing any agreement.
    for candidate in candidates:
        _validate_candidate(candidate, identity)
    counts = {
        target: {
            "candidate_counts": {"positive": 0, "negative": 0, "unknown": 0, "conflict": 0},
            "truth_support": {"observed": 0, "positive": 0, "negative": 0, "unknown": 0},
            "abstained_unknown_by_truth": {"positive": 0, "negative": 0, "unknown": 0},
            "classified_truth_counts": {"positive": 0, "negative": 0, "unknown": 0},
            "confusion": {"TP": 0, "FP": 0, "TN": 0, "FN": 0},
        }
        for target in TARGETS
    }
    observed_studies = 0
    for row, candidate in zip(train_rows, candidates):
        try:
            labels = label_vector(row)
        except (ContractError, TypeError) as error:
            raise ContractError("agreement source label contract is invalid") from error
        if "invalid" in labels["states"]:
            raise ContractError("agreement source contains an invalid label cell")
        observed_studies += any(labels["observed_mask"])
        for index, target in enumerate(TARGETS):
            target_counts = counts[target]
            target_candidate = candidate["targets"][target]
            state = target_candidate["state"]
            target_counts["candidate_counts"][state.lower()] += 1
            target_counts["candidate_counts"]["conflict"] += target_candidate["conflict"]
            observed = labels["observed_mask"][index]
            truth_class = (
                "positive" if labels["values"][index] == 1.0 else "negative"
            ) if observed else "unknown"
            target_counts["truth_support"][truth_class] += 1
            target_counts["truth_support"]["observed"] += observed
            if state == "UNKNOWN":
                target_counts["abstained_unknown_by_truth"][truth_class] += 1
                continue
            target_counts["classified_truth_counts"][truth_class] += 1
            if not observed:
                continue
            confusion_key = (
                "TP" if truth_class == "positive" else "FP"
            ) if state == "POSITIVE" else (
                "FN" if truth_class == "positive" else "TN"
            )
            target_counts["confusion"][confusion_key] += 1
    for target_counts in counts.values():
        candidate_counts = target_counts["candidate_counts"]
        support = target_counts["truth_support"]
        confusion = target_counts["confusion"]
        tp, fp, tn, fn = (confusion[key] for key in ("TP", "FP", "TN", "FN"))
        classified_observed = tp + fp + tn + fn
        metrics = {
            "agreement": _proportion(tp + tn, classified_observed),
            "positive_predictive_value": _proportion(tp, tp + fp),
            "selective_sensitivity": _proportion(tp, tp + fn),
            "selective_specificity": _proportion(tn, tn + fp),
            "negative_predictive_value": _proportion(tn, tn + fn),
            "detection_sensitivity": _proportion(tp, support["positive"]),
        }
        target_counts.update({
            "coverage": _proportion(candidate_counts["positive"] + candidate_counts["negative"], len(train_rows)),
            "observed_truth_coverage": _proportion(classified_observed, support["observed"]),
            "metrics": metrics,
            "warnings": _warnings(support, metrics),
        })
    return {
        "status": "PASS",
        "evaluation_status": "EXPLORATORY",
        "independent_validation": False,
        "rule_identity": identity,
        "rule_identity_consistency_verified": True,
        "row_count": len(train_rows),
        "observed_label_study_count": observed_studies,
        "observed_label_cell_count": sum(count["truth_support"]["observed"] for count in counts.values()),
        "per_target": counts,
        "warning_policy": {"low_support_threshold": LOW_SUPPORT_THRESHOLD, "comparison": "strictly_below"},
        "metric_definitions": {
            "agreement": "(TP + TN) / classified observed truth cells; unknown candidates excluded.",
            "positive_predictive_value": "TP / (TP + FP), observed truth only.",
            "selective_sensitivity": "TP / (TP + FN), classified observed positives only.",
            "selective_specificity": "TN / (TN + FP), classified observed negatives only.",
            "negative_predictive_value": "TN / (TN + FN), observed truth only.",
            "detection_sensitivity": "TP / all observed positives; abstentions are not detections and are never assigned negative labels.",
        },
        "limitations": [
            "Candidate-rule agreement is not image-model performance or competition accuracy.",
            "Rules must be frozen before evaluation; the same observed labels must not be used to tune rules and claim independent validation.",
            "Small observed-label samples, including 58 studies, cannot establish reliable twelve-target validation by their count alone.",
            "Wilson intervals are nominal binomial intervals; patient independence and representative sampling remain unverified.",
            "Unknown truth cells are excluded from every confusion count and agreement metric.",
        ],
        "model_metrics": None,
    }
