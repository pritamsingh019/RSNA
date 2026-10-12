"""AUC support, ties and missing-label boundaries using synthetic data."""

import unittest

from rsna_knee.contracts import ContractError, STUDY_ID, TARGETS
from rsna_knee.metrics import binary_auc, evaluate_predictions


def row(uid, value):
    return {STUDY_ID: uid, **{target: value for target in TARGETS}}


class BinaryAUCTests(unittest.TestCase):
    def test_perfect_reversed_and_tied_ranking(self):
        self.assertEqual(binary_auc([0, 1], [0.2, 0.8]), 1.0)
        self.assertEqual(binary_auc([0, 1], [0.8, 0.2]), 0.0)
        self.assertEqual(binary_auc([0, 1], [0.5, 0.5]), 0.5)
        self.assertEqual(binary_auc([0, 1, 0, 1], [0, 0, 1, 2]), 0.625)

    def test_rare_class_and_undefined_targets(self):
        self.assertEqual(binary_auc([0, 0, 0, 1], [0.1, 0.2, 0.3, 0.4]), 1.0)
        self.assertIsNone(binary_auc([], []))
        self.assertIsNone(binary_auc([0, 0], [0.3, 0.4]))
        self.assertIsNone(binary_auc([1], [0.4]))

    def test_reject_unknown_soft_bool_or_nonfinite_inputs(self):
        cases = [([None], [0.3]), ([True], [0.3]), (["1"], [0.3]), ([0.3], [0.3]), ([1], [float("nan")]), ([1], [float("inf")]), ([1], [True]), ([0, 1], [0.2])]
        for labels, scores in cases:
            with self.subTest(labels=labels), self.assertRaises(ContractError):
                binary_auc(labels, scores)


class EvaluationTests(unittest.TestCase):
    def test_observed_only_metrics_and_explicit_incomplete_macro(self):
        labels = [row("001", 0), row("002", 1), row("003", "")]
        labels[1]["Fracture"] = None
        predictions = [row("003", 0.99), row("002", 0.8), row("001", 0.2)]
        result = evaluate_predictions(labels, predictions)
        self.assertEqual(result["per_target"]["ACL"], {"observed": 2, "positive": 1, "negative": 1, "unknown": 1, "auc": 1.0})
        self.assertIsNone(result["per_target"]["Fracture"]["auc"])
        self.assertIsNone(result["macro_auc"])
        self.assertEqual(result["diagnostic_defined_target_macro_auc"], 1.0)
        self.assertEqual(result["defined_target_count"], 11)
        self.assertEqual(result["status"], "INCOMPLETE_METRICS")

    def test_full_macro_requires_all_twelve_supported_targets(self):
        result = evaluate_predictions([row("a", 0), row("b", 1)], [row("a", 0.1), row("b", 0.9)])
        self.assertEqual(result["macro_auc"], 1.0)
        self.assertEqual(result["defined_target_count"], 12)

    def test_all_unknown_labels_do_not_create_a_macro(self):
        result = evaluate_predictions([row("a", None)], [row("a", 0.5)])
        self.assertIsNone(result["macro_auc"])
        self.assertIsNone(result["diagnostic_defined_target_macro_auc"])

    def test_reject_duplicate_or_nonmatching_ids_and_invalid_labels(self):
        labels = [row("a", 0), row("b", 1)]
        cases = [([row("a", 0), row("a", 1)], [row("a", 0.5)]), (labels, [row("a", 0.1), row("c", 0.9)]), (labels, [row("a", 0.1), row("a", 0.9)])]
        for references, predictions in cases:
            with self.assertRaises(ContractError):
                evaluate_predictions(references, predictions)
        labels[0]["ACL"] = "0.2"
        with self.assertRaises(ContractError):
            evaluate_predictions(labels, [row("a", 0.1), row("b", 0.9)])


if __name__ == "__main__":
    unittest.main()
