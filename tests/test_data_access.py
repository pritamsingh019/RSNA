"""Access-check regressions using only tiny, temporary synthetic inputs."""

from __future__ import annotations

import hashlib
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rsna_knee.data_access import (
    IMAGE_DIRECTORY_CANDIDATES,
    MANIFEST_NAMES,
    discover_inputs,
    run_access_check,
    write_report,
)


class DataAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rsna-access-test-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.inputs = self.workspace / "input"
        self.outputs = self.workspace / "output"
        self.inputs.mkdir()

    def make_manifests(self, root, names=MANIFEST_NAMES):
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            # These files establish names and metadata only, not dataset validity.
            (root / name).write_bytes(b"synthetic_fixture,value\nexample,1\n")
        return root

    def source_hashes(self):
        return {
            str(path.relative_to(self.inputs)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.inputs.rglob("*"))
            if path.is_file()
        }

    def test_missing_input_base(self):
        result = discover_inputs(self.inputs / "absent")
        self.assertEqual(result["status"], "INPUT_BASE_MISSING")
        self.assertEqual(result["directories_checked"], 0)
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["selected_root"])

    def test_existing_empty_input_tree(self):
        result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "INPUTS_NOT_LOCATED")
        self.assertEqual(result["directories_checked"], 1)
        self.assertTrue(result["search_complete_within_limits"])
        self.assertIsNone(result["selected_root"])

    def test_each_missing_manifest_prevents_selection(self):
        for missing in MANIFEST_NAMES:
            with self.subTest(missing=missing):
                root = self.inputs / missing.removesuffix(".csv")
                self.make_manifests(root, [name for name in MANIFEST_NAMES if name != missing])
                result = discover_inputs(self.inputs, explicit_root=root)
                self.assertEqual(result["status"], "INCOMPLETE_MANIFESTS")
                self.assertIsNone(result["selected_root"])
                self.assertEqual(len(result["candidates"]), 1)
                self.assertEqual(result["candidates"][0]["missing_manifests"], [missing])

    def test_all_five_manifest_names_located_without_audit_pass(self):
        self.make_manifests(self.inputs)
        result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "MANIFESTS_LOCATED_AUDIT_PENDING")
        self.assertEqual(result["selected_root"], str(self.inputs.resolve()))
        candidate = result["candidates"][0]
        self.assertEqual({item["name"] for item in candidate["files"]}, set(MANIFEST_NAMES))
        self.assertEqual(candidate["missing_manifests"], [])
        self.assertIsNone(candidate["dicom_count"])
        self.assertFalse(result["csv_contents_inspected"])
        self.assertFalse(result["dicom_contents_inspected"])
        self.assertFalse(result["dataset_identity_verified"])
        for item in candidate["files"]:
            self.assertGreater(item["size_bytes"], 0)
            self.assertIsNone(item["sha256"])
            self.assertEqual(item["schema_status"], "NOT_INSPECTED")

    def test_kaggle_layout_at_depth_two(self):
        root = self.make_manifests(self.inputs / "competition" / "dataset")
        result = discover_inputs(self.inputs, max_depth=2)
        self.assertEqual(result["selected_root"], str(root.resolve()))
        self.assertEqual(result["directories_checked"], 3)
        shallower = discover_inputs(self.inputs, max_depth=1)
        self.assertIsNone(shallower["selected_root"])
        self.assertEqual(shallower["candidates"], [])

    def test_ambiguous_roots_require_explicit_selection(self):
        first = self.make_manifests(self.inputs / "first")
        self.make_manifests(self.inputs / "second")
        result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "AMBIGUOUS_INPUT_ROOTS")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIsNone(result["selected_root"])
        selected = discover_inputs(self.inputs, explicit_root=first)
        self.assertEqual(selected["selected_root"], str(first.resolve()))
        self.assertEqual(selected["directories_checked"], 1)
        self.assertEqual(len(selected["candidates"]), 1)

    def test_complete_root_and_nested_complete_root_are_ambiguous(self):
        self.make_manifests(self.inputs)
        nested = self.make_manifests(self.inputs / "nested")
        result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "AMBIGUOUS_INPUT_ROOTS")
        self.assertIsNone(result["selected_root"])
        self.assertEqual(
            {candidate["root"] for candidate in result["candidates"]},
            {str(self.inputs.resolve()), str(nested.resolve())},
        )

    def test_explicit_root_cannot_traverse_outside_input_base(self):
        outside = self.make_manifests(self.workspace / "outside")
        for root in (outside, self.inputs / ".." / "outside"):
            with self.subTest(root=root), self.assertRaises(ValueError):
                discover_inputs(self.inputs, explicit_root=root)

    def test_directory_limit_prevents_false_unique_selection(self):
        self.make_manifests(self.inputs / "a_complete")
        self.make_manifests(self.inputs / "z_also_complete")
        result = discover_inputs(self.inputs, max_directories=2)
        self.assertEqual(result["directories_checked"], 2)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertFalse(result["search_complete_within_limits"])
        self.assertEqual(result["status"], "DISCOVERY_INCOMPLETE")
        self.assertIsNone(result["selected_root"])

    def test_total_entry_limit_marks_discovery_incomplete(self):
        for name in ("first", "second", "third"):
            (self.inputs / name).mkdir()
        result = discover_inputs(self.inputs, max_entries=2)
        self.assertFalse(result["search_complete_within_limits"])
        self.assertEqual(result["status"], "DISCOVERY_INCOMPLETE")
        self.assertIsNone(result["selected_root"])

    def test_permission_error_is_recorded_as_incomplete_discovery(self):
        self.make_manifests(self.inputs)
        blocked = self.inputs / "train.csv"
        original_lstat = Path.lstat
        private_error_message = "Synthetic error detail must not enter the report"

        def deny_fixture_lstat(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError(errno.EACCES, private_error_message)
            return original_lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", deny_fixture_lstat):
            result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "DISCOVERY_INCOMPLETE")
        self.assertIsNone(result["selected_root"])
        self.assertFalse(result["search_complete_within_limits"])
        self.assertTrue(result["search_errors"])
        self.assertTrue(any(
            issue["path"] == str(blocked)
            and issue["type"] == "PermissionError"
            and issue["errno"] == errno.EACCES
            for issue in result["search_errors"]
        ))
        self.assertNotIn(private_error_message, json.dumps(result))

    def test_discovery_never_follows_directory_symlink_outside_inputs(self):
        external = self.make_manifests(self.workspace / "external")
        link = self.inputs / "external_link"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlinks are unavailable: {type(error).__name__}")
        original_scandir = os.scandir

        def guarded_scandir(path):
            self.assertNotEqual(path, link, "Discovery entered a directory symlink")
            self.assertNotEqual(path, external, "Discovery entered an external directory")
            return original_scandir(path)

        with patch("rsna_knee.data_access.os.scandir", guarded_scandir):
            result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "INPUTS_NOT_LOCATED")
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["selected_root"])
        self.assertIn(str(link), result["skipped_symlinks"])

    def test_known_image_directories_are_never_entered(self):
        # A partial parent must still search siblings, while skipping image trees.
        self.make_manifests(self.inputs, ("train.csv",))
        images = {self.inputs / name for name in IMAGE_DIRECTORY_CANDIDATES}
        for image_root in images:
            self.make_manifests(image_root / "uid_fixture")
        original_scandir = os.scandir

        def guarded_scandir(path):
            self.assertNotIn(path, images, "Discovery entered an image directory")
            return original_scandir(path)

        with patch("rsna_knee.data_access.os.scandir", guarded_scandir):
            result = discover_inputs(self.inputs, max_depth=4)
        self.assertEqual(result["directories_checked"], 1)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["status"], "INCOMPLETE_MANIFESTS")
        self.assertEqual(
            set(result["candidates"][0]["image_directory_candidates"]),
            set(IMAGE_DIRECTORY_CANDIDATES),
        )

    def test_simulated_junction_is_excluded_without_windows_privileges(self):
        junction = self.make_manifests(self.inputs / "junction_fixture")
        with patch.object(Path, "is_junction", lambda path: path == junction, create=True):
            result = discover_inputs(self.inputs)
        self.assertEqual(result["candidates"], [])
        self.assertIn(str(junction), result["skipped_symlinks"])

    def test_resolved_child_outside_input_tree_is_reported(self):
        child = self.make_manifests(self.inputs / "redirected_fixture")
        external = self.workspace / "outside"
        original_resolve = Path.resolve

        def redirected_resolve(path, *args, **kwargs):
            return external if path == child else original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", redirected_resolve):
            result = discover_inputs(self.inputs)
        self.assertEqual(result["status"], "DISCOVERY_INCOMPLETE")
        self.assertIsNone(result["selected_root"])
        self.assertTrue(any(item["type"] == "PATH_OUTSIDE_INPUT_BASE" for item in result["search_errors"]))

    def test_report_rejects_output_inside_input_tree(self):
        destinations = (
            self.inputs,
            self.inputs / "reports",
            self.inputs / "unused" / ".." / "reports",
        )
        before = self.source_hashes()
        for destination in destinations:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                write_report({"synthetic": True}, destination, input_base=self.inputs)
        self.assertEqual(self.source_hashes(), before)
        self.assertFalse((self.inputs / "reports").exists())

    def test_access_check_preserves_all_source_hashes_and_writes_valid_report(self):
        self.make_manifests(self.inputs)
        image = self.inputs / "train_series" / "synthetic_study" / "synthetic_series"
        image.mkdir(parents=True)
        (image / "synthetic.dcm").write_bytes(b"synthetic fixture, not a DICOM\x00")
        before = self.source_hashes()
        report, path = run_access_check(self.inputs, self.outputs, source_sha256="fixture_hash")
        self.assertEqual(self.source_hashes(), before)
        self.assertTrue(path.is_relative_to(self.outputs))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
        self.assertEqual(report["source_sha256"], "fixture_hash")
        self.assertFalse(report["phase1_pass"])
        self.assertFalse(report["training_started"])
        self.assertEqual(report["phase_b_audit_status"], "NOT_RUN")
        self.assertIsNone(report["model_metrics"])
        self.assertEqual(report["discovery"]["image_inventory_status"], "NOT_AUDITED")

    def test_report_paths_are_unique_and_existing_artifacts_are_preserved(self):
        first = write_report({"run": 1}, self.outputs, input_base=self.inputs)
        original = first.read_bytes()
        second = write_report({"run": 2}, self.outputs, input_base=self.inputs)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(json.loads(second.read_text(encoding="utf-8")), {"run": 2})
        self.assertEqual(set(self.outputs.iterdir()), {first, second})

    def test_invalid_discovery_limits_rejected(self):
        for limits in (
            {"max_depth": -1}, {"max_directories": 0}, {"max_directories": -1},
            {"max_entries": 0}, {"max_entries": -1},
        ):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                discover_inputs(self.inputs, **limits)


if __name__ == "__main__":
    unittest.main()
