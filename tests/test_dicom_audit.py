"""Synthetic DICOM interface tests; no competition data or dependency installs."""

from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import json
import logging
import struct
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

from rsna_knee.dicom_audit import _BoundedStream, _BudgetExceeded, _TimeExceeded, inspect_dicom


class FakeArray:
    def __init__(self, shape=(2, 3), minimum=0, maximum=5, kind="u"):
        self.shape = shape
        self.size = 1
        for dimension in shape:
            self.size *= dimension
        self.nbytes = self.size * 2
        self.dtype = SimpleNamespace(kind=kind)
        self.minimum, self.maximum = minimum, maximum

    def min(self):
        return self.minimum

    def max(self):
        return self.maximum


class FakeDataset(SimpleNamespace):
    @property
    def pixel_array(self):
        self.pixel_accesses += 1
        if self.decode_error is not None:
            raise self.decode_error
        return self.array


class DicomAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name) / "input"
        self.series = self.base / "images" / "synthetic-series"
        self.series.mkdir(parents=True)
        self.config = SimpleNamespace(input_base=str(self.base), max_header_reads=128,
                                      max_header_bytes=2 * 1024 * 1024,
                                      max_dicom_file_bytes=64 * 1024 * 1024,
                                      max_pixel_decodes=12, max_pixel_elements=4 * 1024 * 1024,
                                      max_dicom_seconds=90)
        self.datasets = {}
        self.records = []
        self.calls = []
        self.decoder = SimpleNamespace(is_available=True, available_plugins=("numpy", "PRIVATE_PLUGIN_VALUE"))
        self.pydicom = SimpleNamespace(dcmread=self.read, pixels=SimpleNamespace(get_decoder=lambda syntax: self.decoder))

    def changed(self, **values):
        return SimpleNamespace(**(vars(self.config) | values))

    def add(self, number=1, **changes):
        path = self.series / f"slice_{number:03}.dcm"
        path.write_bytes(bytes([number]) + b"synthetic-bytes" * 20)
        values = dict(StudyInstanceUID="1.2.100", SeriesInstanceUID="1.2.200",
                      SOPInstanceUID=f"1.2.300.{number}", PatientID="PRIVATE_PATIENT_VALUE",
                      SeriesDescription="PRIVATE_DESCRIPTION_VALUE", ImageType=["ORIGINAL", "PRIMARY"],
                      Modality="MR", Rows=2, Columns=3, PixelSpacing=[0.5, 0.5],
                      ImageOrientationPatient=[1, 0, 0, 0, 1, 0],
                      ImagePositionPatient=[0, 0, float(number)], InstanceNumber=number,
                      SliceThickness=77, SpacingBetweenSlices=1, BitsStored=12, BitsAllocated=16,
                      SamplesPerPixel=1, PixelRepresentation=0, PhotometricInterpretation="MONOCHROME2",
                      RescaleSlope=1, RescaleIntercept=0,
                      file_meta=SimpleNamespace(TransferSyntaxUID="1.2.840.10008.1.2.1"),
                      array=FakeArray(), decode_error=None, pixel_accesses=0)
        values.update(changes)
        dataset = FakeDataset(**values)
        self.datasets[number] = dataset
        record = {"path": path, "series_path": self.series, "split": "train",
                  "row": {"StudyInstanceUID": "1.2.100", "SeriesInstanceUID": "1.2.200"}}
        self.records.append(record)
        return dataset

    def read(self, stream, *, stop_before_pixels, force):
        self.assertFalse(force)
        number = stream.read(1)[0]
        self.calls.append((number, stop_before_pixels))
        return self.datasets[number]

    def audit(self, config=None):
        with patch("rsna_knee.dicom_audit._load_pydicom", return_value=self.pydicom):
            return inspect_dicom(self.records, config or self.config)

    def test_optional_dependency_missing_is_blocked_without_reads(self):
        self.add()
        with patch("rsna_knee.dicom_audit._load_pydicom", side_effect=ModuleNotFoundError("PRIVATE_DEPENDENCY_MESSAGE")):
            result = inspect_dicom(self.records, self.config)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["headers"]["attempts"], 0)
        self.assertNotIn("PRIVATE_DEPENDENCY_MESSAGE", json.dumps(result))

    def test_empty_sample_does_not_claim_coverage(self):
        result = self.audit()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["decoding"]["attempts"], 0)
        self.assertFalse(result["patient_grouping"]["trustworthy_full_study_mapping"])

    def test_success_is_compact_and_preserves_inputs(self):
        self.add(1)
        self.add(2)
        before = {record["path"]: hashlib.sha256(record["path"].read_bytes()).hexdigest() for record in self.records}
        result = self.audit()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["headers"]["attempts"], 2)
        self.assertEqual(result["headers"]["successes"], 2)
        self.assertEqual(result["decoding"]["successes"], 2)
        self.assertEqual(result["headers"]["bytes_read"], 266)
        self.assertEqual(result["decoding"]["bytes_read"], 266)
        serialized = json.dumps(result, allow_nan=False)
        for secret in ("PRIVATE_PATIENT_VALUE", "PRIVATE_DESCRIPTION_VALUE", "PRIVATE_PLUGIN_VALUE", "1.2.100", "1.2.200", "1.2.300.1", str(self.base), "synthetic-series", "slice_001"):
            self.assertNotIn(secret, serialized)
        self.assertLess(len(serialized), 20000)
        self.assertEqual(before, {record["path"]: hashlib.sha256(record["path"].read_bytes()).hexdigest() for record in self.records})
        self.assertFalse(result["patient_grouping"]["trustworthy_full_study_mapping"])
        self.assertEqual(result["transfer_syntaxes"]["unencountered_syntax_support"], "NOT_ASSESSED")
        observed = result["transfer_syntaxes"]["observed"]["1.2.840.10008.1.2.1"]
        self.assertEqual(observed["available_plugins"], ["numpy"])

    def test_header_and_pixel_budgets_count_attempts_deterministically(self):
        for number in range(1, 8):
            self.add(number)
        config = self.changed(max_header_reads=4, max_pixel_decodes=2)
        result = self.audit(config)
        self.assertEqual(self.calls, [(1, True), (2, True), (3, True), (4, True), (1, False), (2, False)])
        self.assertEqual(result["headers"]["attempts"], 4)
        self.assertEqual(result["decoding"]["attempts"], 2)
        self.assertTrue(result["headers"]["sample_limit_reached"])

    def test_disabled_decodes_do_not_access_pixel_property(self):
        dataset = self.add()
        result = self.audit(self.changed(max_pixel_decodes=0))
        self.assertEqual(result["headers"]["status"], "PASS")
        self.assertEqual(result["decoding"]["status"], "NOT_ASSESSED")
        self.assertEqual(dataset.pixel_accesses, 0)

    def test_header_failure_message_and_path_never_leak(self):
        self.add()
        self.pydicom.dcmread = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("PRIVATE_HEADER_STRING " + str(self.base)))
        result = self.audit()
        self.assertEqual(result["headers"]["attempts"], 1)
        self.assertEqual(result["headers"]["failures"], 1)
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn("PRIVATE_HEADER_STRING", json.dumps(result))
        self.assertNotIn(str(self.base), json.dumps(result))
        self.assertEqual(result["decoding"]["attempts"], 0)

    def test_parser_warnings_and_logs_are_suppressed_and_restored(self):
        self.add()
        original = self.read
        logger = logging.getLogger("pydicom")
        before = logger.disabled
        def noisy(stream, **kwargs):
            warnings.warn("PRIVATE_WARNING_VALUE")
            logger.warning("PRIVATE_LOG_VALUE")
            return original(stream, **kwargs)
        self.pydicom.dcmread = noisy
        output = io.StringIO()
        with redirect_stderr(output):
            result = self.audit()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(logger.disabled, before)
        self.assertNotIn("PRIVATE_WARNING_VALUE", json.dumps(result))

    def test_dynamic_child_logger_diagnostics_are_suppressed_on_header_and_decode(self):
        self.add()
        original = self.read
        output = io.StringIO()
        child = None
        previous_global_disable = logging.root.manager.disable
        logging.disable(logging.WARNING)
        self.addCleanup(logging.disable, previous_global_disable)
        calls = []
        def noisy(stream, **kwargs):
            nonlocal child
            # A logger created after the audit starts with its own direct handler
            # must not escape suppression through propagation=False.
            if child is None:
                child = logging.getLogger("pydicom.pixels.decoders.synthetic_privacy_test")
                old_handlers, old_level, old_propagate = child.handlers[:], child.level, child.propagate
                self.addCleanup(setattr, child, "handlers", old_handlers)
                self.addCleanup(setattr, child, "level", old_level)
                self.addCleanup(setattr, child, "propagate", old_propagate)
                child.handlers = [logging.StreamHandler(output)]
                child.setLevel(logging.DEBUG)
                child.propagate = False
            calls.append(kwargs["stop_before_pixels"])
            child.critical("PRIVATE_CHILD_LOG_IDENTIFIER")
            warnings.warn("PRIVATE_CHILD_WARNING_IDENTIFIER")
            return original(stream, **kwargs)
        self.pydicom.dcmread = noisy
        with redirect_stderr(output):
            result = self.audit()
        self.assertEqual(calls, [True, False])
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(logging.root.manager.disable, logging.WARNING)
        self.assertNotIn("PRIVATE_CHILD_LOG_IDENTIFIER", json.dumps(result))
        self.assertNotIn("PRIVATE_CHILD_WARNING_IDENTIFIER", json.dumps(result))

    def test_logging_state_restored_after_unexpected_dependency_failure(self):
        previous = logging.root.manager.disable
        with patch("rsna_knee.dicom_audit._load_pydicom", side_effect=RuntimeError("synthetic dependency failure")):
            with self.assertRaises(RuntimeError):
                inspect_dicom(self.records, self.config)
        self.assertEqual(logging.root.manager.disable, previous)

    def test_missing_file_is_a_counted_safe_failure(self):
        self.add()
        self.records[0]["path"].unlink()
        result = self.audit()
        self.assertEqual(result["headers"]["failure_categories"], {"file_missing": 1})
        self.assertEqual(result["headers"]["attempts"], 1)

    def test_outside_base_is_never_opened(self):
        self.add()
        outside = self.base.parent / "outside.dcm"
        outside.write_bytes(b"outside")
        self.records[0]["path"] = outside
        result = self.audit()
        self.assertEqual(result["headers"]["failure_categories"], {"unsafe_path": 1})
        self.assertEqual(self.calls, [])

    def test_file_outside_record_series_is_rejected(self):
        self.add()
        self.records[0]["series_path"] = self.base / "another-series"
        result = self.audit()
        self.assertEqual(result["headers"]["failure_categories"], {"unsafe_path": 1})

    def test_permission_failure_is_not_raw(self):
        self.add()
        with patch("rsna_knee.dicom_audit._checked_path", side_effect=PermissionError("PRIVATE_PERMISSION")):
            result = self.audit()
        self.assertEqual(result["headers"]["failure_categories"], {"inaccessible": 1})
        self.assertNotIn("PRIVATE_PERMISSION", json.dumps(result))

    def test_read_byte_budget_is_enforced_inside_parser(self):
        self.add()
        def oversized(stream, **kwargs):
            stream.read(9)
        self.pydicom.dcmread = oversized
        result = self.audit(self.changed(max_header_bytes=8))
        self.assertEqual(result["headers"]["failure_categories"], {"read_byte_limit": 1})
        self.assertEqual(result["headers"]["bytes_read"], 0)
        self.assertEqual(result["status"], "BLOCKED")

    def test_seek_cannot_escape_header_budget(self):
        self.add()
        self.pydicom.dcmread = lambda stream, **kwargs: stream.seek(9)
        result = self.audit(self.changed(max_header_bytes=8))
        self.assertEqual(result["headers"]["failure_categories"], {"read_byte_limit": 1})

    def test_deflated_dataset_is_blocked_before_pydicom_inflation(self):
        self.add()
        syntax = b"1.2.840.10008.1.2.1.99\0"
        meta = struct.pack("<HH2sH", 2, 0x10, b"UI", len(syntax)) + syntax
        self.records[0]["path"].write_bytes(b"\0" * 128 + b"DICM" + meta + b"compressed-body-never-read")
        result = self.audit()
        self.assertEqual(self.calls, [])
        self.assertEqual(result["headers"]["failure_categories"], {"deflated_dataset_not_audited": 1})
        self.assertEqual(result["headers"]["status"], "BLOCKED")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["decoding"]["attempts"], 0)
        observed = result["transfer_syntaxes"]["observed"]["1.2.840.10008.1.2.1.99"]
        self.assertEqual(observed["file_meta_count"], 1)
        self.assertEqual(observed["header_count"], 0)
        self.assertEqual(observed["audit_policy_status"], "BLOCKED")
        self.assertEqual(observed["availability_status"], "NOT_ASSESSED")
        self.assertEqual(observed["sample_decode_status"], "NOT_ASSESSED")

    def test_elapsed_limit_stops_before_read(self):
        self.add()
        with patch("rsna_knee.dicom_audit.time.perf_counter", side_effect=[0, 91, 91]):
            result = self.audit()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["headers"]["attempts"], 0)
        self.assertTrue(result["time_limit_reached"])

    def test_elapsed_limit_inside_parser_is_recorded(self):
        self.add()
        with patch("rsna_knee.dicom_audit.time.perf_counter", side_effect=[0, 0, 91, 91]):
            result = self.audit()
        self.assertEqual(result["headers"]["failure_categories"], {"time_limit": 1})
        self.assertEqual(result["headers"]["attempts"], 1)
        self.assertTrue(result["time_limit_reached"])

    def test_manifest_mismatch_duplicate_sop_and_inconsistent_patient(self):
        self.add(1)
        self.add(2, StudyInstanceUID="1.2.999", SOPInstanceUID="1.2.300.1", PatientID="PRIVATE_PATIENT_SECOND")
        result = self.audit()
        counts = result["identifiers"]["counts"]
        self.assertEqual(counts["StudyInstanceUID_manifest_mismatch_count"], 1)
        self.assertEqual(counts["StudyInstanceUID_inconsistent_series_count"], 1)
        self.assertEqual(counts["PatientID_inconsistent_series_count"], 1)
        self.assertEqual(counts["duplicate_sop_instance_uid_count"], 1)
        self.assertEqual(result["identifiers"]["status"], "FAIL")
        self.assertNotIn("PRIVATE_PATIENT_SECOND", json.dumps(result))

    def test_missing_and_invalid_identifiers_are_not_emitted(self):
        self.add(StudyInstanceUID=None, SeriesInstanceUID="PRIVATE_IDENTIFIER", SOPInstanceUID="01.2", PatientID=None)
        result = self.audit()
        self.assertEqual(result["metadata"]["missing_field_counts"]["StudyInstanceUID"], 1)
        self.assertEqual(result["metadata"]["malformed_field_counts"]["SeriesInstanceUID"], 1)
        self.assertEqual(result["identifiers"]["status"], "FAIL")
        self.assertNotIn("PRIVATE_IDENTIFIER", json.dumps(result))

    def test_arbitrary_categorical_strings_are_sanitized(self):
        self.add(Modality="PRIVATE_MODALITY", ImageType=["PRIVATE_IMAGE_TYPE", "ORIGINAL"], PhotometricInterpretation="PRIVATE_PHOTOMETRIC")
        result = self.audit()
        text = json.dumps(result)
        self.assertNotIn("PRIVATE_MODALITY", text)
        self.assertNotIn("PRIVATE_IMAGE_TYPE", text)
        self.assertNotIn("PRIVATE_PHOTOMETRIC", text)
        self.assertIn("OTHER", text)

    def test_histogram_categories_are_bounded(self):
        for number in range(1, 36):
            self.add(number, Rows=number)
        result = self.audit(self.changed(max_pixel_decodes=0))
        histogram = result["metadata"]["distributions"]["dimensions"]
        self.assertEqual(len(histogram["values"]), 24)
        self.assertEqual(histogram["other_count"], 11)
        self.assertEqual(histogram["distinct_count"], 35)

    def test_position_order_uses_projection_not_thickness(self):
        self.add(1, ImagePositionPatient=[0, 0, 4], InstanceNumber=3)
        self.add(2, ImagePositionPatient=[0, 0, 0], InstanceNumber=1)
        self.add(3, ImagePositionPatient=[0, 0, 2], InstanceNumber=2)
        result = self.audit()
        geometry = result["geometry"]
        self.assertEqual(geometry["physical_order_supported_series_count"], 1)
        self.assertEqual(geometry["sampled_position_gap_statistics_mm"]["median"], 2)
        self.assertEqual(result["metadata"]["numeric_statistics"]["SliceThickness"]["median"], 77)

    def test_duplicate_positions_and_nonmonotonic_instance_order_flagged(self):
        self.add(1, ImagePositionPatient=[0, 0, 0])
        self.add(2, ImagePositionPatient=[0, 0, 2])
        self.add(3, ImagePositionPatient=[0, 0, 0])
        result = self.audit()
        self.assertEqual(result["geometry"]["duplicate_position_count"], 1)
        self.assertEqual(result["geometry"]["instance_order_direction_change_series_count"], 1)
        self.assertEqual(result["geometry"]["status"], "FAIL")

    def test_orientation_inconsistency_prevents_physical_order_claim(self):
        self.add(1)
        self.add(2, ImageOrientationPatient=[0, 1, 0, 0, 0, 1])
        result = self.audit()
        self.assertEqual(result["geometry"]["orientation_inconsistent_series_count"], 1)
        self.assertEqual(result["geometry"]["physical_order_supported_series_count"], 0)

    def test_position_absence_does_not_use_slice_thickness(self):
        self.add(1, ImagePositionPatient=None)
        self.add(2, ImagePositionPatient=None)
        result = self.audit()
        self.assertEqual(result["geometry"]["status"], "NOT_ASSESSED")
        self.assertIsNone(result["geometry"]["sampled_position_gap_statistics_mm"]["median"])

    def test_header_success_does_not_imply_decode_success(self):
        self.add(decode_error=RuntimeError("PRIVATE_DECODER_EXCEPTION"))
        result = self.audit()
        self.assertEqual(result["headers"]["status"], "PASS")
        self.assertEqual(result["decoding"]["attempts"], 1)
        self.assertEqual(result["decoding"]["failures"], 1)
        self.assertEqual(result["decoding"]["status"], "FAIL")
        self.assertNotIn("PRIVATE_DECODER_EXCEPTION", json.dumps(result))
        observed = result["transfer_syntaxes"]["observed"]["1.2.840.10008.1.2.1"]
        self.assertEqual(observed["decode_failures"], 1)
        self.assertEqual(observed["sample_decode_status"], "FAIL")

    def test_missing_decoder_plugin_is_an_explicit_failure_category(self):
        self.add(decode_error=ImportError("PRIVATE_PLUGIN_REQUIREMENT"))
        result = self.audit()
        self.assertEqual(result["decoding"]["failure_categories"], {"decoder_dependency_unavailable": 1})
        self.assertEqual(result["decoding"]["status"], "BLOCKED")
        self.assertNotIn("PRIVATE_PLUGIN_REQUIREMENT", json.dumps(result))

    def test_unknown_codec_introspection_remains_unassessed(self):
        self.add()
        self.pydicom.pixels.get_decoder = lambda syntax: (_ for _ in ()).throw(NotImplementedError("PRIVATE_CODEC"))
        result = self.audit()
        observed = result["transfer_syntaxes"]["observed"]["1.2.840.10008.1.2.1"]
        self.assertEqual(observed["availability_status"], "NOT_ASSESSED")
        self.assertEqual(observed["sample_decode_status"], "PASS")

    def test_declared_element_and_uncompressed_byte_limits_prevent_decode(self):
        dataset = self.add(Rows=100000, Columns=100000)
        result = self.audit()
        self.assertEqual(result["decoding"]["attempts"], 0)
        self.assertEqual(result["decoding"]["skipped_resource_limits"], 1)
        self.assertEqual(dataset.pixel_accesses, 0)
        self.datasets[1].Rows = 2
        self.datasets[1].Columns = 3
        self.datasets[1].BitsAllocated = 64
        result = self.audit(self.changed(max_dicom_file_bytes=32))
        self.assertEqual(result["decoding"]["skip_categories"], {"declared_pixel_budget": 1})

    def test_file_byte_budget_prevents_full_read(self):
        dataset = self.add()
        result = self.audit(self.changed(max_dicom_file_bytes=64))
        self.assertEqual(result["headers"]["successes"], 1)
        self.assertEqual(result["decoding"]["skip_categories"], {"file_byte_budget": 1})
        self.assertEqual(result["decoding"]["attempts"], 0)
        self.assertEqual(dataset.pixel_accesses, 0)

    def test_invalid_pixel_metadata_prevents_decode(self):
        dataset = self.add(BitsAllocated=3)
        result = self.audit()
        self.assertEqual(result["decoding"]["skip_categories"], {"invalid_pixel_metadata": 1})
        self.assertEqual(dataset.pixel_accesses, 0)

    def test_declared_frames_and_components_are_budgeted(self):
        dataset = self.add(NumberOfFrames=1000, SamplesPerPixel=16)
        result = self.audit(self.changed(max_pixel_elements=16))
        self.assertEqual(result["decoding"]["skipped_resource_limits"], 1)
        self.assertEqual(dataset.pixel_accesses, 0)

    def test_shape_nonfinite_and_invalid_type_fail_sanity(self):
        cases = ((FakeArray((3, 2)), "array_shape_or_type"),
                 (FakeArray(minimum=float("nan")), "nonfinite_pixels"),
                 (FakeArray(kind="O"), "array_shape_or_type"))
        self.add()
        for array, category in cases:
            with self.subTest(category=category):
                self.datasets[1].array = array
                result = self.audit()
                self.assertEqual(result["decoding"]["failure_categories"], {category: 1})
                self.assertEqual(result["decoding"]["attempts"], 1)
                self.assertEqual(result["decoding"]["failures"], 1)

    def test_zero_constant_arrays_recorded_without_pixels(self):
        self.add(array=FakeArray(minimum=0, maximum=0))
        result = self.audit()
        self.assertEqual(result["decoding"]["constant_arrays"], 1)
        self.assertEqual(result["decoding"]["all_zero_arrays"], 1)

    def test_mutated_full_read_metadata_is_checked_before_pixel_access(self):
        header = self.add()
        changed = FakeDataset(**(vars(header) | {"Rows": 1000000}))
        old_read = self.read
        def reader(stream, *, stop_before_pixels, force):
            dataset = old_read(stream, stop_before_pixels=stop_before_pixels, force=force)
            return dataset if stop_before_pixels else changed
        self.pydicom.dcmread = reader
        result = self.audit()
        self.assertEqual(result["decoding"]["failures"], 1)
        self.assertEqual(changed.pixel_accesses, 0)

    def test_full_read_open_failure_is_counted_as_decode_attempt(self):
        self.add()
        original = Path.open
        attempts = 0
        def opening(path, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise PermissionError("PRIVATE_FULL_READ")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", opening):
            result = self.audit()
        self.assertEqual(result["decoding"]["attempts"], 1)
        self.assertEqual(result["decoding"]["failures"], 1)
        self.assertEqual(result["decoding"]["failure_categories"], {"inaccessible": 1})

    def test_bounded_stream_cumulative_reads_and_zero_length(self):
        self.add()
        with self.records[0]["path"].open("rb") as stream:
            bounded = _BoundedStream(stream, 3, float("inf"))
            self.assertEqual(bounded.read(0), b"")
            bounded.read(2)
            bounded.seek(0)
            bounded.read(1)
            with self.assertRaises(_BudgetExceeded):
                bounded.read(1)
            self.assertEqual(bounded.bytes_read, 3)

    @unittest.skipUnless(importlib.util.find_spec("pydicom") is not None, "pydicom not installed; no installation requested")
    def test_installed_pydicom_minimal_synthetic_roundtrip(self):
        import pydicom
        from pydicom.dataset import Dataset, FileDataset
        self.add()
        metadata = Dataset()
        metadata.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
        metadata.MediaStorageSOPInstanceUID = "1.2.300.1"
        metadata.TransferSyntaxUID = "1.2.840.10008.1.2.1"
        dataset = FileDataset(str(self.records[0]["path"]), {}, file_meta=metadata, preamble=b"\0" * 128)
        for name, value in vars(self.datasets[1]).items():
            if name not in ("array", "decode_error", "pixel_accesses", "file_meta"):
                setattr(dataset, name, value)
        dataset.SOPClassUID = metadata.MediaStorageSOPClassUID
        dataset.PixelData = b"\0\0" * 6
        dataset.save_as(self.records[0]["path"], write_like_original=False)
        result = inspect_dicom(self.records, self.config)
        self.assertEqual(result["headers"]["successes"], 1)
        self.assertEqual(result["decoding"]["successes"], 1)
