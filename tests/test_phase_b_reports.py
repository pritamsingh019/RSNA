"""Synthetic aggregate-report privacy and sparse-label support regressions."""

from copy import deepcopy
import json
import unittest

from rsna_knee.contracts import STUDY_ID, TARGETS, audit_studies, label_vector
from rsna_knee.report_quality import aggregate_reports
from rsna_knee.validation_support import summarize_validation


def row(index, report="SENTINEL_PRIVATE_REPORT_ONLY", **changes):
    result = {STUDY_ID: f"RAW_PRIVATE_STUDY_ID_{index}", "Report": report, **dict.fromkeys(TARGETS, "")}
    result.update(changes)
    return result


class ReportQualityTests(unittest.TestCase):
    def test_missing_empty_invalid_and_no_text_or_identifiers_exported(self):
        rows = [row(1), row(2, None), row(3, " \n "), row(4, 123), {STUDY_ID: "RAW_PRIVATE_STUDY_ID_5"}]
        original = deepcopy(rows)
        report = aggregate_reports(rows)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["missing_report_count"], 2)
        self.assertEqual(report["empty_report_count"], 1)
        self.assertEqual(report["invalid_report_type_count"], 1)
        self.assertEqual(report["nonempty_report_count"], 1)
        payload = json.dumps(report, allow_nan=False)
        self.assertNotIn("SENTINEL_PRIVATE_REPORT_ONLY", payload)
        self.assertNotIn("RAW_PRIVATE_STUDY_ID", payload)
        self.assertEqual(rows, original)

    def test_duplicate_normalization_without_exporting_normalized_keys(self):
        reports = ["SENTINEL Secret text", " sentinel   SECRET\ntext ", "ＳＥＮＴＩＮＥＬ Secret text", "", None]
        result = aggregate_reports([row(index, value) for index, value in enumerate(reports)])
        self.assertEqual(result["normalized_duplicate_report_count"], 2)
        self.assertEqual(result["normalized_duplicate_group_count"], 1)
        self.assertEqual(result["reports_in_duplicate_groups"], 3)
        self.assertNotIn("sentinel secret text", json.dumps(result).lower())

    def test_distributions_use_documented_deterministic_interpolation(self):
        result = aggregate_reports([row(index, "x" * length) for index, length in enumerate((10, 20, 30, 40, 50))])
        distribution = result["character_count"]
        self.assertEqual(distribution["min"], 10)
        self.assertEqual(distribution["median"], 30)
        self.assertEqual(distribution["p90"], 46)
        self.assertAlmostEqual(distribution["p99"], 49.6)
        self.assertEqual(distribution["max"], 50)
        self.assertEqual(result["whitespace_word_count"]["median"], 1)
        self.assertEqual(aggregate_reports([])["character_count"]["median"], None)

    def test_unicode_categories_and_quality_flags_are_fixed_aggregate_keys(self):
        reports = ["Latin only", "हिन्दी", "Mixed हिन्दी", "Ελληνικά", "123",
                   "Latin Ελληνικά", "हिन्दी Ελληνικά", "Latin हिन्दी Ελληνικά",
                   "bad\x00\u200b\ufffd\ue000\U0001f600", "a" * 10_001, "\tline\nline\r"]
        result = aggregate_reports([row(index, value) for index, value in enumerate(reports)])
        scripts = result["unicode_script_indicators"]
        self.assertEqual(scripts["devanagari_present_report_count"], 4)
        self.assertEqual(scripts["heuristic_report_categories"]["devanagari_only"], 1)
        self.assertEqual(scripts["heuristic_report_categories"]["other_letters_only"], 1)
        self.assertEqual(scripts["heuristic_report_categories"]["latin_devanagari_and_other_letters"], 1)
        flags = result["quality_flag_report_counts"]
        for name in ("unexpected_control_characters", "format_characters", "replacement_characters", "private_use_characters", "other_symbols", "unusual_characters", "very_long_reports"):
            self.assertEqual(flags[name], 1)
        self.assertTrue(result["quality_review_required"])
        self.assertFalse(result["weak_labels_created"])
        self.assertFalse(result["target_labels_changed"])
        self.assertIn("not reliable language identification", " ".join(result["limitations"]))

    def test_output_size_is_independent_of_unique_report_strings(self):
        rows = [row(index, f"SENTINEL_UNIQUE_PRIVATE_{index} " * 5) for index in range(2_000)]
        report = aggregate_reports(rows)
        payload = json.dumps(report, allow_nan=False)
        self.assertLess(len(payload.encode("utf-8")), 5_000)
        self.assertNotIn("SENTINEL_UNIQUE_PRIVATE", payload)

    def test_devanagari_marks_and_digits_indicate_script_without_words_or_letters(self):
        result = aggregate_reports([row(1, "\u0967\u0968"), row(2, "\u093e\u094d")])
        scripts = result["unicode_script_indicators"]
        self.assertEqual(scripts["devanagari_present_report_count"], 2)
        self.assertEqual(scripts["devanagari_character_count"], 4)
        self.assertEqual(scripts["devanagari_letter_count"], 0)
        self.assertEqual(scripts["heuristic_report_categories"]["devanagari_only"], 2)


