"""Bounded DICOM smoke audit; sampled aggregates only, never image caches or IDs.

Pydicom is optional and is imported only when this audit is requested. Time
limits are cooperative: stream operations and attempt boundaries check elapsed
time, but an already running native decoder cannot be forcibly interrupted.
"""

from collections import Counter, defaultdict
import importlib
from itertools import islice
import logging
import math
import os
from pathlib import Path
import re
import stat
import statistics
import struct
import time
import warnings

from .inputs import InputError, safe_absolute


_UID = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*\Z")
_MODALITIES = frozenset("MR CT CR DX US NM PT XA RF MG OT SR SC SEG RTIMAGE RTDOSE RTSTRUCT".split())
_PHOTOMETRIC = frozenset(("MONOCHROME1", "MONOCHROME2", "RGB", "PALETTE COLOR", "YBR_FULL", "YBR_FULL_422", "YBR_PARTIAL_422", "YBR_PARTIAL_420", "YBR_ICT", "YBR_RCT"))
_IMAGE_TYPES = frozenset("ORIGINAL DERIVED PRIMARY SECONDARY AXIAL SAGITTAL CORONAL LOCALIZER OTHER NONE MPR PROJECTION VOLUME MAXIMUM MINIMUM AVERAGE MAGNITUDE PHASE REAL IMAGINARY".split())
_PLUGINS = frozenset(("numpy", "pydicom", "pylibjpeg", "pillow", "gdcm"))


class _BudgetExceeded(Exception):
    pass


class _TimeExceeded(Exception):
    pass


class _UnsupportedEncoding(Exception):
    pass


