import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rsna_knee.audit import run_preflight, write_report
from rsna_knee.config import AuditConfig
from rsna_knee.contracts import TARGETS
from rsna_knee.inputs import InputError


ROOT = Path(__file__).resolve().parents[1]


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.source, self.output = base / "input", base / "output"
        self.source.mkdir()
        self.config = AuditConfig(input_base=str(self.source), output_dir=str(self.output), inspect_csv=True)

    def write_csv(self, name, columns, rows):
        with (self.source / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def fixture(self, train_id="001", test_id="002", label=""):
        train = {"StudyInstanceUID": train_id, "Report": "SYNTHETIC PRIVATE REPORT", **{target: label for target in TARGETS}}
        self.write_csv("train.csv", ["StudyInstanceUID", "Report", *TARGETS], [train])
        self.write_csv("test.csv", ["StudyInstanceUID"], [{"StudyInstanceUID": test_id}])
        for name, study, series in (("train_series.csv", train_id, "train.series"), ("test_series.csv", test_id, "test.series")):
            row = {"StudyInstanceUID": study, "SeriesInstanceUID": series, "Anatomical_Plane": "Synthetic", "Fluid_Sensitive": "", "Fat_Suppression": "1"}
            self.write_csv(name, list(row), [row])
        self.write_csv("sample_submission.csv", ["StudyInstanceUID", *TARGETS], [{"StudyInstanceUID": test_id, **{target: 0.5 for target in TARGETS}}])

    def hashes(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.source.iterdir() if path.is_file()}

    def test_valid_csv_contracts_keep_unknowns_and_do_not_promote_training(self):
        self.fixture()
        before = self.hashes()
        report = run_preflight(self.config)
        self.assertEqual(report["status"], "CONTRACTS_VALID")
        target = report["csv_contracts"]["train"]["target_statistics"]["ACL"]
        self.assertEqual(target["unknown"], 1)
        self.assertEqual(target["negative"], 0)
        self.assertEqual(target["observed"], 0)
        self.assertIsNone(target["prevalence"])
        self.assertEqual(report["phase_b_status"], "NOT_RUN")
        self.assertFalse(report["training_ready"])
        self.assertFalse(report["phase1_pass"])
        self.assertEqual(self.hashes(), before)
        self.assertNotIn("SYNTHETIC PRIVATE REPORT", json.dumps(report))

    def test_default_discovery_does_not_open_csv_contents(self):
        self.fixture()
        for path in self.source.iterdir():
            path.write_bytes(b"Not valid CSV\xff")
        report = run_preflight(self.config.with_overrides(inspect_csv=False))
        self.assertEqual(report["status"], "MANIFESTS_LOCATED")
        self.assertEqual(report["csv_contracts"]["status"], "NOT_REQUESTED")
        self.assertFalse(report["discovery"]["csv_contents_read"])

    def test_invalid_labels_remain_invalid(self):
        self.fixture(label="maybe")
        report = run_preflight(self.config)
        self.assertEqual(report["status"], "CONTRACTS_INVALID")
        self.assertEqual(report["csv_contracts"]["train"]["target_statistics"]["ACL"]["invalid"], 1)

    def test_train_test_overlap_blocks_contracts(self):
        self.fixture(train_id="same", test_id="same")
        report = run_preflight(self.config)
        self.assertEqual(report["status"], "CONTRACTS_INVALID")
        self.assertIn("overlap", report["csv_contracts"]["reason"])

    def test_orphan_series_and_missing_target_fail(self):
        self.fixture()
        (self.source / "train_series.csv").write_text("StudyInstanceUID,SeriesInstanceUID,Anatomical_Plane,Fluid_Sensitive,Fat_Suppression\nabsent,x,Sagittal,1,1\n", encoding="utf-8")
        self.assertEqual(run_preflight(self.config)["status"], "CONTRACTS_INVALID")
        self.fixture()
        (self.source / "train.csv").write_text("StudyInstanceUID,Report\n001,synthetic\n", encoding="utf-8")
        self.assertEqual(run_preflight(self.config)["status"], "CONTRACTS_INVALID")

    def test_parsed_inputs_remain_recorded_when_later_contract_fails(self):
        self.fixture()
        path = self.source / "train.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join([*lines, lines[1]]) + "\n", encoding="utf-8")
        report = run_preflight(self.config)
        self.assertEqual(report["status"], "CONTRACTS_INVALID")
        self.assertTrue(report["discovery"]["csv_contents_read"])
        self.assertTrue(report["discovery"]["csv_read_completed"])
        self.assertFalse(report["discovery"]["csv_preflight_completed"])
        self.assertEqual(len(report["discovery"]["csv_read_progress"]["sha256"]), 5)

    def test_output_is_unique_and_evidence_preserved(self):
        self.fixture()
        report = run_preflight(self.config)
        first = write_report(report, self.config)
        original = first.read_bytes()
        second = write_report(report, self.config)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(json.loads(first.read_text(encoding="utf-8")), report)

    def test_output_cannot_mutate_inputs_or_git_metadata(self):
        self.fixture()
        report = run_preflight(self.config)
        before = self.hashes()
        for path in (self.source, self.source / "reports", self.output / ".git"):
            with self.subTest(path=path), self.assertRaises(InputError):
                write_report(report, self.config.with_overrides(output_dir=str(path)))
        self.assertEqual(self.hashes(), before)

    def test_report_budget_rejected_before_output_created(self):
        with self.assertRaises(InputError):
            write_report({"large": "x" * 100}, self.config.with_overrides(max_report_bytes=10))
        self.assertFalse(self.output.exists())

    def test_cli_without_data_has_explicit_nonzero_exit_and_compact_artifact(self):
        process = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "audit.py"), "--input-base", str(self.source / "missing"), "--output-dir", str(self.output)], capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 2, process.stderr)
        summary = json.loads(process.stdout)
        self.assertEqual(summary["status"], "INPUT_BASE_MISSING")
        artifact = json.loads(Path(summary["report_path"]).read_text(encoding="utf-8"))
        self.assertFalse(artifact["training_ready"])
        self.assertEqual(len(artifact["source_identity"]["modules"]), 8)

    def test_csv_field_limits_are_reported(self):
        self.fixture()
        report = run_preflight(self.config.with_overrides(max_file_bytes=5))
        self.assertEqual(report["status"], "CONTRACTS_INVALID")
        self.assertEqual(report["csv_contracts"]["reason"], "CSV_FILE_LIMIT_EXCEEDED")
        self.assertTrue(report["discovery"]["csv_read_attempted"])
        self.assertFalse(report["discovery"]["csv_read_completed"])
        self.assertIsNone(report["discovery"]["csv_contents_read"])
