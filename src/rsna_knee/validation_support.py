"""Sparse-label support diagnostics, never an approved validation split."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import ContractError, STUDY_ID, TARGETS, label_vector, validate_identifier


EXACT_SEARCH_MAX_LABELED_STUDIES = {2: 12, 3: 8}


def _joint_coverage(masks: list[int], folds: int) -> dict[str, Any]:
    """Find only a class-coverage witness on tiny study-grouped problems.

    All twenty-four class bits must occur in every fold. No identity, row index
    or assignment is returned. Larger problems remain explicitly unassessed.
    The first study is fixed to fold zero to remove equivalent fold names.
    """
    limit = EXACT_SEARCH_MAX_LABELED_STUDIES[folds]
    base = {"scope": "Observed positive and negative class coverage only; study grouping",
            "labeled_study_count": len(masks), "exact_search_max_labeled_studies": limit,
            "split_approved": False}
    if len(masks) > limit:
        return {**base, "status": "NOT_ASSESSED", "reason": "Tiny exact-search limit exceeded; joint feasibility is not implied by marginal counts"}
    ordered = sorted(masks, key=lambda value: (-value.bit_count(), value))
    complete = (1 << (2 * len(TARGETS))) - 1
    suffix_union = [0] * (len(ordered) + 1)
    for index in range(len(ordered) - 1, -1, -1):
        suffix_union[index] = suffix_union[index + 1] | ordered[index]
    visits = 0
    failed: set[tuple[int, tuple[int, ...]]] = set()

    def search(index: int, covered: tuple[int, ...], counts: tuple[int, ...]) -> tuple[int, ...] | None:
        nonlocal visits
        visits += 1
        if all(value == complete for value in covered):
            # Remaining labeled studies may be put in any fold without losing
            # coverage. This is not a balanced or patient-safe split proposal.
            return (counts[0] + len(ordered) - index, *counts[1:])
        if index == len(ordered) or any((value | suffix_union[index]) != complete for value in covered):
            return None
        key = (index, tuple(sorted(covered)))
        if key in failed:
            return None
        equivalent: set[int] = set()
        for fold, value in enumerate(covered):
            if value in equivalent:
                continue
            equivalent.add(value)
            updated = list(covered)
            updated[fold] |= ordered[index]
            sizes = list(counts)
            sizes[fold] += 1
            result = search(index + 1, tuple(updated), tuple(sizes))
            if result is not None:
                return result
        failed.add(key)
        return None

    witness = search(1, (ordered[0], *([0] * (folds - 1))), (1, *([0] * (folds - 1)))) if ordered else None
    if witness is None:
        return {**base, "status": "FAIL", "reason": "Joint class coverage is infeasible in the exact study-grouped search",
                "search_states_visited": visits}
    return {**base, "status": "PASS", "reason": "Class-coverage witness found; this is not an approved validation design",
            "search_states_visited": visits, "witness_labeled_study_counts_per_fold": list(witness)}


def _failure(reason: str) -> dict[str, Any]:
    return {"status": "FAIL", "reason": reason, "training_ready": False,
            "validation_design_status": "REVIEW_REQUIRED", "split_approved": False}


def summarize_validation(train_rows: list[dict[str, Any]], csv_train_summary: dict[str, Any]) -> dict[str, Any]:
    """Reuse verified target statistics, preserving every unknown-label mask.

    Rechecking counts against unchanged input rows protects this interface from
    a stale summary. The optional tiny exact search proves only class coverage,
    not balance, reliable estimates, patient safety or a suitable split.
    """
    if not isinstance(csv_train_summary, Mapping) or csv_train_summary.get("status") != "PASS":
        return _failure("Valid training CSV contract summary is required")
    if csv_train_summary.get("row_count") != len(train_rows):
        return _failure("CSV summary row count does not match supplied training rows")
    source = csv_train_summary.get("target_statistics")
    if not isinstance(source, Mapping) or any(target not in source for target in TARGETS):
        return _failure("CSV summary must include all twelve verified target statistics")
    observed = {target: {"positive": 0, "negative": 0, "observed": 0, "unknown": 0, "invalid": 0}
                for target in TARGETS}
    seen: set[str] = set()
    masks: list[int] = []
    try:
        for row in train_rows:
            uid = validate_identifier(row[STUDY_ID])
            if uid in seen:
                return _failure("Study grouping requires unique training study identifiers")
            seen.add(uid)
            vector = label_vector(row)
            mask = 0
            for index, target in enumerate(TARGETS):
                state, value = vector["states"][index], vector["values"][index]
                observed[target][state] += 1
                if vector["observed_mask"][index]:
                    name = "positive" if value == 1.0 else "negative"
                    observed[target][name] += 1
                    mask |= 1 << (2 * index + int(value == 1.0))
            if mask:
                masks.append(mask)
    except (ContractError, KeyError, TypeError):
        return _failure("Supplied training rows violate study or label contracts")
    targets = {}
    for target in TARGETS:
        stats = source[target]
        if not isinstance(stats, Mapping):
            return _failure("CSV target statistics are malformed")
        for key, actual in observed[target].items():
            value = stats.get(key)
            if type(value) is not int or value < 0 or value != actual:
                return _failure("CSV target statistics do not match supplied label observations")
        if stats["invalid"]:
            return _failure("Invalid label values cannot establish validation support")
        # Reuse the verified counts; prevalence uses observed labels only.
        targets[target] = {key: stats[key] for key in ("positive", "negative", "observed", "unknown", "invalid")}
        targets[target]["observation_coverage"] = stats["observed"] / len(train_rows) if train_rows else None
        targets[target]["support_consequence"] = (
            "Both classes observed; marginal support does not prove joint split feasibility or reliable estimates"
            if stats["positive"] and stats["negative"] else
            "At least one class has no observed support; a two-class validation estimate is unsupported"
        )
    diagnostics = {}
    for folds, name in ((2, "two_folds"), (3, "three_folds")):
        deficient = [target for target in TARGETS if min(targets[target]["positive"], targets[target]["negative"]) < folds]
        joint = {"status": "FAIL", "reason": "Necessary per-class study support is insufficient", "split_approved": False}
        if not deficient:
            joint = _joint_coverage(masks, folds)
        diagnostics[name] = {
            "fold_count": folds, "necessary_per_class_support": folds,
            "necessary_support_status": "FAIL" if deficient else "PASS",
            "targets_below_necessary_support": deficient, "joint_class_coverage": joint,
        }
    return {
        "status": "PASS", "source": "Verified CSV training target_statistics, checked against label_vector observation masks",
        "study_count": len(train_rows), "labeled_study_count": len(masks), "targets": targets,
        "observed_label_cells": sum(stats["observed"] for stats in targets.values()),
        "unknown_label_cells": sum(stats["unknown"] for stats in targets.values()),
        "label_mask_policy": "Unknown and invalid cells remain unobserved; missing labels are never negative",
        "study_grouped_class_support": diagnostics,
        "patient_grouping": {"status": "NOT_ASSESSED", "reason": "No trustworthy dataset-wide study-to-patient mapping has been established; sampled PatientID metadata is insufficient"},
        "gold_resource": {"status": "NOT_ESTABLISHED", "reason": "Observed label counts do not establish an independent 58-study resource or its provenance"},
        "validation_design_status": "REVIEW_REQUIRED", "split_approved": False, "training_ready": False,
        "limitations": [
            "Sparse labels may prevent a reliable twelve-target validation estimate.",
            "Validation design must be reviewed before training.",
            "Necessary marginal positive/negative counts do not guarantee simultaneous class coverage across targets.",
            "A class-coverage witness does not establish balanced folds, patient independence or statistical reliability.",
            "No approved split, patient mapping, independent gold resource or performance estimate is created.",
        ],
    }
