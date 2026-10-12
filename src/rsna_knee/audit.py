"""Initial preflight contracts only; never a Phase B or training approval."""

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import time
import uuid

from .config import AuditConfig
from .contracts import ContractError, TARGETS, audit_series, audit_studies
from .inputs import InputError, MANIFEST_NAMES, discover_inputs, read_csv_manifest, safe_absolute
from .submission import validate_submission


def _summarize(contract: dict) -> dict:
    # Keep outputs compact; no full identifier tables, report text, or pixels.
    return {key: value for key, value in contract.items() if key not in ("study_ids", "series_ids")}


def _csv_contracts(root: str, config: AuditConfig, progress: dict) -> dict:
    tables = {}
    columns = {}
    hashes = {}
    for name in MANIFEST_NAMES:
        progress["attempted_files"].append(name)
        columns[name], tables[name], hashes[name] = read_csv_manifest(Path(root) / name, config)
        progress["parsed_files"].append(name)
        progress["sha256"][name] = hashes[name]
    train = audit_studies(tables["train.csv"], "train")
    test = audit_studies(tables["test.csv"], "test")
    train_ids, test_ids = set(train["study_ids"]), set(test["study_ids"])
    overlap = train_ids & test_ids
    if overlap:
        raise ContractError("Train/test study identifiers overlap")
    train_series = audit_series(tables["train_series.csv"], train_ids)
    test_series = audit_series(tables["test_series.csv"], test_ids)
    series_overlap = set(train_series["series_ids"]) & set(test_series["series_ids"])
    if series_overlap:
        raise ContractError("Train/test series identifiers overlap")
    missing_train = train_ids - {row["StudyInstanceUID"] for row in tables["train_series.csv"]}
    missing_test = test_ids - {row["StudyInstanceUID"] for row in tables["test_series.csv"]}
    if missing_train or missing_test:
        raise ContractError("Study manifest includes studies without a series reference")
    sample = []
    for row in tables["sample_submission.csv"]:
        converted = dict(row)
        for target in TARGETS:
            if target not in converted:
                raise ContractError("Sample submission is missing target columns")
            try:
                converted[target] = float(converted[target])
            except (TypeError, ValueError) as error:
                raise ContractError("Sample submission scores must be numeric") from error
        sample.append(converted)
    submission = validate_submission(sample, test_ids, columns=columns["sample_submission.csv"])
    valid = all(result["status"] == "PASS" for result in (train, test, train_series, test_series))
    return {
        "status": "VALID" if valid else "INVALID", "columns": columns,
        "sha256": hashes, "train": _summarize(train), "test": _summarize(test),
        "train_series": _summarize(train_series), "test_series": _summarize(test_series),
        "sample_submission": submission, "train_test_study_overlap_count": 0,
        "train_test_series_overlap_count": 0,
        "scope": "CSV contracts only; no patient, report-quality, geometry or image-integrity audit",
    }


def run_preflight(config: AuditConfig, source_identity: dict | None = None) -> dict:
    if not isinstance(config, AuditConfig):
        raise TypeError("run_preflight requires validated AuditConfig")
    started = time.perf_counter()
    discovery = discover_inputs(config)
    csv_contracts = {"status": "NOT_REQUESTED"}
    status = discovery["status"]
    if config.inspect_csv:
        csv_contracts = {"status": "BLOCKED_BY_DISCOVERY"}
        if discovery["selected_root"]:
            progress = {"attempted_files": [], "parsed_files": [], "sha256": {}}
            discovery["csv_read_attempted"] = True
            discovery["csv_read_completed"] = False
            discovery["csv_preflight_completed"] = False
            try:
                csv_contracts = _csv_contracts(discovery["selected_root"], config, progress)
                status = "CONTRACTS_VALID" if csv_contracts["status"] == "VALID" else "CONTRACTS_INVALID"
                discovery["csv_preflight_completed"] = True
            except (InputError, ContractError, OSError) as error:
                # Exceptions contain only static contract descriptions; no raw reports.
                csv_contracts = {"status": "INVALID", "error_type": type(error).__name__,
                                 "reason": str(error) if isinstance(error, (InputError, ContractError)) else "Filesystem read failed"}
                status = "CONTRACTS_INVALID"
            discovery["csv_read_completed"] = len(progress["parsed_files"]) == len(MANIFEST_NAMES)
            # A parse failure may still have consumed bytes. None means unknown,
            # while successful parsed files establish that contents were read.
            discovery["csv_contents_read"] = True if progress["parsed_files"] else None
            discovery["csv_read_progress"] = progress
    return {
        "schema": "rsna-knee-preflight-v1", "phase": "INITIAL_SCAFFOLD", "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat(), "configuration": config.to_dict(),
        "source_identity": source_identity or {"status": "NOT_SUPPLIED"},
        "runtime": {"python": platform.python_version(), "platform": platform.system(), "gpu_used": False},
        "discovery": discovery, "csv_contracts": csv_contracts,
        "phase_b_status": "NOT_RUN", "training_ready": False, "phase1_pass": False,
        "gold_resource": {"status": "NOT_ESTABLISHED", "observed_study_count": None},
        "model_metrics": None,
        "unassessed": ["patient grouping", "report quality and languages", "DICOM UID inventory",
                       "physical geometry", "transfer syntaxes and decoding", "gold provenance",
                       "validation split viability", "Kaggle account access", "model and inference runtime"],
        "duration_before_report_write_seconds": time.perf_counter() - started,
    }


def write_report(report: dict, config: AuditConfig) -> Path:
    output = safe_absolute(config.output_dir)
    source = safe_absolute(config.input_base)
    if output.is_relative_to(source):
        raise InputError("Report destination must be outside input_base")
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > config.max_report_bytes:
        raise InputError("REPORT_SIZE_LIMIT_EXCEEDED")
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output / f"preflight_{stamp}_{uuid.uuid4().hex[:8]}.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return path
