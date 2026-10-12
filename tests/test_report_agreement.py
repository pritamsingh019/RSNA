"""Synthetic masked agreement, abstention denominators, and safe aggregates."""

from copy import deepcopy
import json
import math
import unittest

from rsna_knee.contracts import ContractError, STUDY_ID, TARGETS, label_vector
from rsna_knee.report_agreement import evaluate_candidates, wilson95


IDENTITY = {
    "version": "rsna-report-rules-v1.0.0",
    "sha256": "a" * 64,
    "engine_policy": "conservative-english-clause-v1",
}


def row(value, uid="PRIVATE_STUDY_SENTINEL"):
    return {STUDY_ID: uid, "Report": "PRIVATE_REPORT_SENTINEL", **{target: value for target in TARGETS}}


def candidate(state="UNKNOWN", conflict=False):
    return {
        "targets": {
            target: {"state": state, "conflict": conflict, "reason_codes": ("PRIVATE_REASON_SENTINEL",)}
            for target in TARGETS
        },
        "report_status": "PRIVATE_STATUS_SENTINEL",
        "rule_identity": deepcopy(IDENTITY),
        "PRIVATE_EXTRA_KEY": "PRIVATE_EXTRA_SENTINEL",
    }


class ReportAgreementTests(unittest.TestCase):
    def test_confusion_denominators_abstentions_and_distinct_detection(self):
        rows = [row(value) for value in (1, 1, 1, 0, 0, 0, "", None)]
        predictions = [candidate(state) for state in (
            "POSITIVE", "NEGATIVE", "UNKNOWN", "POSITIVE", "NEGATIVE", "UNKNOWN", "POSITIVE", "UNKNOWN",
        )]
        predictions[2] = candidate("UNKNOWN", conflict=True)
        result = evaluate_candidates(rows, predictions, IDENTITY)
        self.assertEqual(result["row_count"], 8)
        self.assertEqual(result["observed_label_study_count"], 6)
        self.assertEqual(result["observed_label_cell_count"], 72)
        self.assertEqual(list(result["per_target"]), list(TARGETS))
        for target in TARGETS:
            with self.subTest(target=target):
                data = result["per_target"][target]
                self.assertEqual(data["candidate_counts"], {"positive": 3, "negative": 2, "unknown": 3, "conflict": 1})
                self.assertEqual(data["truth_support"], {"observed": 6, "positive": 3, "negative": 3, "unknown": 2})
                self.assertEqual(data["abstained_unknown_by_truth"], {"positive": 1, "negative": 1, "unknown": 1})
                self.assertEqual(data["classified_truth_counts"], {"positive": 2, "negative": 2, "unknown": 1})
                self.assertEqual(data["confusion"], {"TP": 1, "FP": 1, "TN": 1, "FN": 1})
                self.assertEqual(data["coverage"]["value"], 5 / 8)
                self.assertEqual(data["coverage"]["denominator"], 8)
                self.assertEqual(data["observed_truth_coverage"]["value"], 4 / 6)
                expected = {
                    "agreement": (2, 4), "positive_predictive_value": (1, 2),
                    "selective_sensitivity": (1, 2), "selective_specificity": (1, 2),
                    "negative_predictive_value": (1, 2), "detection_sensitivity": (1, 3),
                }
                for name, (numerator, denominator) in expected.items():
                    metric = data["metrics"][name]
                    self.assertEqual(metric["numerator"], numerator)
                    self.assertEqual(metric["denominator"], denominator)
                    self.assertEqual(metric["value"], numerator / denominator)
                    self.assertEqual(metric["wilson95"], wilson95(numerator, denominator))

    def test_unknown_true_labels_never_enter_confusion_or_ppv(self):
        result = evaluate_candidates([row(None), row("")], [candidate("POSITIVE"), candidate("NEGATIVE")], IDENTITY)
        self.assertEqual(result["observed_label_study_count"], 0)
        self.assertEqual(result["observed_label_cell_count"], 0)
        for data in result["per_target"].values():
            self.assertEqual(data["confusion"], {"TP": 0, "FP": 0, "TN": 0, "FN": 0})
            self.assertEqual(data["classified_truth_counts"]["unknown"], 2)
            self.assertEqual(data["coverage"]["value"], 1.0)
            self.assertIsNone(data["observed_truth_coverage"]["value"])
            for metric in data["metrics"].values():
                self.assertEqual(metric, {"value": None, "numerator": 0, "denominator": 0, "wilson95": None})

    def test_abstention_does_not_become_a_negative_or_false_negative(self):
        result = evaluate_candidates([row(1), row(1), row(0)], [candidate(), candidate(), candidate()], IDENTITY)
        data = result["per_target"]["ACL"]
        self.assertEqual(data["confusion"], {"TP": 0, "FP": 0, "TN": 0, "FN": 0})
        self.assertEqual(data["abstained_unknown_by_truth"], {"positive": 2, "negative": 1, "unknown": 0})
        self.assertIsNone(data["metrics"]["selective_sensitivity"]["value"])
        self.assertIsNone(data["metrics"]["selective_specificity"]["value"])
        self.assertEqual(data["metrics"]["detection_sensitivity"]["value"], 0.0)
        self.assertEqual(data["metrics"]["detection_sensitivity"]["denominator"], 2)

    def test_target_specific_observation_masks_are_exact_and_inputs_immutable(self):
        rows = [row("1"), row("0"), row(None)]
        rows[0]["MCL"] = None
        rows[1]["MCL"] = ""
        rows[2]["Fracture"] = "1.0"
        predictions = [candidate("POSITIVE"), candidate("NEGATIVE"), candidate("UNKNOWN")]
        before = deepcopy((rows, predictions, IDENTITY))
        masks_before = [label_vector(item) for item in rows]
        result = evaluate_candidates(rows, predictions, IDENTITY)
        self.assertEqual((rows, predictions, IDENTITY), before)
        self.assertEqual([label_vector(item) for item in rows], masks_before)
        self.assertEqual(result["per_target"]["MCL"]["truth_support"]["observed"], 0)
        self.assertEqual(result["per_target"]["Fracture"]["truth_support"]["observed"], 3)
        self.assertEqual(result["per_target"]["ACL"]["truth_support"]["observed"], 2)
        self.assertEqual(result["observed_label_study_count"], 3)

    def test_actual_numeric_nan_remains_unknown(self):
        result = evaluate_candidates([row(float("nan"))], [candidate("NEGATIVE")], IDENTITY)
        self.assertEqual(result["observed_label_cell_count"], 0)
        self.assertEqual(result["per_target"]["ACL"]["confusion"]["TN"], 0)

    def test_output_contains_no_reports_identifiers_reasons_statuses_or_prediction_rows(self):
        result = evaluate_candidates([row(1)], [candidate("POSITIVE")], IDENTITY)
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn("PRIVATE_", encoded)
        self.assertNotIn(STUDY_ID, encoded)
        self.assertNotIn("Report", encoded)
        self.assertNotIn("reason_codes", encoded)
        self.assertNotIn("report_status", encoded)
        self.assertNotIn("predictions", encoded)
        self.assertLess(len(encoded), 64 * 1024)
        self.assertIsNone(result["model_metrics"])
        self.assertFalse(result["independent_validation"])
        self.assertEqual(result["evaluation_status"], "EXPLORATORY")

    def test_low_support_warning_threshold_is_exact(self):
        rows = [row(1)] * 10 + [row(0)] * 10
        predictions = [candidate("POSITIVE")] * 10 + [candidate("NEGATIVE")] * 10
        result = evaluate_candidates(rows, predictions, IDENTITY)
        self.assertEqual(result["per_target"]["ACL"]["warnings"], [])
        result = evaluate_candidates(rows[1:], predictions[1:], IDENTITY)
        warnings = result["per_target"]["ACL"]["warnings"]
        self.assertTrue(any("LOW_POSITIVE_SUPPORT" in warning for warning in warnings))
        self.assertTrue(any("selective_sensitivity" in warning for warning in warnings))
        self.assertFalse(any("LOW_NEGATIVE_SUPPORT" in warning for warning in warnings))

    def test_fifty_eight_studies_cannot_imply_independent_reliable_validation(self):
        rows = [row(1)] * 29 + [row(0)] * 29
        predictions = [candidate("POSITIVE")] * 29 + [candidate("NEGATIVE")] * 29
        result = evaluate_candidates(rows, predictions, IDENTITY)
        self.assertEqual(result["observed_label_study_count"], 58)
        self.assertFalse(result["independent_validation"])
        self.assertTrue(any("58 studies" in limitation for limitation in result["limitations"]))
        self.assertTrue(any("patient independence" in limitation for limitation in result["limitations"]))

    def test_empty_aligned_inputs_have_explicit_zero_support(self):
        result = evaluate_candidates([], [], IDENTITY)
        self.assertEqual(result["row_count"], 0)
        self.assertEqual(result["observed_label_cell_count"], 0)
        self.assertIsNone(result["per_target"]["ACL"]["coverage"]["value"])
        self.assertTrue(result["per_target"]["ACL"]["warnings"])

    def test_invalid_source_label_cells_fail_without_exposing_values(self):
        for value in ("PRIVATE_INVALID_LABEL", True, 0.5, float("inf"), "NaN"):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "agreement source contains an invalid label cell") as error:
                evaluate_candidates([row(value)], [candidate()], IDENTITY)
            self.assertNotIn("PRIVATE_", str(error.exception))

    def test_missing_source_target_or_nonmapping_source_fails_static(self):
        missing = row(1)
        del missing["ACL"]
        for item in (missing, "PRIVATE_INVALID_ROW", None):
            with self.subTest(item=item), self.assertRaisesRegex(ContractError, "agreement source label contract is invalid") as error:
                evaluate_candidates([item], [candidate()], IDENTITY)
            self.assertNotIn("PRIVATE_", str(error.exception))

    def test_unaligned_or_unbounded_sequence_is_rejected(self):
        cases = [([row(1)], []), ([], [candidate()]), ("PRIVATE_ROW", []), ([], "PRIVATE_PREDICTION")]
        for rows, predictions in cases:
            with self.subTest(rows=rows), self.assertRaisesRegex(ContractError, "aligned bounded"):
                evaluate_candidates(rows, predictions, IDENTITY)
        with self.assertRaisesRegex(ContractError, "aligned bounded"):
            evaluate_candidates([row(1)] * 100_001, [candidate()] * 100_001, IDENTITY)

    def test_malformed_candidates_fail_with_static_errors(self):
        bad_candidates = [None, "PRIVATE_CANDIDATE"]
        missing_target = candidate()
        del missing_target["targets"]["ACL"]
        bad_candidates.append(missing_target)
        extra_target = candidate()
        extra_target["targets"]["PRIVATE_EXTRA_TARGET"] = {}
        bad_candidates.append(extra_target)
        for bad_state in ("PRIVATE_STATE", None, 0, [], "positive"):
            item = candidate()
            item["targets"]["ACL"]["state"] = bad_state
            bad_candidates.append(item)
        for bad_conflict in (None, 1, "PRIVATE_CONFLICT"):
            item = candidate()
            item["targets"]["ACL"]["conflict"] = bad_conflict
            bad_candidates.append(item)
        bad_candidates.append(candidate("POSITIVE", conflict=True))
        for item in bad_candidates:
            with self.subTest(item=item), self.assertRaises(ContractError) as error:
                evaluate_candidates([row(1)], [item], IDENTITY)
            self.assertNotIn("PRIVATE_", str(error.exception))

    def test_mismatched_candidate_rule_identity_is_rejected(self):
        predictions = [candidate(), candidate()]
        predictions[-1]["rule_identity"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ContractError, "candidate rule identity mismatch"):
            evaluate_candidates([row(1), row(0)], predictions, IDENTITY)

    def test_invalid_rule_identity_is_not_echoed(self):
        identities = [None, {}, "PRIVATE_IDENTITY"]
        for key, value in (
            ("version", "PRIVATE_VERSION"), ("sha256", "PRIVATE_DIGEST"),
            ("sha256", "A" * 64), ("engine_policy", "PRIVATE_POLICY"),
        ):
            item = deepcopy(IDENTITY)
            item[key] = value
            identities.append(item)
        extra = deepcopy(IDENTITY)
        extra["PRIVATE_KEY"] = "PRIVATE_VALUE"
        identities.append(extra)
        for identity in identities:
            with self.subTest(identity=identity), self.assertRaisesRegex(ContractError, "valid frozen rule identity") as error:
                evaluate_candidates([], [], identity)
            self.assertNotIn("PRIVATE_", str(error.exception))

    def test_output_identity_is_detached_from_input(self):
        identity = deepcopy(IDENTITY)
        result = evaluate_candidates([], [], identity)
        identity["sha256"] = "b" * 64
        self.assertEqual(result["rule_identity"], IDENTITY)


