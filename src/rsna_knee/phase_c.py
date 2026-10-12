"""Read-only report supervision audit with frozen rules and aggregate evidence."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import time
import uuid

from .audit import run_preflight
from .contracts import ContractError
from .inputs import InputError, MANIFEST_NAMES, read_csv_manifest, safe_absolute
from .phase_c_config import PhaseCConfig
from .phase_c_validation import assess_validation
from .report_quality import aggregate_reports


CHECK_STATES = {"PASS", "FAIL", "BLOCKED", "NOT_ASSESSED"}
SAFE_SOURCE_FILES = frozenset(name + ".py" for name in (
    "__init__", "config", "inputs", "contracts", "metrics", "submission", "audit", "cli",
    "report_quality", "validation_support", "report_rules", "report_agreement",
    "phase_c_config", "phase_c_validation", "phase_c",
))


def _validated_source_identity(value):
    """Accept only the generated bundle schema, never arbitrary caller text."""
    if value is None or value == {"status": "NOT_SUPPLIED"}:
        return {"status": "NOT_SUPPLIED"}
    if (not isinstance(value, dict) or set(value) != {"kind", "bundle_sha256", "source_sha256"}
            or value.get("kind") != "embedded-notebook" or not isinstance(value.get("source_sha256"), dict)
            or set(value["source_sha256"]) != SAFE_SOURCE_FILES):
        raise InputError("Invalid Phase C source identity schema")
    hashes = [value["bundle_sha256"], *value["source_sha256"].values()]
    if any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in hashes):
        raise InputError("Invalid Phase C source checksum")
    return {"kind": "embedded-notebook", "bundle_sha256": value["bundle_sha256"],
            "source_sha256": dict(value["source_sha256"])}


def _csv_summary(preflight):
    contracts = preflight["csv_contracts"]
    discovery = preflight["discovery"]
    summary = {"status": preflight["status"], "csv_status": contracts["status"],
               "discovery": {key: discovery[key] for key in (
                   "status", "scan_complete", "directories_checked", "entries_checked",
                   "limit_reached", "error_count", "skipped_link_count")}}
    if contracts["status"] == "VALID":
        summary.update({
            "csv_sha256": dict(contracts["sha256"]),
            "counts": {key: contracts[key]["row_count"] for key in ("train", "test", "train_series", "test_series")},
            "train_test_study_overlap_count": contracts["train_test_study_overlap_count"],
            "train_test_series_overlap_count": contracts["train_test_series_overlap_count"],
        })
    # No discovery paths, UID tables, arbitrary metadata categories or CSV error text.
    return summary


def _payload(report, config):
    payload = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > config.max_report_bytes:
        raise InputError("REPORT_SIZE_LIMIT_EXCEEDED")
    return payload


def run_phase_c(config: PhaseCConfig, source_identity=None, *, preflight=None):
    if not isinstance(config, PhaseCConfig):
        raise TypeError("run_phase_c requires validated PhaseCConfig")
    from .report_rules import freeze_rules, extract_report
    from .report_agreement import evaluate_candidates

    started = time.perf_counter()
    source_identity = _validated_source_identity(source_identity)
    csv_config = config.preflight_config()
    # Freeze before extraction and any report-candidate agreement.
    frozen = freeze_rules()
    identity = frozen.identity()
    frozen.verify()
    if preflight is None:
        preflight = run_preflight(csv_config, source_identity=source_identity)
    elif (preflight.get("configuration") != csv_config.to_dict()
          or preflight.get("source_identity") != (source_identity or {"status": "NOT_SUPPLIED"})):
        raise InputError("CSV preflight is stale; rerun with the current configuration and source")
    valid = preflight["csv_contracts"].get("status") == "VALID"
    checks = {name: {"status": "NOT_ASSESSED"} for name in (
        "report_quality", "rule_extraction", "observed_label_agreement", "validation_feasibility", "input_immutability")}
    checks["csv_preflight"] = {"status": "PASS" if valid else "FAIL" if preflight["csv_contracts"].get("status") == "INVALID" else "BLOCKED"}
    checks["rule_freeze"] = {"status": "PASS", "identity": identity,
                             "freeze_scope": "Immutable rule content frozen before extraction and agreement; no real-label tuning.",
                             "engine_source_sha256": (source_identity or {}).get("source_sha256", {}).get("report_rules.py")}
    if valid:
        try:
            root = safe_absolute(preflight["discovery"]["selected_root"])
            tables, hashes = {}, {}
            for name in MANIFEST_NAMES:
                _, tables[name], hashes[name] = read_csv_manifest(root / name, csv_config)
            if hashes != preflight["csv_contracts"]["sha256"]:
                raise InputError("CSV_CHANGED_SINCE_PREFLIGHT")
            train = tables["train.csv"]
            checks["report_quality"] = aggregate_reports(train)
            extraction_started = time.perf_counter()
            candidates = []  # Private in-memory objects, separate from source targets.
            report_states = Counter()
            limit_reached = False
            for row in train:
                if time.perf_counter() - extraction_started >= config.max_extraction_seconds:
                    limit_reached = True
                    break
                candidate = extract_report(row["Report"], frozen, max_characters=config.max_report_chars,
                                           max_clauses=config.max_report_clauses)
                candidates.append(candidate)
                report_states[candidate["report_status"]] += 1
            frozen.verify()
            checks["rule_extraction"] = {
                "status": "BLOCKED" if limit_reached else "PASS", "rule_identity": identity,
                "reports_processed": len(candidates), "reports_total": len(train),
                "report_status_counts": dict(sorted(report_states.items())),
                "time_limit_reached": limit_reached, "elapsed_seconds": time.perf_counter() - extraction_started,
                "limits": {"max_report_chars": config.max_report_chars, "max_report_clauses": config.max_report_clauses,
                           "max_extraction_seconds": config.max_extraction_seconds},
                "scope": "Frozen English rule candidates only; unsupported, ambiguous or bounded-out reports abstain. No per-study outputs retained.",
            }
            if not limit_reached:
                agreement = evaluate_candidates(train, candidates, identity)
                checks["observed_label_agreement"] = {"status": "PASS", **agreement}
            del candidates
            checks["validation_feasibility"] = assess_validation(train, tables["train_series.csv"], preflight["csv_contracts"]["train"])
            after = {name: read_csv_manifest(root / name, csv_config)[2] for name in MANIFEST_NAMES}
            checks["input_immutability"] = {"status": "PASS" if after == hashes else "FAIL",
                                             "csv_sha256": after, "scope": "All five CSV hashes before/after; no DICOM or pixel reads"}
        except (InputError, ContractError, OSError) as error:
            checks["input_or_contract"] = {"status": "BLOCKED", "error_category": type(error).__name__}
    if any(check["status"] not in CHECK_STATES for check in checks.values()):
        raise ValueError("Unrecognized Phase C check status")
    states = {check["status"] for check in checks.values()}
    status = "FAIL" if "FAIL" in states else "BLOCKED" if {"BLOCKED", "NOT_ASSESSED"} & states else "PASS"
    report = {
        "schema": "rsna-knee-phase-c-v1", "phase": "PHASE_C", "status": status,
        "created_utc": datetime.now(timezone.utc).isoformat(), "configuration": config.to_dict(),
        "source_identity": source_identity or {"status": "NOT_SUPPLIED"},
        "rule_identity": identity, "preflight": _csv_summary(preflight), "checks": checks,
        "runtime": {"python": platform.python_version(), "platform": platform.system(), "gpu_used": False,
                    "dicom_pixels_decoded": 0, "elapsed_seconds": time.perf_counter() - started},
        "evaluation_status": "EXPLORATORY", "independent_validation": False,
        "training_ready": False, "split_approved": False, "model_metrics": None,
        "gold_resource": {"status": "NOT_ESTABLISHED", "independent_provenance_verified": False},
        "unassessed": ["real-rule reliability without Kaggle aggregate evidence", "dataset-wide trustworthy patient mapping",
                       "independent gold provenance", "locked twelve-target validation", "image-model performance"],
        "next_gate": "Review real aggregate evidence and validation design before training or rule changes informed by observed-label agreement.",
    }
    _payload(report, config)  # Size guard before notebook output, not just the write.
    return report


def write_phase_c_report(report, config):
    if not isinstance(config, PhaseCConfig):
        raise TypeError("write_phase_c_report requires validated PhaseCConfig")
    output, source = safe_absolute(config.output_dir), safe_absolute(config.input_base)
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise InputError("Phase C output must be separate from input_base")
    payload = _payload(report, config)
    output.mkdir(parents=True, exist_ok=True)
    safe_absolute(output)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output / f"phase_c_{stamp}_{uuid.uuid4().hex[:8]}.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return path
