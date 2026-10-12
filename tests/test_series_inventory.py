import errno
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rsna_knee.inputs import InputError
from rsna_knee.phase_b_config import PhaseBConfig
from rsna_knee.series_inventory import inventory_series, sample_series_files


def row(study, series, plane="sagittal", fluid="1", fat="0"):
    return {"StudyInstanceUID": study, "SeriesInstanceUID": series,
            "Anatomical_Plane": plane, "Fluid_Sensitive": fluid, "Fat_Suppression": fat}


class SeriesInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name) / "inputs"
        self.base.mkdir()
        self.config = PhaseBConfig(input_base=str(self.base))

    def directory(self, *parts):
        path = self.base.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_learns_uid_layout_prunes_dicom_contents_and_redacts_paths(self):
        first = row("synthetic-study-001", "synthetic-series-001")
        second = row("synthetic-study-002", "synthetic-series-002")
        self.directory("train_series", first["StudyInstanceUID"], first["SeriesInstanceUID"])
        self.directory("test_images", second["SeriesInstanceUID"])
        original = os.scandir
        visited = []
        def guarded(path):
            visited.append(Path(path))
            return original(path)
        with patch("rsna_knee.series_inventory.os.scandir", guarded):
            report, records = inventory_series(self.base, [first], [second], self.config)
        self.assertTrue(report["scan_complete"])
        self.assertEqual(len(records), 2)
        self.assertEqual(report["matched_series_directories_pruned"], 2)
        self.assertEqual(report["split_statistics"]["train"]["missing_series"], 0)
        self.assertEqual(report["split_statistics"]["test"]["resolved_series"], 1)
        patterns = {item["pattern"] for item in report["path_conventions"]}
        self.assertIn("train_series/<StudyInstanceUID>/<SeriesInstanceUID>", patterns)
        self.assertIn("test_images/<SeriesInstanceUID>", patterns)
        serialized = json.dumps(report)
        self.assertNotIn(str(self.base), serialized)
        for identifier in (first["StudyInstanceUID"], first["SeriesInstanceUID"], second["SeriesInstanceUID"]):
            self.assertNotIn(identifier, serialized)
        self.assertIsNone(report["dicom_file_count"])

    def test_missing_and_ambiguous_are_distinct(self):
        missing = row("study-missing", "series-missing")
        duplicate = row("study-duplicate", "series-duplicate")
        self.directory("first", duplicate["SeriesInstanceUID"])
        self.directory("second", duplicate["SeriesInstanceUID"])
        report, records = inventory_series(self.base, [missing, duplicate], [], self.config)
        self.assertTrue(report["scan_complete"])
        self.assertEqual(records, [])
        stats = report["split_statistics"]["train"]
        self.assertEqual(stats["missing_series"], 1)
        self.assertEqual(stats["ambiguous_series"], 1)
        self.assertEqual(stats["resolved_series"], 0)

    def test_ancestry_must_agree_when_identifiers_or_split_are_observed(self):
        first, second = row("study-a", "series-a"), row("study-b", "series-b")
        self.directory("test_series", first["SeriesInstanceUID"])
        self.directory(second["StudyInstanceUID"], first["SeriesInstanceUID"])
        report, records = inventory_series(self.base, [first, second], [], self.config)
        self.assertEqual(report["ancestry_mismatches"], 2)
        self.assertEqual(records, [])

    def test_entry_directory_depth_and_time_limits_are_honest(self):
        expected = row("study", "series")
        self.directory("outer", "inner", expected["SeriesInstanceUID"])
        for overrides, reason in (({"max_inventory_entries": 1}, "entries"),
                                  ({"max_inventory_directories": 1}, "directories"),
                                  ({"max_inventory_depth": 1}, "depth")):
            with self.subTest(overrides=overrides):
                report, _ = inventory_series(self.base, [expected], [], self.config.with_overrides(**overrides))
                self.assertFalse(report["scan_complete"])
                self.assertIn(reason, report["limit_reasons"])
                self.assertIsNone(report["split_statistics"]["train"]["missing_series"])
                self.assertEqual(report["split_statistics"]["train"]["unlocated_not_proven_missing"], 1)
                self.assertLessEqual(report["entries_checked"], self.config.with_overrides(**overrides).max_inventory_entries)
        with patch("rsna_knee.series_inventory.time.monotonic", side_effect=[0, 100000, 100001]):
            report, _ = inventory_series(self.base, [expected], [], self.config)
        self.assertIn("time", report["limit_reasons"])

    def test_containment_and_validated_config_are_enforced(self):
        report, records = inventory_series(self.base.parent, [], [], self.config)
        self.assertEqual(report["status"], "SERIES_INVENTORY_INVALID")
        self.assertEqual(records, [])
        with self.assertRaises(TypeError):
            inventory_series(self.base, [], [], {})
        with self.assertRaises(InputError):
            sample_series_files([{"split": "train", "row": row("study", "series"), "path": self.base.parent}], self.config)

    def test_permission_failures_do_not_leak_private_error_text(self):
        expected = row("study-private", "series-private")
        self.directory(expected["SeriesInstanceUID"])
        original = os.scandir
        def deny(path):
            if Path(path).name == expected["SeriesInstanceUID"]:
                raise PermissionError(errno.EACCES, "private study text")
            return original(path)
        with patch("rsna_knee.series_inventory.os.scandir", deny):
            report, records = inventory_series(self.base, [expected], [], self.config)
        self.assertFalse(report["scan_complete"])
        self.assertEqual(records, [])
        self.assertEqual(report["split_statistics"]["train"]["inaccessible_series"], 1)
        self.assertNotIn("private", json.dumps(report))

    def test_links_reparse_points_and_protected_directories_are_not_entered(self):
        expected = row("study", "series")
        link = self.directory("simulated_junction")
        self.directory(".git", expected["SeriesInstanceUID"])
        original = Path.lstat
        def simulated(path, *args, **kwargs):
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", simulated):
            report, records = inventory_series(self.base, [expected], [], self.config)
        self.assertEqual(records, [])
        self.assertEqual(report["skipped_links"], 1)
        self.assertEqual(report["skipped_protected"], 1)
        self.assertFalse(report["scan_complete"])

    def test_complete_inventory_is_deterministic(self):
        expected = [row("study-b", "series-b"), row("study-a", "series-a")]
        for item in expected:
            self.directory("train_series", item["SeriesInstanceUID"])
        first = inventory_series(self.base, expected, [], self.config)
        second = inventory_series(self.base, list(reversed(expected)), [], self.config)
        first[0].pop("elapsed_seconds")
        second[0].pop("elapsed_seconds")
        self.assertEqual(first, second)


class SeriesSamplingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name) / "inputs"
        self.base.mkdir()
        self.config = PhaseBConfig(input_base=str(self.base))

    def record(self, index, split="train", plane="sagittal", fluid="1", fat="0", files=3):
        item = row(f"study-{index}", f"series-{index}", plane, fluid, fat)
        path = self.base / item["SeriesInstanceUID"]
        path.mkdir()
        for number in range(files):
            (path / f"{number:03}.DCM").write_bytes(b"synthetic-not-decoded")
        return {"split": split, "row": item, "path": path}

    def test_stratified_deterministic_selection_and_round_robin_header_budget(self):
        records = [self.record(3), self.record(1), self.record(2, "test"), self.record(4, plane="coronal")]
        config = self.config.with_overrides(max_sample_series=3, max_files_per_series=2, max_header_reads=3)
        first, files = sample_series_files(records, config)
        second, reversed_files = sample_series_files(list(reversed(records)), config)
        first.pop("elapsed_seconds")
        second.pop("elapsed_seconds")
        self.assertEqual(first, second)
        self.assertEqual(files, reversed_files)
        self.assertEqual(first["strata_count"], 3)
        self.assertEqual(first["strata_selected"], 3)
        self.assertEqual(first["selected_files"], 3)
        self.assertEqual(first["split_selected_series"], {"train": 2, "test": 1})
        self.assertEqual(first["split_selected_files"], {"train": 2, "test": 1})
        self.assertEqual(len({item["series_path"] for item in files}), 3)
        self.assertTrue(first["header_selection_capped"])
        self.assertTrue(first["scan_complete"])
        self.assertNotIn("study-", json.dumps(first))
        self.assertNotIn("series-", json.dumps(first))
        self.assertNotIn(str(self.base), json.dumps(first))
        self.assertFalse(first["dicom_contents_read"])

    def test_empty_unexpected_entries_and_dicom_count_are_sample_only(self):
        empty, full = self.record(1, files=0), self.record(2, files=2)
        (full["path"] / "notes.txt").write_text("synthetic", encoding="utf-8")
        (full["path"] / "subdirectory").mkdir()
        report, files = sample_series_files([empty, full], self.config)
        self.assertEqual(report["empty_series"], 1)
        self.assertEqual(report["unexpected_regular_files"], 1)
        self.assertEqual(report["unexpected_subdirectories"], 1)
        self.assertEqual(report["candidate_dicom_files"], 2)
        self.assertEqual(report["series_with_no_candidate_dicom_filenames_in_immediate_entries"], 1)
        self.assertEqual(report["extension_counts"], {"dcm": 2, "dicom": 0, "other": 1})
        self.assertGreaterEqual(report["elapsed_seconds"], 0)
        self.assertEqual(len(files), 2)
        self.assertIsNone(report["dicom_file_count"])
        self.assertTrue(all(item["file_count_exact"] for item in report["representative_file_counts"]))

    def test_dicom_extension_is_case_insensitive_and_counts_are_privacy_safe(self):
        record = self.record(1, files=0)
        (record["path"] / "private-identifier.DiCoM").write_bytes(b"synthetic")
        (record["path"] / "private-identifier.unfamiliar").write_bytes(b"synthetic")
        report, files = sample_series_files([record], self.config)
        self.assertEqual(report["extension_counts"], {"dcm": 0, "dicom": 1, "other": 1})
        self.assertEqual(len(files), 1)
        self.assertNotIn("private-identifier", json.dumps(report))
        self.assertNotIn("unfamiliar", json.dumps(report))

    def test_entry_cap_is_bounded_and_discloses_nondeterministic_partial_subset(self):
        record = self.record(1, files=5)
        config = self.config.with_overrides(max_sample_file_entries=2)
        report, files = sample_series_files([record], config)
        self.assertEqual(report["entries_checked"], 2)
        self.assertFalse(report["scan_complete"])
        self.assertFalse(report["reproducible_selection"])
        self.assertIn("per_series_entries", report["limit_reasons"])
        self.assertFalse(report["representative_file_counts"][0]["file_count_exact"])
        self.assertLessEqual(len(files), 2)

    def test_total_cap_and_inaccessible_series_are_explicit(self):
        records = [self.record(1), self.record(2)]
        config = self.config.with_overrides(max_sample_total_entries=1)
        report, _ = sample_series_files(records, config)
        self.assertLessEqual(report["entries_checked"], 1)
        self.assertFalse(report["scan_complete"])
        self.assertIn("total_entries", report["limit_reasons"])
        with patch("rsna_knee.series_inventory.os.scandir", side_effect=PermissionError(errno.EACCES, "sensitive path")):
            report, files = sample_series_files(records, self.config)
        self.assertEqual(report["inaccessible_series"], 2)
        self.assertEqual(files, [])
        self.assertNotIn("sensitive", json.dumps(report))

    def test_sample_skips_protected_files_and_reparse_entries(self):
        record = self.record(1, files=0)
        link = record["path"] / "fake.dcm"
        link.write_bytes(b"synthetic")
        (record["path"] / ".env").write_text("synthetic", encoding="utf-8")
        original = Path.lstat
        def simulated(path, *args, **kwargs):
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", simulated):
            report, files = sample_series_files([record], self.config)
        self.assertEqual(report["skipped_links"], 1)
        self.assertEqual(report["skipped_protected"], 1)
        self.assertFalse(report["scan_complete"])
        self.assertEqual(files, [])
