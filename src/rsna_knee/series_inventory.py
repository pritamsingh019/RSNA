"""Bounded series discovery and representative file sampling, without image reads.

Manifest identifiers are used only for private path matching. Public summaries
contain counts and placeholder path conventions, never patient/study identifiers
or filesystem paths. Completeness concerns directory discovery or the selected
sample; this module never claims to count all DICOMs in the dataset.
"""

from collections import Counter, defaultdict, deque
import os
from pathlib import Path
import stat
import time

from .contracts import SERIES_ID, STUDY_ID, validate_identifier
from .inputs import InputError, PROTECTED_COMPONENTS, safe_absolute


_CONTAINERS = {"train_series": "train", "train_images": "train",
               "test_series": "test", "test_images": "test"}
_STRATA = ("Anatomical_Plane", "Fluid_Sensitive", "Fat_Suppression")


def _validated_config(config):
    from .phase_b_config import PhaseBConfig
    if not isinstance(config, PhaseBConfig):
        raise TypeError("series inventory requires validated PhaseBConfig")


def _protected(name):
    return name.casefold() in PROTECTED_COMPONENTS or name.casefold().startswith(".env")


def _checked(path, root):
    """Check lexical containment, links/reparse points, and ancestors each time."""
    absolute = Path(os.path.abspath(path))
    if not absolute.is_relative_to(root):
        raise InputError("Discovered path escaped the selected root")
    info = absolute.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        return None
    safe_absolute(absolute)
    return info


def _error(counter, error):
    # Error messages and filenames can contain private identifiers.
    counter[type(error).__name__] += 1


def _bounded_entries(directory, budget, deadline):
    """Materialize at most budget entries; sort only this bounded collection.

    At an exact cap the scan is conservatively incomplete: determining whether
    another entry exists would itself exceed the enumeration budget. A capped
    subset follows filesystem enumeration order and is not reproducible across
    filesystems, which callers must disclose.
    """
    entries = []
    complete = True
    reason = None
    with os.scandir(directory) as iterator:
        while True:
            if time.monotonic() >= deadline:
                complete, reason = False, "time"
                break
            if len(entries) >= budget:
                complete, reason = False, "entries"
                break
            try:
                entry = next(iterator)
            except StopIteration:
                break
            entries.append(entry.name)
    return sorted(entries), complete, reason


