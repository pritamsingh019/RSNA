import errno
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rsna_knee.config import AuditConfig
from rsna_knee.inputs import InputError, MANIFEST_NAMES, discover_inputs, read_csv_manifest, safe_absolute


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name) / "input"
        self.base.mkdir()
        self.config = AuditConfig(input_base=str(self.base))

    def manifests(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        for name in MANIFEST_NAMES:
            (directory / name).write_text("synthetic\n001\n", encoding="utf-8")

    def test_missing_and_empty_are_not_validated_datasets(self):
        missing = discover_inputs(self.config.with_overrides(input_base=str(self.base / "absent")))
        self.assertEqual(missing["status"], "INPUT_BASE_MISSING")
        empty = discover_inputs(self.config)
        self.assertEqual(empty["status"], "INPUTS_NOT_LOCATED")
        self.assertFalse(empty["dataset_identity_verified"])

    def test_partial_manifest_blocks_selection(self):
        (self.base / "train.csv").write_text("synthetic", encoding="utf-8")
        result = discover_inputs(self.config)
        self.assertEqual(result["status"], "INCOMPLETE_MANIFESTS")
        self.assertIsNone(result["selected_root"])
        self.assertEqual(len(result["candidates"][0]["missing"]), 4)

    def test_two_level_layout_discovery(self):
        root = self.base / "competitions" / "synthetic"
        self.manifests(root)
        result = discover_inputs(self.config)
        self.assertEqual(result["status"], "MANIFESTS_LOCATED")
        self.assertEqual(result["selected_root"], str(root))
        self.assertFalse(result["csv_contents_read"])
        self.assertFalse(result["dicom_contents_read"])

    def test_ambiguous_nested_roots_require_explicit_selection(self):
        self.manifests(self.base)
        nested = self.base / "nested"
        self.manifests(nested)
        result = discover_inputs(self.config)
        self.assertEqual(result["status"], "AMBIGUOUS_INPUT_ROOTS")
        self.assertIsNone(result["selected_root"])
        explicit = discover_inputs(self.config.with_overrides(data_root=str(nested)))
        self.assertEqual(explicit["selected_root"], str(nested))

    def test_outside_root_and_protected_paths_rejected(self):
        outside = self.base.parent / "outside"
        outside.mkdir()
        result = discover_inputs(self.config.with_overrides(data_root=str(outside)))
        self.assertEqual(result["status"], "INPUT_PATH_INVALID")
        with self.assertRaises(InputError):
            safe_absolute(self.base / ".git" / "config")

    def test_directory_and_entry_limits_prevent_false_unique_result(self):
        self.manifests(self.base / "first")
        self.manifests(self.base / "second")
        for changed in ({"max_directories": 2}, {"max_entries": 1}):
            result = discover_inputs(self.config.with_overrides(**changed))
            self.assertEqual(result["status"], "DISCOVERY_INCOMPLETE")
            self.assertIsNone(result["selected_root"])

    def test_image_directories_are_not_entered(self):
        self.manifests(self.base / "train_series" / "synthetic_uid")
        original = os.scandir
        def guarded(path):
            self.assertNotIn("train_series", Path(path).parts)
            return original(path)
        with patch("rsna_knee.inputs.os.scandir", guarded):
            result = discover_inputs(self.config)
        self.assertEqual(result["directories_checked"], 1)
        self.assertEqual(result["candidates"], [])

    def test_permission_failures_are_explicit(self):
        with patch("rsna_knee.inputs.os.scandir", side_effect=PermissionError(errno.EACCES, "Synthetic private detail")):
            result = discover_inputs(self.config)
        self.assertEqual(result["status"], "DISCOVERY_INCOMPLETE")
        self.assertEqual(result["error_count"], 1)
        self.assertNotIn("Synthetic private detail", str(result))

    def test_simulated_reparse_directory_is_not_followed(self):
        child = self.base / "simulated_junction"
        self.manifests(child)
        original = Path.lstat
        def simulated(path, *args, **kwargs):
            if path == child:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", simulated):
            result = discover_inputs(self.config)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["skipped_link_count"], 1)

    def test_csv_preserves_ids_and_utf8_report(self):
        path = self.base / "train.csv"
        raw = "StudyInstanceUID,Report\n001,正常\n".encode("utf-8")
        path.write_bytes(raw)
        columns, rows, digest = read_csv_manifest(path, self.config)
        self.assertEqual(columns, ["StudyInstanceUID", "Report"])
        self.assertEqual(rows[0], {"StudyInstanceUID": "001", "Report": "正常"})
        self.assertEqual(len(digest), 64)
        self.assertEqual(path.read_bytes(), raw)

    def test_csv_size_and_row_caps_are_fail_closed(self):
        path = self.base / "train.csv"
        path.write_text("id\n001\n002\n", encoding="utf-8")
        for changed in ({"max_file_bytes": 3}, {"max_csv_rows": 1}):
            with self.assertRaises(InputError):
                read_csv_manifest(path, self.config.with_overrides(**changed))

    def test_duplicate_headers_malformed_width_and_quotes_fail(self):
        path = self.base / "train.csv"
        for text in ("id,id\na,b\n", "id,label\na\n", "id\na,b\n", 'id\n"unclosed'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(InputError):
                read_csv_manifest(path, self.config)