class _BoundedStream:
    """Cap cumulative reads and accessible offsets, including parser seeks."""

    def __init__(self, stream, limit, deadline):
        self.stream = stream
        self.limit = limit
        self.deadline = deadline
        self.bytes_read = 0
        self.size = os.fstat(stream.fileno()).st_size

    def _check(self):
        if time.perf_counter() >= self.deadline:
            raise _TimeExceeded()

    def read(self, size=-1):
        self._check()
        remaining = self.limit - self.bytes_read
        requested = remaining if size is None or size < 0 else size
        if requested > remaining:
            raise _BudgetExceeded()
        if requested == 0 and (size is None or size < 0) and self.stream.tell() < self.size:
            raise _BudgetExceeded()
        data = self.stream.read(requested)
        self.bytes_read += len(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def seek(self, offset, whence=0):
        self._check()
        target = offset if whence == 0 else self.stream.tell() + offset if whence == 1 else self.size + offset if whence == 2 else -1
        if target < 0 or target > self.limit:
            raise _BudgetExceeded()
        return self.stream.seek(target)

    def tell(self):
        return self.stream.tell()


def _load_pydicom():
    return importlib.import_module("pydicom")


def _reject_deflated_dataset(stream):
    """Inspect bounded Part10 meta bytes before pydicom can inflate a dataset.

    Group 0002 uses explicit VR little endian even when the following dataset
    uses another syntax. No metadata text is returned or logged.
    """
    prefix = stream.read(132)
    if len(prefix) == 132 and prefix[128:] == b"DICM":
        long_vrs = {b"OB", b"OD", b"OF", b"OL", b"OV", b"OW", b"SQ", b"SV", b"UC", b"UN", b"UR", b"UT", b"UV"}
        for _ in range(256):
            header = stream.read(8)
            if len(header) != 8:
                break
            group, element = struct.unpack("<HH", header[:4])
            if group != 2:
                break
            vr = header[4:6]
            if vr in long_vrs:
                length_bytes = stream.read(4)
                if len(length_bytes) != 4:
                    raise ValueError("Invalid file meta")
                length = struct.unpack("<I", length_bytes)[0]
            else:
                length = struct.unpack("<H", header[6:8])[0]
            if element == 0x10:
                if length > 66:
                    raise ValueError("Invalid transfer syntax metadata")
                syntax = stream.read(length).strip(b"\0 \t\n\r\v\f")
                if syntax == b"1.2.840.10008.1.2.1.99":
                    raise _UnsupportedEncoding()
            else:
                stream.seek(length, 1)
        else:
            raise _BudgetExceeded()
    stream.seek(0)


def _value(dataset, name):
    try:
        return getattr(dataset, name, None)
    except Exception:
        return None


def _text(value, limit=256):
    if value is None:
        return None
    try:
        text = str(value)
    except Exception:
        return None
    return text if text and len(text) <= limit and not any(ord(c) < 32 for c in text) else None


def _uid(value):
    text = _text(value, 64)
    return text if text is not None and _UID.fullmatch(text) else None


def _number(value, lower=-1000000, upper=1000000):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and lower <= number <= upper else None


def _integer(value, lower=0, upper=1000000):
    number = _number(value, lower, upper)
    return int(number) if number is not None and number.is_integer() else None


def _vector(value, length, lower=-1000000, upper=1000000):
    if isinstance(value, (str, bytes)):
        return None
    try:
        if len(value) != length:
            return None
        numbers = tuple(_number(item, lower, upper) for item in value)
    except (TypeError, ValueError):
        return None
    return numbers if all(item is not None for item in numbers) else None


def _statistics(values):
    return {"count": len(values), "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "median": statistics.median(values) if values else None}


def _histogram(counter, limit=24):
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return {"values": {key: value for key, value in items[:limit]},
            "other_count": sum(value for _, value in items[limit:]),
            "distinct_count": len(items), "maximum_categories": limit}


def _normal(orientation):
    if orientation is None:
        return None
    row, column = orientation[:3], orientation[3:]
    if any(abs(sum(component * component for component in vector) - 1) > 0.05 for vector in (row, column)):
        return None
    if abs(sum(a * b for a, b in zip(row, column))) > 0.05:
        return None
    cross = (row[1] * column[2] - row[2] * column[1], row[2] * column[0] - row[0] * column[2], row[0] * column[1] - row[1] * column[0])
    norm = math.sqrt(sum(component * component for component in cross))
    return tuple(component / norm for component in cross) if norm > 0 else None


def _geometry(groups):
    gaps = []
    result = {"status": "NOT_ASSESSED", "sampled_series_count": len(groups),
              "physical_order_supported_series_count": 0, "orientation_inconsistent_series_count": 0,
              "insufficient_position_series_count": 0, "duplicate_position_count": 0,
              "duplicate_instance_number_count": 0, "instance_order_direction_change_series_count": 0,
              "large_sampled_gap_count": 0,
              "method": "Project ImagePositionPatient onto cross(row, column) from ImageOrientationPatient; sort projections. SliceThickness is never substituted for position spacing.",
              "scope": "Sampled positions only. Large sample gaps do not establish missing slices or complete-series spacing."}
    for samples in groups.values():
        usable = [(normal, position, instance) for normal, position, instance in samples if normal is not None and position is not None]
        instances = [instance for _, _, instance in samples if instance is not None]
        result["duplicate_instance_number_count"] += len(instances) - len(set(instances))
        if len(usable) < 2:
            result["insufficient_position_series_count"] += 1
            continue
        reference = usable[0][0]
        if any(sum(a * b for a, b in zip(reference, normal)) < 0.999 for normal, _, _ in usable):
            result["orientation_inconsistent_series_count"] += 1
            continue
        positions = sorted(sum(a * b for a, b in zip(reference, position)) for _, position, _ in usable)
        distances = [right - left for left, right in zip(positions, positions[1:])]
        result["duplicate_position_count"] += sum(gap <= 0.0001 for gap in distances)
        positive = [gap for gap in distances if gap > 0.0001]
        gaps.extend(positive)
        if positive:
            median = statistics.median(positive)
            result["large_sampled_gap_count"] += sum(gap > median * 1.5 for gap in positive)
        by_instance = sorted((instance, sum(a * b for a, b in zip(reference, position))) for _, position, instance in usable if instance is not None)
        differences = [right[1] - left[1] for left, right in zip(by_instance, by_instance[1:])]
        if any(gap > 0.0001 for gap in differences) and any(gap < -0.0001 for gap in differences):
            result["instance_order_direction_change_series_count"] += 1
        result["physical_order_supported_series_count"] += 1
    result["sampled_position_gap_statistics_mm"] = _statistics(gaps)
    if result["physical_order_supported_series_count"]:
        result["status"] = "PASS"
    if result["orientation_inconsistent_series_count"] or result["duplicate_position_count"] or result["duplicate_instance_number_count"] or result["instance_order_direction_change_series_count"]:
        result["status"] = "FAIL"
    return result


def _codec_availability(pydicom, syntax):
    result = {"availability_status": "NOT_ASSESSED", "available_plugins": [], "sample_decode_status": "NOT_ASSESSED", "decode_attempts": 0, "decode_successes": 0, "decode_failures": 0}
    try:
        pixels = getattr(pydicom, "pixels", None)
        get_decoder = getattr(pixels, "get_decoder", None)
        if get_decoder:
            decoder = get_decoder(syntax)
            available = bool(decoder.is_available)
            result.update(availability_status="PASS" if available else "BLOCKED",
                          available_plugins=sorted({str(name).casefold() for name in decoder.available_plugins if str(name).casefold() in _PLUGINS}))
        else:
            handlers = getattr(getattr(pydicom, "config", None), "pixel_data_handlers", None)
            if handlers is not None:
                supporting = [handler for handler in handlers if handler.supports_transfer_syntax(syntax) and handler.is_available()]
                result["availability_status"] = "PASS" if supporting else "BLOCKED"
                result["available_plugins"] = sorted({str(getattr(handler, "HANDLER_NAME", "")).casefold() for handler in supporting} & _PLUGINS)
    except Exception:
        pass
    return result


def _record_blocked_deflate(result):
    """Record the one policy-blocked numeric syntax without codec assumptions."""
    syntax = "1.2.840.10008.1.2.1.99"
    observed = result["transfer_syntaxes"]["observed"]
    if syntax not in observed:
        observed[syntax] = {"availability_status": "NOT_ASSESSED", "available_plugins": [],
                            "sample_decode_status": "NOT_ASSESSED", "decode_attempts": 0,
                            "decode_successes": 0, "decode_failures": 0, "file_meta_count": 0,
                            "audit_policy_status": "BLOCKED",
                            "reason": "Deflated whole-dataset parsing is not attempted by this bounded audit"}
    observed[syntax]["file_meta_count"] += 1


def _failure(error):
    if isinstance(error, _TimeExceeded):
        return "time_limit"
    if isinstance(error, _BudgetExceeded):
        return "read_byte_limit"
    if isinstance(error, FileNotFoundError):
        return "file_missing"
    if isinstance(error, PermissionError):
        return "inaccessible"
    if isinstance(error, InputError):
        return "unsafe_path"
    if isinstance(error, MemoryError):
        return "memory_limit"
    if isinstance(error, OSError):
        return "filesystem_error"
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "decoder_dependency_unavailable"
    if isinstance(error, NotImplementedError):
        return "unsupported_encoding"
    if isinstance(error, _UnsupportedEncoding):
        return "deflated_dataset_not_audited"
    return "invalid_header_or_decode"


def _checked_path(record, base):
    source = safe_absolute(record["path"])
    series = safe_absolute(record["series_path"])
    if not source.is_relative_to(base) or not series.is_relative_to(base) or not source.is_relative_to(series):
        raise InputError("DICOM path is outside the permitted input series")
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise InputError("DICOM source is not a regular file")
    return source, info.st_size


def inspect_dicom(file_records, config) -> dict:
    """Suppress parser diagnostics, including child loggers created lazily.

    The notebook runs this audit sequentially. Its temporary process-wide log
    and warning suppression also affects other threads; both states are restored
    on exit. Disabling only the parent pydicom logger would not suppress child
    loggers with their own handlers or propagated records.
    """
    was_disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _inspect_dicom(file_records, config)
    finally:
        logging.disable(was_disabled)


def _inspect_dicom(file_records, config) -> dict:
    """Inspect inventory-selected files in deterministic order with explicit scope.

    Records are the already bounded, stratified inventory sample. No record,
    identifier, raw string exception, report or pixel array is serialized.
    """
    started = time.perf_counter()
    deadline = started + config.max_dicom_seconds
    limits = {name: getattr(config, name) for name in ("max_header_reads", "max_header_bytes", "max_dicom_file_bytes", "max_pixel_decodes", "max_pixel_elements", "max_dicom_seconds")}
    result = {"status": "NOT_ASSESSED", "scope": "Bounded deterministic inventory sample only; no complete DICOM integrity or patient mapping claim.",
              "limits": limits, "runtime_seconds": 0.0, "time_limit_reached": False,
              "time_limit_enforcement": "Cooperative between reads and attempts; cannot interrupt an already running native decoder.",
              "headers": {"status": "NOT_ASSESSED", "attempts": 0, "successes": 0, "failures": 0, "bytes_read": 0, "failure_categories": {}, "sample_limit_reached": False},
              "identifiers": {}, "metadata": {}, "geometry": {"status": "NOT_ASSESSED"},
              "transfer_syntaxes": {"observed": {}, "unencountered_syntax_support": "NOT_ASSESSED"},
              "decoding": {"status": "NOT_ASSESSED", "requested": config.max_pixel_decodes > 0, "attempts": 0, "successes": 0, "failures": 0, "skipped_resource_limits": 0, "failure_categories": {}, "skip_categories": {}, "bytes_read": 0, "array_shapes": {}, "constant_arrays": 0, "all_zero_arrays": 0},
              "patient_grouping": {"status": "NOT_ASSESSED", "trustworthy_full_study_mapping": False, "reason": "Sampled PatientID availability and consistency do not establish reliable patient grouping for all studies."}}
    try:
        pydicom = _load_pydicom()
    except ImportError:
        result.update(status="BLOCKED", dependency={"pydicom": "UNAVAILABLE", "action": "No installation attempted"})
        result["headers"]["status"] = result["decoding"]["status"] = "BLOCKED"
        result["runtime_seconds"] = time.perf_counter() - started
        return result
    result["dependency"] = {"pydicom": "AVAILABLE"}
    header_errors, decode_errors, skips = Counter(), Counter(), Counter()
    missing, malformed, numeric = Counter(), Counter(), defaultdict(list)
    categorical = {name: Counter() for name in ("Modality", "ImageType", "PhotometricInterpretation", "dimensions", "PixelSpacing", "ImageOrientationPatient", "BitsStored", "PixelRepresentation")}
    presence = Counter()
    uid_sets = {name: set() for name in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID")}
    groups, patient_ids, study_ids, series_ids = defaultdict(list), defaultdict(set), defaultdict(set), defaultdict(set)
    id_counts = Counter()
    syntax_counts, shapes, splits = Counter(), Counter(), Counter()
    decode_candidates = []
    try:
        base = safe_absolute(config.input_base)
    except (InputError, OSError):
        result.update(status="BLOCKED", reason="INPUT_PATH_UNSAFE")
        result["runtime_seconds"] = time.perf_counter() - started
        return result

    for number, record in enumerate(file_records):
        if number >= config.max_header_reads:
            result["headers"]["sample_limit_reached"] = True
            break
        if time.perf_counter() >= deadline:
            result["time_limit_reached"] = True
            break
        result["headers"]["attempts"] += 1
        stream = None
        try:
            source, size = _checked_path(record, base)
            with source.open("rb") as raw:
                stream = _BoundedStream(raw, config.max_header_bytes, deadline)
                _reject_deflated_dataset(stream)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    dataset = pydicom.dcmread(stream, stop_before_pixels=True, force=False)
            row = record["row"]
            key = (record.get("split", ""), row.get("StudyInstanceUID", ""), row.get("SeriesInstanceUID", ""))
            identifiers = {}
            for name in uid_sets:
                value = _value(dataset, name)
                identifier = _uid(value)
                if value is None or value == "":
                    missing[name] += 1
                elif identifier is None:
                    malformed[name] += 1
                else:
                    id_counts[name + "_present"] += 1
                    if name == "SOPInstanceUID" and identifier in uid_sets[name]:
                        id_counts["duplicate_sop_instance_uid_count"] += 1
                    uid_sets[name].add(identifier)
                identifiers[name] = identifier
            for name, target in (("StudyInstanceUID", study_ids), ("SeriesInstanceUID", series_ids)):
                identifier = identifiers[name]
                if identifier is not None:
                    target[key].add(identifier)
                    if identifier != row.get(name):
                        id_counts[name + "_manifest_mismatch_count"] += 1
            patient = _text(_value(dataset, "PatientID"))
            if patient is not None:
                id_counts["PatientID_present"] += 1
                patient_ids[key].add(patient)
            else:
                missing["PatientID"] += 1
            if _value(dataset, "SeriesDescription") not in (None, ""):
                presence["SeriesDescription"] += 1
            for name, allowed in (("Modality", _MODALITIES), ("PhotometricInterpretation", _PHOTOMETRIC)):
                value = _text(_value(dataset, name), 32)
                if value is None:
                    missing[name] += 1
                else:
                    categorical[name][value if value in allowed else "OTHER"] += 1
            image_type = _value(dataset, "ImageType")
            if image_type is None:
                missing["ImageType"] += 1
            else:
                tokens = image_type.split("\\", 8)[:8] if isinstance(image_type, str) else list(islice(iter(image_type), 8))
                safe_tokens = [_text(token, 32) for token in tokens[:8]]
                categorical["ImageType"]["\\".join(token if token in _IMAGE_TYPES else "OTHER" for token in safe_tokens)] += 1
            integers = {}
            for name, lower, upper in (("Rows", 1, 1000000), ("Columns", 1, 1000000), ("BitsStored", 1, 64), ("PixelRepresentation", 0, 1), ("InstanceNumber", -1000000, 1000000)):
                raw_value = _value(dataset, name)
                value = _integer(raw_value, lower, upper)
                integers[name] = value
                if raw_value is None:
                    missing[name] += 1
                elif value is None:
                    malformed[name] += 1
                else:
                    numeric[name].append(value)
                    if name in categorical:
                        categorical[name][str(value)] += 1
            if integers["Rows"] and integers["Columns"]:
                categorical["dimensions"][f"{integers['Rows']}x{integers['Columns']}"] += 1
            spacing = _vector(_value(dataset, "PixelSpacing"), 2, 0.000001)
            orientation = _vector(_value(dataset, "ImageOrientationPatient"), 6, -1.1, 1.1)
            position = _vector(_value(dataset, "ImagePositionPatient"), 3)
            for name, value in (("PixelSpacing", spacing), ("ImageOrientationPatient", orientation), ("ImagePositionPatient", position)):
                if value is None:
                    (missing if _value(dataset, name) is None else malformed)[name] += 1
                else:
                    presence[name] += 1
                    if name in categorical:
                        categorical[name][",".join(f"{item:.4g}" for item in value)] += 1
                    for item in value:
                        numeric[name + "_components"].append(item)
            for name in ("SpacingBetweenSlices", "SliceThickness", "RescaleSlope", "RescaleIntercept"):
                raw_value = _value(dataset, name)
                value = _number(raw_value)
                if raw_value is None:
                    missing[name] += 1
                elif value is None:
                    malformed[name] += 1
                else:
                    numeric[name].append(value)
            normal = _normal(orientation)
            if orientation is not None and normal is None:
                malformed["ImageOrientationPatient_basis"] += 1
            groups[key].append((normal, position, integers["InstanceNumber"]))
            syntax_value = _value(_value(dataset, "file_meta"), "TransferSyntaxUID")
            syntax = _uid(syntax_value)
            if syntax is None:
                (missing if syntax_value is None else malformed)["TransferSyntaxUID"] += 1
            else:
                syntax_counts[syntax] += 1
                if syntax not in result["transfer_syntaxes"]["observed"]:
                    result["transfer_syntaxes"]["observed"][syntax] = _codec_availability(pydicom, syntax)
            splits[record.get("split") if record.get("split") in ("train", "test") else "other"] += 1
            result["headers"]["successes"] += 1
            decode_candidates.append((record, dataset, syntax, size))
        except Exception as error:
            category = _failure(error)
            if isinstance(error, _UnsupportedEncoding):
                _record_blocked_deflate(result)
            header_errors[category] += 1
            result["headers"]["failures"] += 1
            if category == "time_limit":
                result["time_limit_reached"] = True
        finally:
            if stream is not None:
                result["headers"]["bytes_read"] += stream.bytes_read
        if result["time_limit_reached"]:
            break

    for record, dataset, syntax, _ in decode_candidates:
        if result["decoding"]["attempts"] >= config.max_pixel_decodes:
            break
        if time.perf_counter() >= deadline:
            result["time_limit_reached"] = True
            break
        rows = _integer(_value(dataset, "Rows"), 1)
        columns = _integer(_value(dataset, "Columns"), 1)
        frames = _integer(_value(dataset, "NumberOfFrames") if _value(dataset, "NumberOfFrames") is not None else 1, 1)
        components = _integer(_value(dataset, "SamplesPerPixel"), 1, 16)
        bits = _integer(_value(dataset, "BitsAllocated"), 1, 64)
        stored = _integer(_value(dataset, "BitsStored"), 1, 64)
        if None in (rows, columns, frames, components, bits, stored) or bits not in (1, 8, 16, 32, 64) or stored > bits:
            skips["invalid_pixel_metadata"] += 1
            continue
        elements = rows * columns * frames * components
        declared_bytes = (elements * bits + 7) // 8
        if elements > config.max_pixel_elements or declared_bytes > config.max_dicom_file_bytes:
            result["decoding"]["skipped_resource_limits"] += 1
            skips["declared_pixel_budget"] += 1
            continue
        stream = None
        attempted = False
        array = full = None
        try:
            source, size = _checked_path(record, base)
            if size > config.max_dicom_file_bytes:
                result["decoding"]["skipped_resource_limits"] += 1
                skips["file_byte_budget"] += 1
                continue
            # Count an attempt when the bounded full read begins, including failures.
            result["decoding"]["attempts"] += 1
            attempted = True
            if syntax is not None:
                result["transfer_syntaxes"]["observed"][syntax]["decode_attempts"] += 1
            with source.open("rb") as raw:
                stream = _BoundedStream(raw, config.max_dicom_file_bytes, deadline)
                _reject_deflated_dataset(stream)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    full = pydicom.dcmread(stream, stop_before_pixels=False, force=False)
                    # Recheck dimensions before invoking a codec, in case a file
                    # changed between its header and full-read smoke test.
                    full_frames = _value(full, "NumberOfFrames")
                    full_frames = _integer(1 if full_frames is None else full_frames, 1)
                    full_shape = (_integer(_value(full, "Rows"), 1), _integer(_value(full, "Columns"), 1), full_frames, _integer(_value(full, "SamplesPerPixel"), 1, 16), _integer(_value(full, "BitsAllocated"), 1, 64))
                    if full_shape != (rows, columns, frames, components, bits):
                        raise _BudgetExceeded()
                    array = full.pixel_array
            shape = tuple(int(item) for item in array.shape)
            expected = ((frames,) if frames > 1 else ()) + (rows, columns) + ((components,) if components > 1 else ())
            if shape != expected or int(array.size) != elements or int(array.nbytes) > config.max_dicom_file_bytes or getattr(array.dtype, "kind", None) not in ("i", "u", "f"):
                decode_errors["array_shape_or_type"] += 1
                result["decoding"]["failures"] += 1
            else:
                minimum, maximum = float(array.min()), float(array.max())
                if not math.isfinite(minimum) or not math.isfinite(maximum):
                    decode_errors["nonfinite_pixels"] += 1
                    result["decoding"]["failures"] += 1
                else:
                    result["decoding"]["successes"] += 1
                    shapes["x".join(str(item) for item in shape)] += 1
                    result["decoding"]["constant_arrays"] += int(minimum == maximum)
                    result["decoding"]["all_zero_arrays"] += int(minimum == maximum == 0)
                    if syntax is not None:
                        result["transfer_syntaxes"]["observed"][syntax]["decode_successes"] += 1
        except Exception as error:
            category = _failure(error)
            if isinstance(error, _UnsupportedEncoding):
                _record_blocked_deflate(result)
            # Path errors before the full read are skips, not decoding attempts.
            if not attempted:
                skips[category] += 1
            else:
                decode_errors[category] += 1
                result["decoding"]["failures"] += 1
            if category == "time_limit":
                result["time_limit_reached"] = True
        finally:
            if stream is not None:
                result["decoding"]["bytes_read"] += stream.bytes_read
            array = full = None

    for name, value in result["transfer_syntaxes"]["observed"].items():
        value["header_count"] = syntax_counts[name]
        value["decode_failures"] = value["decode_attempts"] - value["decode_successes"]
        if value["decode_attempts"]:
            value["sample_decode_status"] = "FAIL" if value["decode_failures"] else "PASS"
    id_counts["StudyInstanceUID_inconsistent_series_count"] = sum(len(values) > 1 for values in study_ids.values())
    id_counts["SeriesInstanceUID_inconsistent_series_count"] = sum(len(values) > 1 for values in series_ids.values())
    id_counts["PatientID_inconsistent_series_count"] = sum(len(values) > 1 for values in patient_ids.values())
    identity_fail = any(value for name, value in id_counts.items() if "mismatch" in name or "inconsistent" in name or name == "duplicate_sop_instance_uid_count") or any(missing[name] or malformed[name] for name in uid_sets)
    result["identifiers"] = {"status": "FAIL" if identity_fail else "PASS" if groups else "NOT_ASSESSED", "counts": dict(id_counts), "distinct_header_uid_counts": {name: len(values) for name, values in uid_sets.items()}, "sampled_series_count": len(groups), "raw_identifiers_emitted": False}
    result["metadata"] = {"status": "FAIL" if malformed else "PASS" if groups else "NOT_ASSESSED", "missing_field_counts": dict(missing), "malformed_field_counts": dict(malformed), "presence_counts": dict(presence), "numeric_statistics": {name: _statistics(values) for name, values in sorted(numeric.items())}, "distributions": {name: _histogram(values) for name, values in categorical.items()}, "successful_header_split_counts": dict(splits), "SeriesDescription_policy": "Presence only; no description text emitted"}
    result["geometry"] = _geometry(groups)
    result["headers"]["failure_categories"] = dict(header_errors)
    result["decoding"].update(failure_categories=dict(decode_errors), skip_categories=dict(skips), array_shapes=_histogram(shapes))
    blocked_categories = {"time_limit", "read_byte_limit", "memory_limit", "deflated_dataset_not_audited"}
    header_bad = any(count for name, count in header_errors.items() if name not in blocked_categories)
    result["headers"]["status"] = "FAIL" if header_bad else "BLOCKED" if header_errors else "PASS" if result["headers"]["successes"] else "NOT_ASSESSED"
    decode_blocked_categories = blocked_categories | {"decoder_dependency_unavailable", "unsupported_encoding"}
    decode_bad = any(count for name, count in decode_errors.items() if name not in decode_blocked_categories)
    result["decoding"]["status"] = "FAIL" if decode_bad else "BLOCKED" if decode_errors or skips else "PASS" if result["decoding"]["successes"] else "BLOCKED" if config.max_pixel_decodes > 0 else "NOT_ASSESSED"
    result["status"] = "FAIL" if header_bad or decode_bad or identity_fail or result["geometry"]["status"] == "FAIL" or malformed else "BLOCKED" if header_errors or result["decoding"]["status"] == "BLOCKED" or not result["headers"]["successes"] else "PASS"
    if result["time_limit_reached"] and result["status"] != "FAIL":
        result["status"] = "BLOCKED"
    result["runtime_seconds"] = time.perf_counter() - started
    return result