class WilsonIntervalTests(unittest.TestCase):
    def test_known_half_and_extreme_intervals(self):
        interval = wilson95(5, 10)
        self.assertAlmostEqual(interval[0], 0.236593090512564)
        self.assertAlmostEqual(interval[1], 0.763406909487436)
        low = wilson95(0, 10)
        high = wilson95(10, 10)
        self.assertAlmostEqual(low[0], 0.0)
        self.assertAlmostEqual(low[1], 0.2775327998628892)
        self.assertAlmostEqual(high[0], 0.7224672001371107)
        self.assertAlmostEqual(high[1], 1.0)
        self.assertEqual(wilson95(0, 0), None)

    def test_all_finite_bounds_contain_proportion(self):
        for denominator in (1, 2, 58, 696, 4407):
            for numerator in (0, denominator // 2, denominator):
                with self.subTest(numerator=numerator, denominator=denominator):
                    low, high = wilson95(numerator, denominator)
                    self.assertTrue(math.isfinite(low) and math.isfinite(high))
                    self.assertGreaterEqual(low, 0)
                    self.assertLessEqual(high, 1)
                    self.assertLessEqual(low, numerator / denominator + 1e-15)
                    self.assertGreaterEqual(high, numerator / denominator - 1e-15)

    def test_invalid_counts_are_rejected(self):
        for numerator, denominator in ((-1, 1), (2, 1), (0, -1), (1, 0), (True, 2), (1, True), (1.0, 2)):
            with self.subTest(numerator=numerator, denominator=denominator), self.assertRaises(ContractError):
                wilson95(numerator, denominator)


if __name__ == "__main__":
    unittest.main()