class ValidationSupportTests(unittest.TestCase):
    def test_sparse_masks_unknowns_and_source_rows_are_preserved(self):
        rows = [row(1, ACL="1", MCL="0"), row(2, ACL="0"), row(3, MCL="1")]
        original = deepcopy(rows)
        masks = [label_vector(item) for item in rows]
        summary = summarize_validation(rows, audit_studies(rows, "train"))
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["targets"]["ACL"]["unknown"], 1)
        self.assertEqual(summary["targets"]["ACL"]["negative"], 1)
        self.assertEqual(summary["targets"]["Fracture"]["negative"], 0)
        self.assertEqual(summary["observed_label_cells"], 4)
        self.assertEqual(summary["unknown_label_cells"], 32)
        self.assertEqual(summary["study_grouped_class_support"]["two_folds"]["necessary_support_status"], "FAIL")
        self.assertFalse(summary["training_ready"])
        self.assertFalse(summary["split_approved"])
        self.assertEqual(summary["patient_grouping"]["status"], "NOT_ASSESSED")
        self.assertEqual(summary["gold_resource"]["status"], "NOT_ESTABLISHED")
        self.assertEqual(rows, original)
        self.assertEqual([label_vector(item) for item in rows], masks)
        payload = json.dumps(summary, allow_nan=False)
        self.assertNotIn("RAW_PRIVATE_STUDY_ID", payload)
        self.assertNotIn("SENTINEL_PRIVATE_REPORT_ONLY", payload)
        self.assertIn("Sparse labels may prevent a reliable twelve-target validation estimate", payload)

    def test_feasible_tiny_class_coverage_witness_is_not_approved_split(self):
        rows = [row(index, **dict.fromkeys(TARGETS, str(index % 2))) for index in range(6)]
        result = summarize_validation(rows, audit_studies(rows, "train"))
        for name in ("two_folds", "three_folds"):
            diagnostic = result["study_grouped_class_support"][name]
            self.assertEqual(diagnostic["necessary_support_status"], "PASS")
            self.assertEqual(diagnostic["joint_class_coverage"]["status"], "PASS")
            self.assertFalse(diagnostic["joint_class_coverage"]["split_approved"])

    def test_sufficient_marginal_support_can_still_have_impossible_joint_split(self):
        # Positive support pairs form an odd cycle requiring three mutually
        # opposite fold assignments. All classes separately have two examples.
        rows = [row(index) for index in range(5)]
        pairs = ((0, 1), (1, 2), (0, 2))
        for target_index, target in enumerate(TARGETS):
            for index in pairs[target_index % 3]:
                rows[index][target] = "1"
            rows[3][target] = rows[4][target] = "0"
        result = summarize_validation(rows, audit_studies(rows, "train"))
        two = result["study_grouped_class_support"]["two_folds"]
        self.assertEqual(two["necessary_support_status"], "PASS")
        self.assertEqual(two["joint_class_coverage"]["status"], "FAIL")
        self.assertIn("infeasible", two["joint_class_coverage"]["reason"])

    def test_joint_search_limit_never_claims_feasibility_from_counts(self):
        rows = [row(index, **dict.fromkeys(TARGETS, str(index % 2))) for index in range(58)]
        result = summarize_validation(rows, audit_studies(rows, "train"))
        for name in ("two_folds", "three_folds"):
            diagnostic = result["study_grouped_class_support"][name]
            self.assertEqual(diagnostic["necessary_support_status"], "PASS")
            self.assertEqual(diagnostic["joint_class_coverage"]["status"], "NOT_ASSESSED")
        self.assertEqual(result["gold_resource"]["status"], "NOT_ESTABLISHED")

    def test_stale_invalid_or_missing_verified_summary_fails_closed(self):
        rows = [row(1, ACL="1")]
        valid = audit_studies(rows, "train")
        stale = deepcopy(valid)
        stale["target_statistics"]["ACL"]["positive"] = 3
        malformed = deepcopy(valid)
        malformed["target_statistics"]["ACL"] = "not statistics"
        unknown_target = deepcopy(valid)
        del unknown_target["target_statistics"]["ACL"]
        for summary in ({}, stale, malformed, unknown_target, {**valid, "row_count": 2}):
            with self.subTest(summary_keys=list(summary)):
                self.assertEqual(summarize_validation(rows, summary)["status"], "FAIL")
        rows[0]["ACL"] = "0.7"
        self.assertEqual(summarize_validation(rows, audit_studies(rows, "train"))["status"], "FAIL")

    def test_duplicate_study_rows_do_not_establish_grouped_support(self):
        rows = [row(1, ACL="1")]
        summary = audit_studies(rows, "train")
        duplicated = rows * 2
        summary["row_count"] = 2
        self.assertEqual(summarize_validation(duplicated, summary)["status"], "FAIL")

    def test_summary_is_deterministic_and_contains_only_fixed_targets(self):
        rows = [row(index, **dict.fromkeys(TARGETS, str(index % 2))) for index in range(6)]
        summary = audit_studies(rows, "train")
        first = summarize_validation(rows, summary)
        second = summarize_validation(list(reversed(rows)), summary)
        self.assertEqual(first, second)
        self.assertEqual(list(first["targets"]), list(TARGETS))


if __name__ == "__main__":
    unittest.main()