def inventory_series(root, train_rows, test_rows, config):
    """Resolve manifest series by observed directory names with explicit limits.

    Matching SeriesInstanceUID directories are pruned rather than enumerating
    their DICOM contents. If a known StudyInstanceUID or train/test container
    appears in the ancestry, it must agree with the manifest. Other layouts are
    permitted and learned from the paths actually observed.
    """
    _validated_config(config)
    started = time.monotonic()
    summary = {
        "status": "SERIES_INVENTORY_INVALID", "scan_complete": False,
        "directories_checked": 0, "entries_checked": 0,
        "matched_series_directories_pruned": 0, "skipped_links": 0,
        "skipped_protected": 0, "inaccessible_directories": 0,
        "ancestry_mismatches": 0, "limit_reasons": [],
        "reproducible_selection": True, "error_types": {},
        "split_statistics": {}, "root_layout": {}, "path_conventions": [],
        "dicom_file_count": None, "dicom_contents_read": False,
        "count_scope": "manifest series directory inventory; DICOM files are not enumerated",
        "completeness_scope": "directory namespace outside pruned matched-series payload subtrees",
        "uniqueness_scope": "observed series-directory matches in the traversed namespace",
        "elapsed_seconds": None,
        "budgets": {name: getattr(config, name) for name in (
            "max_inventory_depth", "max_inventory_directories", "max_inventory_entries", "max_inventory_seconds")},
    }
    try:
        base = safe_absolute(config.input_base)
        root = safe_absolute(root)
        if not root.is_relative_to(base):
            raise InputError("Selected root must be within input_base")
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise InputError("Selected root must be a directory")
    except (OSError, InputError) as error:
        summary["error_types"] = {type(error).__name__: 1}
        summary["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)
        return summary, []

    expected = {}
    studies = set()
    for split, rows in (("train", train_rows), ("test", test_rows)):
        for row in rows:
            study = validate_identifier(row[STUDY_ID])
            series = validate_identifier(row[SERIES_ID])
            if series in expected:
                raise InputError("Series identifiers must be unique across manifests")
            expected[series] = (split, row)
            studies.add(study)

    hits = defaultdict(list)
    inaccessible = set()
    error_types = Counter()
    layout = Counter()
    conventions = Counter()
    limits = set()
    deadline = started + config.max_inventory_seconds
    pending = deque([(root, 0)])

    def convention(path):
        parts = []
        for part in path.relative_to(root).parts:
            if part in expected:
                parts.append("<SeriesInstanceUID>")
            elif part in studies:
                parts.append("<StudyInstanceUID>")
            elif part.casefold() in _CONTAINERS:
                parts.append(part.casefold())
            else:
                parts.append("<directory>")
        return "/".join(parts)

    while pending:
        if time.monotonic() >= deadline:
            limits.add("time")
            break
        if summary["directories_checked"] >= config.max_inventory_directories:
            limits.add("directories")
            break
        remaining = config.max_inventory_entries - summary["entries_checked"]
        if remaining <= 0:
            limits.add("entries")
            break
        directory, depth = pending.popleft()
        summary["directories_checked"] += 1
        try:
            names, complete, reason = _bounded_entries(directory, remaining, deadline)
        except OSError as error:
            summary["inaccessible_directories"] += 1
            _error(error_types, error)
            continue
        summary["entries_checked"] += len(names)
        if not complete:
            limits.add(reason)
            summary["reproducible_selection"] = False
        for name in names:
            if time.monotonic() >= deadline:
                limits.add("time")
                break
            child = directory / name
            if _protected(name):
                summary["skipped_protected"] += 1
                continue
            try:
                info = _checked(child, root)
            except (OSError, InputError) as error:
                _error(error_types, error)
                if name in expected:
                    inaccessible.add(name)
                continue
            if info is None:
                summary["skipped_links"] += 1
                continue
            is_directory = stat.S_ISDIR(info.st_mode)
            if directory == root:
                kind = (name.casefold() if name.casefold() in _CONTAINERS else
                        "study_identifier_directory" if name in studies else
                        "series_identifier_directory" if name in expected else
                        "other_directory" if is_directory else "regular_file" if stat.S_ISREG(info.st_mode) else "other_entry")
                layout[kind] += 1
            if not is_directory:
                continue
            if depth >= config.max_inventory_depth:
                limits.add("depth")
                continue
            if name in expected:
                summary["matched_series_directories_pruned"] += 1
                split, row = expected[name]
                ancestor_parts = child.relative_to(root).parts[:-1]
                known_studies = {part for part in ancestor_parts if part in studies}
                known_splits = {_CONTAINERS[part.casefold()] for part in ancestor_parts if part.casefold() in _CONTAINERS}
                if (known_studies and known_studies != {row[STUDY_ID]}) or (known_splits and known_splits != {split}):
                    summary["ancestry_mismatches"] += 1
                    continue
                # Opening a directory checks accessibility without listing its files.
                if summary["directories_checked"] >= config.max_inventory_directories:
                    limits.add("directories")
                    continue
                summary["directories_checked"] += 1
                try:
                    with os.scandir(child):
                        pass
                except OSError as error:
                    inaccessible.add(name)
                    summary["inaccessible_directories"] += 1
                    _error(error_types, error)
                    continue
                hits[name].append(child)
                conventions[(split, convention(child))] += 1
                continue
            if summary["directories_checked"] + len(pending) >= config.max_inventory_directories:
                limits.add("directories")
            else:
                pending.append((child, depth + 1))
        if not complete:
            break

    # Skipped subtrees might conceal additional candidates, so even a unique
    # observed path cannot claim exhaustive uniqueness in an incomplete scan.
    summary["scan_complete"] = not (limits or error_types or summary["skipped_links"] or summary["skipped_protected"])
    summary["status"] = "SERIES_INVENTORY_COMPLETE" if summary["scan_complete"] else "SERIES_INVENTORY_LIMITED"
    summary["limit_reasons"] = sorted(limits)
    summary["error_types"] = dict(sorted(error_types.items()))
    summary["root_layout"] = dict(sorted(layout.items()))
    summary["path_conventions"] = [
        {"split": split, "pattern": pattern, "observed_series_directories": count}
        for (split, pattern), count in sorted(conventions.items())
    ][:32]
    summary["path_conventions_omitted"] = max(0, len(conventions) - 32)
    records = []
    for split in ("train", "test"):
        identifiers = [uid for uid, (row_split, _) in expected.items() if row_split == split]
        unlocated = sum(not hits[uid] and uid not in inaccessible for uid in identifiers)
        summary["split_statistics"][split] = {
            "expected_series": len(identifiers),
            "located_series": sum(bool(hits[uid]) for uid in identifiers),
            "resolved_series": sum(len(hits[uid]) == 1 for uid in identifiers),
            "ambiguous_series": sum(len(hits[uid]) > 1 for uid in identifiers),
            "inaccessible_series": sum(uid in inaccessible for uid in identifiers),
            "missing_series": unlocated if summary["scan_complete"] else None,
            "unlocated_not_proven_missing": unlocated if not summary["scan_complete"] else 0,
        }
    for uid in sorted(expected):
        if len(hits[uid]) == 1:
            split, row = expected[uid]
            records.append({"split": split, "row": row, "path": hits[uid][0]})
    summary["resolved_uniqueness_verified"] = summary["scan_complete"]
    summary["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)
    return summary, records


def _stratum(record):
    return (record["split"], *((type(record["row"].get(field)).__name__, str(record["row"].get(field))) for field in _STRATA))


def sample_series_files(resolved_records, config):
    """Select stratified deterministic series and bounded DICOM file samples."""
    _validated_config(config)
    started = time.monotonic()
    summary = {
        "status": "SAMPLING_COMPLETE", "scan_complete": True,
        "reproducible_selection": True, "available_series": len(resolved_records),
        "selected_series": 0, "strata_count": 0, "strata_selected": 0,
        "stratification_fields": ["split", *_STRATA], "entries_checked": 0,
        "selected_files": 0, "candidate_dicom_files": 0, "unexpected_regular_files": 0,
        "unexpected_subdirectories": 0, "other_entries": 0, "skipped_links": 0,
        "skipped_protected": 0, "inaccessible_series": 0, "empty_series": 0,
        "limit_reasons": [], "error_types": {}, "representative_file_counts": [],
        "dicom_file_count": None, "dicom_contents_read": False,
        "count_scope": "immediate entries of selected series filename candidates only (.dcm/.dicom); extensions do not verify DICOM contents; incomplete per-series counts are lower bounds",
        "elapsed_seconds": None,
        "budgets": {name: getattr(config, name) for name in (
            "max_sample_series", "max_files_per_series", "max_sample_file_entries", "max_sample_total_entries", "max_header_reads", "max_dicom_seconds")},
        "split_selected_series": {"train": 0, "test": 0},
        "split_selected_files": {"train": 0, "test": 0},
        "extension_counts": {"dcm": 0, "dicom": 0, "other": 0},
        "series_without_candidate_dicom_files": 0,
        "series_with_no_candidate_dicom_filenames_in_immediate_entries": 0,
        "series_with_unscanned_subdirectories": 0,
        "unexpected_subdirectories_not_scanned": True,
    }
    base = safe_absolute(config.input_base)
    groups = defaultdict(list)
    for record in resolved_records:
        path = safe_absolute(record["path"])
        if not path.is_relative_to(base):
            raise InputError("Sample series path must be within input_base")
        groups[_stratum(record)].append(record)
    for records in groups.values():
        records.sort(key=lambda item: (item["row"][STUDY_ID], item["row"][SERIES_ID], str(item["path"])))
    strata = sorted(groups)
    summary["strata_count"] = len(strata)
    selected = []
    cursor = 0
    while len(selected) < config.max_sample_series:
        progressed = False
        for key in strata:
            if cursor < len(groups[key]):
                selected.append(groups[key][cursor])
                progressed = True
                if len(selected) == config.max_sample_series:
                    break
        if not progressed:
            break
        cursor += 1
    summary["selected_series"] = len(selected)
    summary["split_selected_series"] = {split: sum(record["split"] == split for record in selected) for split in ("train", "test")}
    summary["strata_selected"] = len({_stratum(record) for record in selected})
    summary["series_selection_capped"] = len(selected) < len(resolved_records)
    error_types = Counter()
    limits = set()
    deadline = started + config.max_dicom_seconds
    candidate_groups = []
    for index, record in enumerate(selected):
        observed = {"sample_index": index, "split": record["split"], "entries_checked": 0,
                    "candidate_dicom_files": 0, "selected_files": 0, "scan_complete": True,
                    "file_count_exact": True, "inaccessible": False,
                    "unexpected_subdirectories": 0}
        summary["representative_file_counts"].append(observed)
        candidates = []
        candidate_groups.append(candidates)
        remaining = config.max_sample_total_entries - summary["entries_checked"]
        budget = min(config.max_sample_file_entries, remaining)
        if budget <= 0 or time.monotonic() >= deadline:
            limits.add("total_entries" if budget <= 0 else "time")
            observed.update(scan_complete=False, file_count_exact=False)
            continue
        path = Path(record["path"])
        try:
            info = _checked(path, base)
            if info is None or not stat.S_ISDIR(info.st_mode):
                raise InputError("Sample series is no longer a safe directory")
            names, complete, reason = _bounded_entries(path, budget, deadline)
        except (OSError, InputError) as error:
            summary["inaccessible_series"] += 1
            _error(error_types, error)
            observed.update(scan_complete=False, file_count_exact=False, inaccessible=True)
            continue
        observed["entries_checked"] = len(names)
        summary["entries_checked"] += len(names)
        observed.update(scan_complete=complete, file_count_exact=complete)
        if not complete:
            limits.add("per_series_entries" if reason == "entries" and budget == config.max_sample_file_entries else "total_entries" if reason == "entries" else reason)
            summary["reproducible_selection"] = False
        for name in names:
            if time.monotonic() >= deadline:
                limits.add("time")
                observed.update(scan_complete=False, file_count_exact=False)
                break
            if _protected(name):
                summary["skipped_protected"] += 1
                observed.update(scan_complete=False, file_count_exact=False)
                continue
            child = path / name
            try:
                info = _checked(child, base)
            except (OSError, InputError) as error:
                _error(error_types, error)
                observed.update(scan_complete=False, file_count_exact=False)
                continue
            if info is None:
                summary["skipped_links"] += 1
                observed.update(scan_complete=False, file_count_exact=False)
            elif stat.S_ISREG(info.st_mode):
                extension = child.suffix.casefold().lstrip(".")
                extension_type = extension if extension in {"dcm", "dicom"} else "other"
                summary["extension_counts"][extension_type] += 1
                if extension_type in {"dcm", "dicom"}:
                    observed["candidate_dicom_files"] += 1
                    summary["candidate_dicom_files"] += 1
                    if len(candidates) < config.max_files_per_series:
                        candidates.append({"split": record["split"], "row": record["row"],
                                           "path": child, "series_path": path})
                else:
                    summary["unexpected_regular_files"] += 1
            elif stat.S_ISDIR(info.st_mode):
                summary["unexpected_subdirectories"] += 1
                observed["unexpected_subdirectories"] += 1
            else:
                summary["other_entries"] += 1
        if complete and not names:
            summary["empty_series"] += 1
    # Round-robin file allocation keeps all selected strata represented before
    # assigning second files, even if the independent header budget is small.
    files = []
    for position in range(config.max_files_per_series):
        for index, candidates in enumerate(candidate_groups):
            if position < len(candidates) and len(files) < config.max_header_reads:
                files.append(candidates[position])
                summary["representative_file_counts"][index]["selected_files"] += 1
    summary["header_selection_capped"] = sum(len(group) for group in candidate_groups) > len(files)
    summary["selected_files"] = len(files)
    summary["split_selected_files"] = {split: sum(record["split"] == split for record in files) for split in ("train", "test")}
    summary["series_without_candidate_dicom_files"] = sum(not item["candidate_dicom_files"] for item in summary["representative_file_counts"])
    summary["series_with_no_candidate_dicom_filenames_in_immediate_entries"] = sum(not item["candidate_dicom_files"] and item["file_count_exact"] and not item["unexpected_subdirectories"] for item in summary["representative_file_counts"])
    summary["series_with_unscanned_subdirectories"] = sum(bool(item["unexpected_subdirectories"]) for item in summary["representative_file_counts"])
    summary["scan_complete"] = not (limits or error_types or summary["skipped_links"] or summary["skipped_protected"])
    if not summary["scan_complete"]:
        summary["status"] = "SAMPLING_LIMITED"
    summary["limit_reasons"] = sorted(limits)
    summary["error_types"] = dict(sorted(error_types.items()))
    summary["elapsed_seconds"] = round(max(0, time.monotonic() - started), 6)
    return summary, files
