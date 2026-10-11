"""Small read-only input check. No API client, downloads, DICOM reads, or training."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import time
import uuid


MANIFEST_NAMES = (
    "train.csv", "train_series.csv", "test.csv", "test_series.csv",
    "sample_submission.csv",
)
IMAGE_DIRECTORY_CANDIDATES = (
    "train_series", "test_series", "train_images", "test_images",
)
PACKAGE_NAMES = ("numpy", "pandas", "pydicom", "torch", "nbformat")


def _issue(path: Path, error: OSError) -> dict:
    # Do not serialize exception messages or environment variables.
    return {"path": str(path), "type": type(error).__name__, "errno": error.errno}


def discover_inputs(
    input_base: Path | str,
    *,
    explicit_root: Path | str | None = None,
    max_depth: int = 2,
    max_directories: int = 128,
    max_entries: int = 1024,
) -> dict:
    """Find co-located manifests within a bounded tree, without opening inputs.

    Identifier directories and DICOM files are not scanned. All limits, skipped
    symlinks, and filesystem errors are reported; presence is not an audit PASS.
    An explicit root must remain within the input base and is checked alone.
    """
    if max_depth < 0 or max_directories < 1 or max_entries < 1:
        raise ValueError("Discovery limits must be non-negative depth and positive counts")
    base = Path(input_base).absolute()
    if base.is_symlink():
        raise ValueError("Input base must not be a symlink")
    base = base.resolve()
    start = base if explicit_root is None else Path(explicit_root).absolute()
    if start.is_symlink():
        raise ValueError("Explicit input root must not be a symlink")
    start = start.resolve()
    if not start.is_relative_to(base):
        raise ValueError("Explicit input root must be within the input base")
    result = {
        "input_base": str(base), "explicit_root": str(start) if explicit_root else None,
        "max_depth": max_depth, "max_directories": max_directories,
        "max_entries": max_entries, "entries_examined": 0, "entry_limit_reached": False,
        "directories_checked": 0, "search_complete_within_limits": True,
        "search_errors": [], "skipped_symlinks": [], "candidates": [],
        "selected_root": None, "status": "INPUTS_NOT_LOCATED",
        "csv_contents_inspected": False, "dicom_contents_inspected": False,
        "image_inventory_status": "NOT_AUDITED", "dataset_identity_verified": False,
    }

    def checked_stat(path: Path):
        try:
            info = path.lstat()
            is_junction = getattr(path, "is_junction", lambda: False)
            if stat.S_ISLNK(info.st_mode) or is_junction():
                result["skipped_symlinks"].append(str(path))
                return None
            if not path.resolve().is_relative_to(base):
                result["search_errors"].append({"path": str(path), "type": "PATH_OUTSIDE_INPUT_BASE", "errno": None})
                return None
            return info
        except FileNotFoundError:
            return None
        except OSError as error:
            result["search_errors"].append(_issue(path, error))
            return None

    start_info = checked_stat(start)
    if start_info is None or not stat.S_ISDIR(start_info.st_mode):
        result["search_complete_within_limits"] = not result["search_errors"]
        result["status"] = "DISCOVERY_INCOMPLETE" if result["search_errors"] else (
            "INPUT_BASE_MISSING" if start == base else "INPUT_ROOT_MISSING"
        )
        return result
    pending = deque([(start, 0)])
    while pending and result["directories_checked"] < max_directories:
        directory, depth = pending.popleft()
        result["directories_checked"] += 1
        found = []
        for name in MANIFEST_NAMES:
            path = directory / name
            info = checked_stat(path)
            if info is not None and stat.S_ISREG(info.st_mode):
                found.append({
                    "name": name, "source_path": str(path), "size_bytes": info.st_size,
                    "modified_utc": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                    "sha256": None, "schema_status": "NOT_INSPECTED",
                })
        if found:
            names = {item["name"] for item in found}
            images = {}
            for name in IMAGE_DIRECTORY_CANDIDATES:
                path = directory / name
                info = checked_stat(path)
                if info is not None and stat.S_ISDIR(info.st_mode):
                    images[name] = str(path)
            result["candidates"].append({
                "root": str(directory), "files": found,
                "missing_manifests": [name for name in MANIFEST_NAMES if name not in names],
                "image_directory_candidates": images,
                "dicom_count": None,
            })
        if depth >= max_depth or explicit_root is not None:
            continue
        try:
            # Stream directory entries: do not materialize a potentially enormous listing.
            with os.scandir(directory) as entries:
                for entry in entries:
                    if result["entries_examined"] >= max_entries:
                        result["entry_limit_reached"] = True
                        break
                    result["entries_examined"] += 1
                    if entry.name.startswith(".") or entry.name in IMAGE_DIRECTORY_CANDIDATES:
                        continue
                    child = directory / entry.name
                    info = checked_stat(child)
                    if info is not None and stat.S_ISDIR(info.st_mode):
                        pending.append((child, depth + 1))
        except OSError as error:
            result["search_errors"].append(_issue(directory, error))
        if result["entry_limit_reached"]:
            break
    result["candidates"].sort(key=lambda item: item["root"])
    result["search_complete_within_limits"] = (
        not pending and not result["search_errors"] and not result["entry_limit_reached"]
    )
    complete = [item for item in result["candidates"] if not item["missing_manifests"]]
    if len(complete) > 1:
        result["status"] = "AMBIGUOUS_INPUT_ROOTS"
    elif len(complete) == 1:
        if result["search_complete_within_limits"]:
            result["selected_root"] = complete[0]["root"]
            result["status"] = "MANIFESTS_LOCATED_AUDIT_PENDING"
        else:
            result["status"] = "DISCOVERY_INCOMPLETE"
    elif not result["search_complete_within_limits"]:
        result["status"] = "DISCOVERY_INCOMPLETE"
    elif result["candidates"]:
        result["status"] = "INCOMPLETE_MANIFESTS"
    return result


def environment_snapshot(output_dir: Path | str) -> dict:
    """Record an allowlist of runtime information without reading credentials."""
    versions = {}
    for name in PACKAGE_NAMES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    probe = Path(output_dir).resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    disk = shutil.disk_usage(probe)
    return {
        "python_version": platform.python_version(), "platform": platform.system(),
        "package_versions": versions, "disk_volume_probe": str(probe),
        "disk_total_bytes": disk.total, "disk_free_bytes": disk.free,
        "gpu_status": "NOT_PROBED_CPU_ONLY_ACCESS_CHECK",
        "peak_memory_bytes": None,
    }


def write_report(report: dict, output_dir: Path | str, *, input_base: Path | str) -> Path:
    """Write a new compact artifact outside the input tree; never overwrite evidence."""
    destination = Path(output_dir).resolve()
    source = Path(input_base).resolve()
    if destination.is_relative_to(source):
        raise ValueError("Report output must be outside the input tree")
    payload = json.dumps(report, indent=2, allow_nan=False, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Access report exceeds the 2 MiB artifact budget")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = destination / f"access_check_{stamp}_{uuid.uuid4().hex[:8]}.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    return path


def run_access_check(
    input_base: Path | str,
    output_dir: Path | str,
    *,
    explicit_root: Path | str | None = None,
    source_sha256: str | None = None,
) -> tuple[dict, Path]:
    started = time.perf_counter()
    discovery = discover_inputs(input_base, explicit_root=explicit_root)
    report = {
        "schema": "rsna-knee-access-check-v1", "phase": "A",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_sha256, "discovery": discovery,
        "environment": environment_snapshot(output_dir),
        "phase1_pass": False, "phase_b_audit_status": "NOT_RUN",
        "kaggle_account_access": "UNVERIFIED_MANUAL_BROWSER_ACTION_REQUIRED",
        "gold_resource_status": "NOT_ESTABLISHED",
        "training_started": False, "model_metrics": None,
        "duration_before_report_write_seconds": time.perf_counter() - started,
    }
    path = write_report(report, output_dir, input_base=input_base)
    return report, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-base", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--data-root", type=Path, help="Explicit root within input base")
    args = parser.parse_args()
    source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report, path = run_access_check(
        args.input_base, args.output_dir, explicit_root=args.data_root, source_sha256=source_sha,
    )
    print(json.dumps({"status": report["discovery"]["status"], "report_path": str(path)}, indent=2))
    return 0 if report["discovery"]["selected_root"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
