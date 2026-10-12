"""Evidence needed for grouping and a prospective leakage-safe study design."""

from collections import defaultdict

from .contracts import STUDY_ID
from .validation_support import summarize_validation


PATIENT_COLUMNS = ("PatientID", "PatientId", "patient_id", "PatientInstanceUID")
FOLD_SAFEGUARDS = (
    "Keep all MRI series and slices of a study in one partition.",
    "Require proven dataset-wide patient mapping before claiming patient independence; keep repeated studies together when verified.",
    "Fit weak-label generation, learned preprocessing, transformations and calibration only in each fold's training partition.",
    "Exclude validation-study reports and their candidates from the image model's training labels, vocabulary fitting and rule tuning.",
    "Use observed validation targets and their original masks only; candidate labels never replace gold targets.",
    "Use observed supervision in preference to candidates; record source, mask, rule hash and any weak-label weight separately.",
    "Retain UNKNOWN as a false loss/evaluation mask; never fill missing labels with negative targets.",
    "Preserve exact target order, study identifiers and sample-submission schema; split at study/patient level, never by slices.",
    "Keep test studies and hidden targets out of model selection; the image-only inference model must not require reports.",
    "Treat exploratory rule agreement and model-selection scores as development evidence, never final performance estimates.",
    "Freeze rules before agreement measurement; a change after inspecting observed agreement requires a new version and a declared development evaluation.",
)


def assess_validation(train_rows, series_rows, csv_train_summary):
    """Inspect possible CSV grouping fields without guessing their provenance.

    Merely having a PatientID column or successful sampled DICOM headers cannot
    establish a trustworthy dataset-wide patient mapping. No IDs or assignments
    are returned and no DICOM metadata or pixels are read in this phase.
    """
    support = summarize_validation(train_rows, csv_train_summary)
    columns = {}
    for table_name, rows in (("train.csv", train_rows), ("train_series.csv", series_rows)):
        available = {}
        for name in PATIENT_COLUMNS:
            if not any(name in row for row in rows):
                continue
            mappings = defaultdict(set)
            nonempty = 0
            for row in rows:
                value = row.get(name)
                if isinstance(value, str) and value.strip():
                    nonempty += 1
                    mappings[row[STUDY_ID]].add(value)
            available[name] = {
                "rows_with_nonempty_candidate_value": nonempty,
                "row_count": len(rows), "studies_with_candidate_value": len(mappings),
                "studies_with_inconsistent_candidate_values": sum(len(values) > 1 for values in mappings.values()),
            }
        columns[table_name] = available
    return {
        "status": support["status"], "study_grouped_support": support,
        "patient_grouped_validation": {
            "status": "NOT_ASSESSED", "candidate_csv_fields": columns,
            "mapping_trustworthy": False,
            "reason": "No authoritative, complete, consistent dataset-wide patient map and provenance have been established. Candidate columns and sampled PatientID are insufficient.",
            "metadata_scope": "CSV field presence/consistency only; prior Phase B headers were sampled; no new DICOM reads.",
        },
        "prospective_design": {
            "status": "REVIEW_REQUIRED", "split_approved": False,
            "proposal": "Review a small two-fold study/patient-grouped exploratory design first; choose fold count only after measured per-class and joint support. Require an independent holdout or locked nested design for final estimates.",
            "patient_grouping_consequence": "Grouping repeated studies can reduce independent class support and make a marginally feasible study split impossible.",
            "label_sources": {
                "OBSERVED": "Original verified target and mask; primary reference supervision.",
                "REPORT_CANDIDATE": "Separate tri-state weak supervision from frozen rules on training-partition reports only; never gold truth.",
                "UNKNOWN": "No supervised target; false mask, no fabricated negative.",
            },
            "future_weak_labels": "Use training-only candidates on previously unknown cells with separately reviewed weights and quality gates; observed labels take precedence. Do not generate validation weak targets for training.",
            "safeguards": list(FOLD_SAFEGUARDS),
        },
        "warnings": ["Sparse labels may prevent a reliable twelve-target validation estimate.",
                     "A small class-coverage witness or successful audit does not approve a split.",
                     "No independent gold resource is established from the observed study count."],
        "split_approved": False, "training_ready": False,
    }
