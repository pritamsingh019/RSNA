"""Synthetic tests for exact identities, masks and study/series integrity."""

import unittest
from copy import deepcopy
from decimal import Decimal

from rsna_knee.contracts import (
    ContractError, STUDY_ID, TARGETS, audit_series, audit_studies,
    label_vector, parse_label, validate_identifier,
)


def study(uid="study-001", **changes):
    row = {STUDY_ID: uid, **{target: "" for target in TARGETS}, "Report": "private report text"}
    row.update(changes)
    return row


def series(uid="series-001", study_uid="study-001", **changes):
    row = {
        STUDY_ID: study_uid, "SeriesInstanceUID": uid,
        "Anatomical_Plane": "sagittal", "Fluid_Sensitive": "1",
        "Fat_Suppression": "0",
    }
    row.update(changes)
    return row


class IdentifierTests(unittest.TestCase):
    def test_identifiers_preserve_exact_spelling_and_non_numeric_format(self):
        for value in ("001.002", "Study-A", "study with internal space", "study_é"):
            self.assertEqual(validate_identifier(value), value)

    def test_reject_empty_padded_paths_and_control_characters(self):
        for value in ("", " ", " padded", "padded ", ".", "..", "../outside", "x/y", "x\\y", "C:drive", "x\x00y", "x\x7fy", "x\x85y", "x\u200by", 123, None):
            with self.subTest(value=repr(value)), self.assertRaises(ContractError):
                validate_identifier(value)


class LabelTests(unittest.TestCase):
    def test_observed_numeric_and_explicit_text(self):
        for value, expected in ((0, 0.0), (1, 1.0), (0.0, 0.0), (Decimal("1"), 1.0), (" 0 ", 0.0), ("1.0", 1.0)):
            self.assertEqual(parse_label(value), (expected, "observed"))

    def test_unknown_is_never_negative(self):
        for value in (None, "", " \t ", float("nan")):
            self.assertEqual(parse_label(value), (None, "unknown"))

    def test_invalid_values_are_not_observations(self):
        for value in (True, False, 0.25, 2, -1, float("inf"), float("-inf"), "NaN", "true", "-0", "0.00", "01", [], {}, 10 ** 1000):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(parse_label(value), (None, "invalid"))

    def test_label_vector_exposes_explicit_observation_mask_without_filling(self):
        source = {target: None for target in TARGETS}
        source.update({"ACL": "0", "MCL": 1, "Medial Meniscus": "", "Lateral Meniscus": "0.2"})
        original = deepcopy(source)
        vector = label_vector(source)
        self.assertEqual(vector["targets"], list(TARGETS))
        self.assertEqual(vector["values"], [0.0, 1.0, *([None] * 10)])
        self.assertEqual(vector["observed_mask"], [True, True, *([False] * 10)])
        self.assertEqual(vector["states"], ["observed", "observed", "unknown", "invalid", *(["unknown"] * 8)])
        self.assertEqual(source, original)
        self.assertNotIn("Report", source)

    def test_label_vector_rejects_missing_target_cells(self):
        with self.assertRaises(ContractError):
            label_vector({"ACL": 0})


class StudyAuditTests(unittest.TestCase):
    def test_masks_prevalence_and_jointly_observed_cooccurrence(self):
        rows = [study("001", ACL="1", MCL="1"), study("002", ACL="0", MCL=""), study("003", ACL="", MCL="0")]
        result = audit_studies(rows, "train")
        acl = result["target_statistics"]["ACL"]
        self.assertEqual((acl["positive"], acl["negative"], acl["unknown"]), (1, 1, 1))
        self.assertEqual(acl["prevalence"], 0.5)
        self.assertEqual(acl["observation_coverage"], 2 / 3)
        pair = next(item for item in result["jointly_observed_cooccurrence"] if item["targets"] == ["ACL", "MCL"])
        self.assertEqual(pair["jointly_observed"], 1)
        self.assertEqual(pair["both_positive_fraction"], 1.0)
        self.assertNotIn("private report text", repr(result))
        self.assertEqual(result["target_statistics"]["Fracture"]["prevalence"], None)
        self.assertEqual(result["status"], "PASS")

    def test_invalid_labels_remain_explicit_and_mark_audit_invalid(self):
        result = audit_studies([study(ACL="0.2")], "train")
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["target_statistics"]["ACL"]["invalid"], 1)
        self.assertEqual(result["target_statistics"]["ACL"]["negative"], 0)

    def test_audits_do_not_mutate_supplied_source_rows(self):
        studies = [study(ACL=" 1.0 ", MCL="")]
        series_rows = [series(Fluid_Sensitive=" 1 ", Anatomical_Plane=None)]
        original_studies, original_series = deepcopy(studies), deepcopy(series_rows)
        audit_studies(studies, "train")
        audit_series(series_rows, {"study-001"})
        self.assertEqual(studies, original_studies)
        self.assertEqual(series_rows, original_series)

    def test_report_summary_does_not_echo_content(self):
        result = audit_studies([study("a", Report=None), study("b", Report=""), study("c", Report=123)], "train")
        self.assertEqual(result["report_summary"], {"present": 0, "empty_or_missing": 2, "invalid": 1})
        self.assertEqual(result["status"], "INVALID")

    def test_test_table_requires_only_study_identifier(self):
        result = audit_studies([{STUDY_ID: "001.A"}], "test")
        self.assertEqual(result["study_ids"], ["001.A"])
        self.assertNotIn("target_statistics", result)

    def test_duplicate_missing_and_empty_study_rows_rejected(self):
        invalid_cases = ([study("a"), study("a")], [{STUDY_ID: "a"}], [], [{STUDY_ID: "../a"}])
        for rows in invalid_cases:
            with self.subTest(rows=len(rows)), self.assertRaises(ContractError):
                audit_studies(rows, "train")
        with self.assertRaises(ContractError):
            audit_studies([study()], "validation")


class SeriesAuditTests(unittest.TestCase):
    def test_metadata_missingness_and_exact_typed_values_are_preserved(self):
        rows = [series("s1", Anatomical_Plane="SAGITTAL", Fluid_Sensitive="1"), series("s2", Anatomical_Plane=None, Fluid_Sensitive=True, Fat_Suppression=" ")]
        result = audit_series(rows, {"study-001", "study-without-series"})
        self.assertEqual(result["study_count_without_series"], 1)
        self.assertEqual(result["metadata_statistics"]["Anatomical_Plane"]["unknown"], 1)
        values = result["metadata_statistics"]["Fluid_Sensitive"]["value_counts"]
        self.assertIn({"type": "str", "value": "1", "count": 1}, values)
        self.assertIn({"type": "bool", "value": True, "count": 1}, values)

    def test_invalid_metadata_is_recorded(self):
        result = audit_series([series(Fluid_Sensitive=[])], {"study-001"})
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["metadata_statistics"]["Fluid_Sensitive"]["invalid"], 1)

    def test_duplicate_series_and_missing_study_references_rejected(self):
        cases = [([series(), series()], {"study-001"}), ([series()], {"other-study"}), ([{STUDY_ID: "study-001"}], {"study-001"}), ([], {"study-001"})]
        for rows, studies in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(ContractError):
                audit_series(rows, studies)


if __name__ == "__main__":
    unittest.main()
