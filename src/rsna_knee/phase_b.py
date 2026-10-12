"""Read-only, bounded real-data audit; execution never grants training approval."""

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import time
import uuid

from .audit import run_preflight
from .contracts import ContractError
from .inputs import InputError, MANIFEST_NAMES, read_csv_manifest, safe_absolute
from .phase_b_config import PhaseBConfig


CHECK_STATES = {"PASS", "FAIL", "BLOCKED", "NOT_ASSESSED"}


def _private_preflight_summary(preflight):
    """Retain measured contracts/hashes, not traversal paths or unbounded categories."""
    contracts = preflight["csv_contracts"]
    if contracts.get("status") == "VALID":
        contracts = json.loads(json.dumps(contracts, allow_nan=False))
        # Input metadata categories may contain arbitrary user strings. Preserve
        # observed/unknown/invalid counts here; sampling stores typed strata privately.
        for split in ("train_series", "test_series"):
            for stats in contracts[split]["metadata_statistics"].values():
                stats.pop("value_counts", None)
    else:
        contracts = {key: contracts[key] for key in ("status", "error_type") if key in contracts}
    discovery = preflight["discovery"]
    return {
        "status": preflight["status"], "csv_contracts": contracts,
        "discovery": {key: discovery[key] for key in (
            "status", "directories_checked", "entries_checked", "scan_complete",
            "limit_reached", "error_count", "skipped_link_count")},
        "scope": "All five bounded CSV reads and contracts; no hidden/private test targets inspected",
    }


def _state(summary, *, complete_key=None):
    state = summary.get("status")
    if state in CHECK_STATES:
        return state
    if "INVALID" in str(state):
        return "FAIL"
    if complete_key and not summary.get(complete_key, False):
        return "BLOCKED"
    if "LIMITED" in str(state):
        return "BLOCKED"
    return "PASS"


def _inventory_state(summary):
    statistics = summary.get("split_statistics", {}).values()
    if (summary.get("ancestry_mismatches", 0)
            or any(stats.get("ambiguous_series", 0) or stats.get("missing_series", 0) for stats in statistics)):
        return "FAIL"
    return _state(summary, complete_key="scan_complete")


