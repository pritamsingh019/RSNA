"""Phase C configuration, aggregate pipeline and leakage-design regressions."""

import copy
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from rsna_knee.audit import run_preflight
from rsna_knee.config import AuditConfig, ConfigError
from rsna_knee.contracts import TARGETS, audit_studies, label_vector
from rsna_knee.inputs import InputError
from rsna_knee.phase_c import CHECK_STATES, run_phase_c, write_phase_c_report
from rsna_knee.phase_c_config import PhaseCConfig, load_phase_c_config
from rsna_knee.phase_c_validation import FOLD_SAFEGUARDS, assess_validation
from test_phase_c_notebook import PRIVATE_REPORT, PRIVATE_UID, fixture, hashes


class PhaseCConfigurationTests(unittest.TestCase):
    def test_roundtrip_inheritance_and_no_dicom_options(self):
        config = PhaseCConfig()
        self.assertEqual(config, PhaseCConfig.from_dict(config.to_dict()))
        self.assertIs(type(config.preflight_config()), AuditConfig)
        self.assertTrue(config.inspect_csv)
        self.assertEqual(config.max_report_chars, 20000)
        for values in ({"inspect_csv": False}, {"max_report_chars": True}, {"max_report_clauses": 0},
                       {"max_extraction_seconds": .1}, {"max_report_chars": 20001},
                       {"max_report_clauses": 257}, {"max_csv_rows": 100001}, {"max_pixel_decodes": 1}):
            with self.subTest(values=values), self.assertRaises(ConfigError):
                PhaseCConfig.from_dict(values)

    def test_secure_json_loader_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase_c.json"
            path.write_text('{"max_report_chars": 900}', encoding="utf-8")
            self.assertEqual(load_phase_c_config(path).max_report_chars, 900)
            path.write_text('{"max_report_chars": 900, "max_report_chars": 1000}', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_phase_c_config(path)
        for path in (".aws/phase_c.json", ".env", "credentials/phase_c.json"):
            with self.subTest(path=path), self.assertRaises(ConfigError):
                load_phase_c_config(path)


class PhaseCPipelineTests(unittest.TestCase):
    def test_compact_private_report_masks_and_inputs_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            before = hashes(root)
            report = run_phase_c(PhaseCConfig.from_dict(settings))
            self.assertEqual(report["schema"], "rsna-knee-phase-c-v1")
            self.assertEqual(report["phase"], "PHASE_C")
            self.assertEqual(report["status"], "PASS")
            self.assertFalse(report["training_ready"])
            self.assertFalse(report["split_approved"])
            self.assertIsNone(report["model_metrics"])
            self.assertEqual(report["runtime"]["dicom_pixels_decoded"], 0)
            self.assertEqual(report["checks"]["rule_extraction"]["reports_processed"], 3)
            self.assertTrue(all(check["status"] in CHECK_STATES for check in report["checks"].values()))
            agreement = report["checks"]["observed_label_agreement"]
            self.assertFalse(agreement["independent_validation"])
            self.assertEqual(agreement["evaluation_status"], "EXPLORATORY")
            self.assertEqual(set(agreement["per_target"]), set(TARGETS))
            raw = json.dumps(report, allow_nan=False)
            for sentinel in (PRIVATE_REPORT, PRIVATE_UID, "PrivateNegativeSentinel", "private.synthetic.series", "study_ids", "prediction_table"):
                self.assertNotIn(sentinel, raw)
            self.assertLess(len(raw), 64 * 1024)
            self.assertEqual(hashes(root), before)
            config = PhaseCConfig.from_dict(settings)
            path = write_phase_c_report(report, config)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
            self.assertEqual(hashes(root), before)

    def test_freeze_precedes_extraction_and_agreement(self):
        from rsna_knee import report_rules, report_agreement
        calls = []
        freeze, extract, evaluate = report_rules.freeze_rules, report_rules.extract_report, report_agreement.evaluate_candidates
        def wrapped_freeze():
            calls.append("freeze")
            return freeze()
        def wrapped_extract(*args, **kwargs):
            calls.append("extract")
            return extract(*args, **kwargs)
        def wrapped_evaluate(*args, **kwargs):
            calls.append("evaluate")
            return evaluate(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            _, settings = fixture(Path(directory))
            with mock.patch.object(report_rules, "freeze_rules", wrapped_freeze), \
                    mock.patch.object(report_rules, "extract_report", wrapped_extract), \
                    mock.patch.object(report_agreement, "evaluate_candidates", wrapped_evaluate):
                run_phase_c(PhaseCConfig.from_dict(settings))
        self.assertEqual(calls, ["freeze", "extract", "extract", "extract", "evaluate"])

    def test_missing_invalid_and_stale_csv_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            missing = run_phase_c(PhaseCConfig(input_base=str(base / "missing")))
            self.assertEqual(missing["status"], "BLOCKED")
            self.assertEqual(missing["checks"]["observed_label_agreement"]["status"], "NOT_ASSESSED")
            root, settings = fixture(base)
            config = PhaseCConfig.from_dict(settings)
            preflight = run_preflight(config.preflight_config())
            with self.assertRaisesRegex(InputError, "stale"):
                run_phase_c(config.with_overrides(max_file_bytes=100), preflight=preflight)
            (root / "train.csv").write_bytes(b"invalid\n")
            report = run_phase_c(config)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["checks"]["rule_extraction"]["status"], "NOT_ASSESSED")

    def test_cooperative_extraction_budget_does_not_report_partial_agreement(self):
        with tempfile.TemporaryDirectory() as directory:
            _, settings = fixture(Path(directory))
            config = PhaseCConfig.from_dict(settings).with_overrides(max_extraction_seconds=1)
            with mock.patch("rsna_knee.phase_c.time.perf_counter", side_effect=itertools.count()):
                report = run_phase_c(config)
            self.assertEqual(report["checks"]["rule_extraction"]["status"], "BLOCKED")
            self.assertEqual(report["checks"]["observed_label_agreement"]["status"], "NOT_ASSESSED")
            self.assertFalse(report["training_ready"])

    def test_output_limits_and_protected_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            config = PhaseCConfig.from_dict(settings)
            with self.assertRaisesRegex(InputError, "REPORT_SIZE_LIMIT"):
                run_phase_c(config.with_overrides(max_report_bytes=10))
            with self.assertRaises(InputError):
                write_phase_c_report({}, config.with_overrides(output_dir=str(root / "write")))
            with self.assertRaises(InputError):
                write_phase_c_report({"large": "x" * 100}, config.with_overrides(max_report_bytes=10))
            self.assertFalse(Path(config.output_dir).exists())
            self.assertFalse((root / "write").exists())

    def test_arbitrary_source_metadata_cannot_enter_output(self):
        with self.assertRaisesRegex(InputError, "source identity"):
            run_phase_c(PhaseCConfig(), source_identity={"private": "PrivateReportOrPatientSentinel"})


class PhaseCValidationDesignTests(unittest.TestCase):
    def rows(self):
        return [{"StudyInstanceUID": "PrivatePatientStudy" + str(i), "Report": "PrivateReportText",
                 **{target: value for target in TARGETS}} for i, value in enumerate(("1", "0", ""))]

    def test_unknown_masks_unchanged_and_no_approved_grouping(self):
        rows = self.rows()
        original = copy.deepcopy(rows)
        masks = [label_vector(row) for row in rows]
        result = assess_validation(rows, [], audit_studies(rows, "train"))
        self.assertEqual(result["patient_grouped_validation"]["status"], "NOT_ASSESSED")
        self.assertFalse(result["split_approved"])
        self.assertFalse(result["training_ready"])
        self.assertEqual(rows, original)
        self.assertEqual(masks, [label_vector(row) for row in rows])
        self.assertNotIn("PrivatePatientStudy", json.dumps(result))
        self.assertNotIn("PrivateReportText", json.dumps(result))

    def test_candidate_patient_columns_are_counts_not_trust(self):
        rows = self.rows()
        rows[0]["PatientID"] = rows[1]["PatientID"] = "PrivatePatientSentinel"
        series = [{"StudyInstanceUID": rows[0]["StudyInstanceUID"], "PatientID": value}
                  for value in ("PrivatePatientSentinel", "ConflictingPrivatePatient")]
        result = assess_validation(rows, series, audit_studies(rows, "train"))
        grouping = result["patient_grouped_validation"]
        self.assertFalse(grouping["mapping_trustworthy"])
        self.assertEqual(grouping["status"], "NOT_ASSESSED")
        self.assertEqual(grouping["candidate_csv_fields"]["train_series.csv"]["PatientID"]["studies_with_inconsistent_candidate_values"], 1)
        self.assertNotIn("PrivatePatientSentinel", json.dumps(result))

    def test_fold_safeguards_are_concrete_and_cover_each_leakage_boundary(self):
        text = " ".join(FOLD_SAFEGUARDS).lower()
        for phrase in ("validation-study reports", "training partition", "original masks", "unknown",
                       "test studies", "image-only inference", "model-selection", "patient", "rule hash", "sample-submission"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
