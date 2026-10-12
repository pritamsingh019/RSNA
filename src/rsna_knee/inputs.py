"""Bounded read-only discovery and CSV reads with explicit incomplete states."""

import csv
from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import stat

from .config import AuditConfig


MANIFEST_NAMES = ("train.csv", "train_series.csv", "test.csv", "test_series.csv", "sample_submission.csv")
IMAGE_DIRECTORIES = {"train_series", "test_series", "train_images", "test_images"}
PROTECTED_COMPONENTS = {".git", ".aws", ".kaggle", ".codex", ".venv", "venv", "node_modules", "credentials", "access_token", "kaggle.json"}


class InputError(ValueError):
    """Input contracts or safe-path requirements were not satisfied."""


def safe_absolute(path: str | Path) -> Path:
    """Reject links/reparse ancestors rather than silently resolving through them."""
    absolute = Path(os.path.abspath(path))
    if any(part.casefold() in PROTECTED_COMPONENTS or part.casefold().startswith(".env") for part in absolute.parts):
        raise InputError("Protected directory is not a permitted input or output path")
    for ancestor in (absolute, *absolute.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InputError("Symbolic links and reparse points are not permitted")
    return absolute


def discover_inputs(config: AuditConfig) -> dict:
    result = {
        "status": "INPUTS_NOT_LOCATED", "input_base": None, "selected_root": None,
        "directories_checked": 0, "entries_checked": 0, "scan_complete": True,
        "limit_reached": False, "errors": [], "error_count": 0,
        "skipped_links": [], "skipped_link_count": 0, "candidates": [],
        "csv_contents_read": False, "dicom_contents_read": False,
        "dataset_identity_verified": False,
    }

    def issue(path, error):
        result["error_count"] += 1
        if len(result["errors"]) < 32:
            result["errors"].append({"path": str(path), "type": type(error).__name__, "errno": getattr(error, "errno", None)})

    try:
        base = safe_absolute(config.input_base)
        result["input_base"] = str(base)
        start = safe_absolute(config.data_root) if config.data_root else base
        if not start.is_relative_to(base):
            raise InputError("Explicit data_root must be within input_base")
        info = start.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise InputError("Input root is not a directory")
    except FileNotFoundError:
        result["status"] = "INPUT_ROOT_MISSING" if config.data_root else "INPUT_BASE_MISSING"
        return result
    except (OSError, InputError) as error:
        issue(config.input_base, error)
        result.update(status="INPUT_PATH_INVALID", scan_complete=False)
        return result

    def checked_stat(path):
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                result["skipped_link_count"] += 1
                if len(result["skipped_links"]) < 32:
                    result["skipped_links"].append(str(path))
                return None
            if not path.resolve().is_relative_to(base):
                raise InputError("Discovered path escaped input_base")
            return info
        except FileNotFoundError:
            return None
        except (OSError, InputError) as error:
            issue(path, error)
            return None

    pending = [(start, 0)]
    cursor = 0
    while cursor < len(pending) and result["directories_checked"] < config.max_directories:
        directory, depth = pending[cursor]
        cursor += 1
        result["directories_checked"] += 1
        files = []
        for name in MANIFEST_NAMES:
            path = directory / name
            info = checked_stat(path)
            if info is not None and stat.S_ISREG(info.st_mode):
                files.append({"name": name, "path": str(path), "size_bytes": info.st_size,
                              "modified_utc": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(), "sha256": None})
        if files:
            present = {item["name"] for item in files}
            images = {}
            for name in sorted(IMAGE_DIRECTORIES):
                path = directory / name
                info = checked_stat(path)
                if info is not None and stat.S_ISDIR(info.st_mode):
                    images[name] = str(path)
            result["candidates"].append({"root": str(directory), "files": files,
                                         "missing": [name for name in MANIFEST_NAMES if name not in present],
                                         "image_directory_candidates": images, "dicom_file_count": None})
        if config.data_root is not None or depth >= config.max_depth:
            continue
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if result["entries_checked"] >= config.max_entries:
                        result["limit_reached"] = True
                        break
                    result["entries_checked"] += 1
                    if entry.name.startswith(".") or entry.name in IMAGE_DIRECTORIES or entry.name.casefold() in PROTECTED_COMPONENTS:
                        continue
                    child = directory / entry.name
                    info = checked_stat(child)
                    if info is not None and stat.S_ISDIR(info.st_mode):
                        pending.append((child, depth + 1))
        except OSError as error:
            issue(directory, error)
        if result["limit_reached"]:
            break
    result["scan_complete"] = cursor == len(pending) and not result["limit_reached"] and not result["error_count"]
    result["candidates"].sort(key=lambda item: item["root"])
    complete = [item for item in result["candidates"] if not item["missing"]]
    if len(complete) > 1:
        result["status"] = "AMBIGUOUS_INPUT_ROOTS"
    elif not result["scan_complete"]:
        result["status"] = "DISCOVERY_INCOMPLETE"
    elif complete:
        result.update(status="MANIFESTS_LOCATED", selected_root=complete[0]["root"])
    elif result["candidates"]:
        result["status"] = "INCOMPLETE_MANIFESTS"
    return result


def read_csv_manifest(path: str | Path, config: AuditConfig) -> tuple[list[str], list[dict], str]:
    source = safe_absolute(path)
    base = safe_absolute(config.input_base)
    if not source.is_relative_to(base) or source.name not in MANIFEST_NAMES:
        raise InputError("CSV source must be a named competition manifest within input_base")
    if not stat.S_ISREG(source.lstat().st_mode):
        raise InputError("CSV source must be a regular file")
    with source.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InputError("CSV source must be a regular file")
        raw = stream.read(config.max_file_bytes + 1)
    if len(raw) > config.max_file_bytes:
        raise InputError("CSV_FILE_LIMIT_EXCEEDED")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        columns = reader.fieldnames
        if not columns or any(not name for name in columns) or len(set(columns)) != len(columns):
            raise InputError("CSV_HEADERS_MISSING_OR_DUPLICATED")
        rows = []
        for number, row in enumerate(reader, start=1):
            if number > config.max_csv_rows:
                raise InputError("CSV_ROW_LIMIT_EXCEEDED")
            if None in row or any(value is None for value in row.values()):
                raise InputError("CSV_ROW_WIDTH_INVALID")
            rows.append(row)
    except (UnicodeError, csv.Error) as error:
        raise InputError("CSV_ENCODING_OR_FORMAT_INVALID") from error
    return columns, rows, hashlib.sha256(raw).hexdigest()