def run_phase_b(config: PhaseBConfig, source_identity: dict | None = None, *, preflight: dict | None = None) -> dict:
    if not isinstance(config, PhaseBConfig):
        raise TypeError("run_phase_b requires validated PhaseBConfig")
    started = time.perf_counter()
    csv_config = config.preflight_config()
    if preflight is None:
        preflight = run_preflight(csv_config, source_identity=source_identity)
    elif (preflight.get("configuration") != csv_config.to_dict()
          or preflight.get("source_identity") != (source_identity or {"status": "NOT_SUPPLIED"})):
        raise InputError("CSV preflight is stale; rerun preflight with the current configuration and source")
    checks = {name: {"status": "NOT_ASSESSED"} for name in (
        "series_inventory", "series_file_sample", "dicom", "dicom_headers", "pixel_decoding", "report_quality", "validation_support")}
    csv_valid = preflight["csv_contracts"].get("status") == "VALID"
    checks["csv_preflight"] = {"status": "PASS" if csv_valid else (
        "FAIL" if preflight["csv_contracts"].get("status") == "INVALID" else "BLOCKED")}
    input_hash_recheck = {"status": "NOT_ASSESSED"}
    if csv_valid:
        # Imported only when preflight establishes an unambiguous permitted root.
        from .series_inventory import inventory_series, sample_series_files
        from .dicom_audit import inspect_dicom
        from .report_quality import aggregate_reports
        from .validation_support import summarize_validation

        root = safe_absolute(preflight["discovery"]["selected_root"])
        try:
            tables, current_hashes = {}, {}
            for name in MANIFEST_NAMES:
                _, tables[name], current_hashes[name] = read_csv_manifest(root / name, csv_config)
            if current_hashes != preflight["csv_contracts"]["sha256"]:
                raise InputError("CSV_CHANGED_SINCE_PREFLIGHT")
            inventory, records = inventory_series(root, tables["train_series.csv"], tables["test_series.csv"], config)
            sample, files = sample_series_files(records, config)
            checks["series_inventory"] = {**inventory, "status": _inventory_state(inventory)}
            sample_state = _state(sample)
            if sample.get("empty_series", 0):
                sample_state = "FAIL"
            elif (not files or sample.get("series_with_unscanned_subdirectories", 0)
                  or sample.get("series_with_no_candidate_dicom_filenames_in_immediate_entries", 0)):
                sample_state = "BLOCKED"
            checks["series_file_sample"] = {**sample, "status": sample_state}
            dicom = inspect_dicom(files, config)
            checks["dicom"] = dicom
            for name, key in (("dicom_headers", "headers"), ("pixel_decoding", "decoding")):
                detail = dicom[key]
                measured_state = detail["status"]
                if measured_state == "NOT_ASSESSED" and (name == "dicom_headers" or config.max_pixel_decodes):
                    measured_state = "BLOCKED"
                checks[name] = {"status": measured_state, "attempts": detail["attempts"],
                                "successes": detail["successes"], "failures": detail["failures"],
                                "scope": "Inventory-selected sample only"}
            checks["report_quality"] = aggregate_reports(tables["train.csv"])
            checks["validation_support"] = summarize_validation(tables["train.csv"], preflight["csv_contracts"]["train"])
            after_hashes = {name: read_csv_manifest(root / name, csv_config)[2] for name in MANIFEST_NAMES}
            input_hash_recheck = {"status": "PASS" if after_hashes == current_hashes else "FAIL",
                                  "scope": "All five CSVs SHA-256 before/after; DICOMs opened read-only, not globally hashed",
                                  "csv_sha256": after_hashes}
        except (InputError, ContractError, OSError) as error:
            checks["input_read"] = {"status": "BLOCKED", "error_category": type(error).__name__,
                                    "scope": "No raw exception text, paths, or report text retained"}
    checks["input_immutability"] = input_hash_recheck
    for summary in checks.values():
        if summary["status"] not in CHECK_STATES:
            raise ValueError("Phase B checks must declare a recognized status")
    states = {summary["status"] for summary in checks.values()}
    status = "FAIL" if "FAIL" in states else "BLOCKED" if {"BLOCKED", "NOT_ASSESSED"} & states else "PASS"
    report = {
        "schema": "rsna-knee-phase-b-v1", "phase": "PHASE_B", "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat(), "configuration": config.to_dict(),
        "source_identity": source_identity or {"status": "NOT_SUPPLIED"},
        "runtime": {"python": platform.python_version(), "platform": platform.system(),
                    "gpu_used": False, "elapsed_seconds": time.perf_counter() - started},
        "preflight": _private_preflight_summary(preflight), "checks": checks,
        "training_ready": False, "split_approved": False, "model_metrics": None,
        "gold_resource": {"status": "NOT_ESTABLISHED", "independent_resource_verified": False},
        "scope": "Manifest consistency where exhaustive; bounded series/header/pixel samples otherwise. Visible test data are only the attached runtime sample.",
        "unassessed": ["full-dataset DICOM integrity", "complete codec support", "dataset-wide patient grouping",
                       "independent gold provenance", "approved twelve-target validation", "model performance"],
        "next_gate": "Review audit evidence and validation design before any training approval.",
    }
    # Enforce the output budget before the notebook prints any JSON as well as
    # at the write boundary. Never dump a large report then fail on its write.
    if len((json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")) > config.max_report_bytes:
        raise InputError("REPORT_SIZE_LIMIT_EXCEEDED")
    return report


def write_phase_b_report(report: dict, config: PhaseBConfig) -> Path:
    if not isinstance(config, PhaseBConfig):
        raise TypeError("write_phase_b_report requires validated PhaseBConfig")
    output, source = safe_absolute(config.output_dir), safe_absolute(config.input_base)
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise InputError("Phase B outputs must be separate from input_base")
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > config.max_report_bytes:
        raise InputError("REPORT_SIZE_LIMIT_EXCEEDED")
    output.mkdir(parents=True, exist_ok=True)
    safe_absolute(output)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = output / f"phase_b_{stamp}_{uuid.uuid4().hex[:8]}.json"
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return destination
