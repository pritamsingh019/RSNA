"""Synthetic rule regressions; these are not measured clinical reliability."""

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from rsna_knee.contracts import TARGETS
from rsna_knee.report_rules import ENGINE_POLICY, RULE_VERSION, extract_report, freeze_rules


EXAMPLES = {
    "ACL": ("Complete ACL tear.", "The ACL is intact.", "ACL evaluated."),
    "MCL": ("The MCL is sprained.", "MCL is normal.", "MCL mentioned."),
    "Medial Meniscus": ("Tear of the medial meniscus.", "Medial meniscus is intact.", "Medial meniscus."),
    "Lateral Meniscus": ("Lateral meniscal tear.", "No lateral meniscal tear.", "Lateral meniscus evaluated."),
    "Medial OA": ("Medial compartment osteoarthritis.", "No medial compartment osteoarthritis.", "Medial compartment cartilage thinning."),
    "Lateral OA": ("Lateral compartment degenerative changes.", "No lateral OA.", "Lateral compartment cartilage loss."),
    "PF OA": ("Patellofemoral osteoarthritis.", "No PF OA.", "Patellofemoral cartilage is normal."),
    "Effusion": ("Small joint effusion.", "No effusion.", "Effusion evaluated."),
    "Synovitis": ("Synovitis is present.", "Synovitis is absent.", "Synovitis."),
    "Baker's": ("A small Baker's cyst is identified.", "No popliteal cyst.", "Baker's cyst evaluated."),
    "Contusion": ("Bone contusion is identified.", "No contusion.", "Contusion mentioned."),
    "Fracture": ("An acute fracture is present.", "No evidence of fracture.", "Fracture."),
}


class ReportRulesTests(unittest.TestCase):
    def setUp(self):
        self.frozen = freeze_rules()

    def state(self, report, target):
        return extract_report(report, self.frozen)["targets"][target]["state"]

    def test_all_twelve_targets_have_positive_negative_unknown_examples(self):
        self.assertEqual(tuple(EXAMPLES), TARGETS)
        for target, examples in EXAMPLES.items():
            for expected, text in zip(("POSITIVE", "NEGATIVE", "UNKNOWN"), examples):
                with self.subTest(target=target, expected=expected):
                    result = extract_report(text, self.frozen)
                    self.assertEqual(result["targets"][target]["state"], expected)
                    self.assertFalse(result["targets"][target]["conflict"])
                    self.assertEqual(list(result["targets"]), list(TARGETS))

    def test_absent_terms_do_not_become_negative_labels(self):
        for text in ("A normal MRI examination.", "No significant abnormality.", "Pain is present."):
            with self.subTest(text=text):
                result = extract_report(text, self.frozen)
                self.assertTrue(all(item["state"] == "UNKNOWN" for item in result["targets"].values()))

    def test_explicit_negation_including_coordinated_scope(self):
        examples = (
            ("No ACL tear or MCL rupture.", ("ACL", "MCL")),
            ("No ACL or MCL tear.", ("ACL", "MCL")),
            ("No effusion or synovitis.", ("Effusion", "Synovitis")),
            ("No evidence of fracture or contusion.", ("Fracture", "Contusion")),
            ("Without medial meniscal tear.", ("Medial Meniscus",)),
            ("Fracture is not identified.", ("Fracture",)),
            ("Small effusion is not visualized.", ("Effusion",)),
            ("Moderate effusion not detected.", ("Effusion",)),
            ("Medial OA is absent.", ("Medial OA",)),
            ("No osteoarthritis in the medial compartment.", ("Medial OA",)),
        )
        for text, targets in examples:
            for target in targets:
                with self.subTest(text=text, target=target):
                    self.assertEqual(self.state(text, target), "NEGATIVE")

    def test_contrast_starts_new_scope_and_no_evidence_is_not_affirmative(self):
        result = extract_report("No fracture but bone contusion is present.", self.frozen)
        self.assertEqual(result["targets"]["Fracture"]["state"], "NEGATIVE")
        self.assertEqual(result["targets"]["Contusion"]["state"], "POSITIVE")
        self.assertEqual(self.state("No evidence of effusion.", "Effusion"), "NEGATIVE")
        result = extract_report("There is no fracture and there is a small effusion.", self.frozen)
        self.assertEqual(result["targets"]["Fracture"]["state"], "NEGATIVE")
        self.assertEqual(result["targets"]["Effusion"]["state"], "POSITIVE")

    def test_uncertainty_differential_and_question_mark_remain_unknown(self):
        for prefix in ("Possible ", "Probable ", "Cannot exclude ", "Rule out ", "Equivocal ", "Suspected ", "May represent ", "Possibility of ", "Suspicious for ", "Suspicion of ", "Moderate risk of "):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.state(prefix + "ACL tear.", "ACL"), "UNKNOWN")
        for text in ("Fracture cannot be ruled out.", "Fracture is not excluded.", "Fracture?", "ACL tear?", "Tear versus degeneration of medial meniscus."):
            target = "ACL" if "ACL" in text else "Medial Meniscus" if "meniscus" in text else "Fracture"
            with self.subTest(text=text):
                self.assertEqual(self.state(text, target), "UNKNOWN")
        self.assertEqual(self.state("ACL intact. Possible ACL tear.", "ACL"), "UNKNOWN")
        for text, target in (("Suspicious for small effusion.", "Effusion"), ("Suspicion of fracture is present.", "Fracture"), ("Moderate risk of fracture.", "Fracture")):
            with self.subTest(text=text):
                self.assertEqual(self.state(text, target), "UNKNOWN")

    def test_history_indication_and_differential_sections_are_not_findings(self):
        for text in ("History of ACL tear.", "Prior ACL tear.", "Status post ACL repair.", "Indication: ACL tear.", "Clinical history: fracture.", "Differential diagnosis: small effusion."):
            target = "ACL" if "ACL" in text else "Fracture" if "fracture" in text else "Effusion"
            with self.subTest(text=text):
                self.assertEqual(self.state(text, target), "UNKNOWN")
        text = "History: ACL tear\nSmall effusion\nFindings: ACL intact.\nImpression: No effusion."
        result = extract_report(text, self.frozen)
        self.assertEqual(result["targets"]["ACL"]["state"], "NEGATIVE")
        self.assertEqual(result["targets"]["Effusion"]["state"], "NEGATIVE")
        self.assertEqual(self.state("History: ACL tear. Findings: ACL intact.", "ACL"), "NEGATIVE")
        self.assertEqual(self.state("An old healed fracture is seen.", "Fracture"), "UNKNOWN")

    def test_anatomy_and_compartments_are_specific(self):
        result = extract_report("ACL tear and MCL intact. Medial meniscus tear and lateral meniscus normal.", self.frozen)
        for target in ("ACL", "Medial Meniscus"):
            self.assertEqual(result["targets"][target]["state"], "POSITIVE")
        for target in ("MCL", "Lateral Meniscus"):
            self.assertEqual(result["targets"][target]["state"], "NEGATIVE")
        result = extract_report("Medial compartment OA. No lateral OA. PF cartilage thinning.", self.frozen)
        self.assertEqual(result["targets"]["Medial OA"]["state"], "POSITIVE")
        self.assertEqual(result["targets"]["Lateral OA"]["state"], "NEGATIVE")
        self.assertEqual(result["targets"]["PF OA"]["state"], "UNKNOWN")
        result = extract_report("PCL tear. LCL sprain. Meniscus tear. Osteoarthritis.", self.frozen)
        self.assertTrue(all(item["state"] == "UNKNOWN" for item in result["targets"].values()))

    def test_scope_boundaries_do_not_transfer_negation_or_other_structure_findings(self):
        examples = (
            ("No ACL tear and MCL is torn.", "MCL", "POSITIVE"),
            ("No MCL injury with ACL tear.", "ACL", "POSITIVE"),
            ("No ACL tear, MCL rupture.", "MCL", "UNKNOWN"),
            ("No interval change in moderate effusion.", "Effusion", "UNKNOWN"),
            ("No pain related to a Baker's cyst.", "Baker's", "UNKNOWN"),
            ("ACL and PCL tear.", "ACL", "UNKNOWN"),
            ("MCL and lateral collateral ligament sprain.", "MCL", "UNKNOWN"),
            ("Soft tissue contusion is present.", "Contusion", "UNKNOWN"),
            ("Contusion of the skin is present.", "Contusion", "UNKNOWN"),
            ("Contusion is present.", "Contusion", "UNKNOWN"),
            ("Medial OA evaluated.", "Medial OA", "UNKNOWN"),
            ("Small effusion has resolved.", "Effusion", "UNKNOWN"),
            ("Small effusion is not definitely present.", "Effusion", "UNKNOWN"),
        )
        for text, target, expected in examples:
            with self.subTest(text=text, target=target):
                self.assertEqual(self.state(text, target), expected)
        result = extract_report("Medial meniscus posterior horn tear without lateral meniscus abnormality.", self.frozen)
        self.assertEqual(result["targets"]["Medial Meniscus"]["state"], "POSITIVE")
        self.assertEqual(result["targets"]["Lateral Meniscus"]["state"], "NEGATIVE")

    def test_conflicting_explicit_findings_are_unknown_with_conflict_flag(self):
        for target, (positive, negative, _) in EXAMPLES.items():
            with self.subTest(target=target):
                item = extract_report(positive + " " + negative, self.frozen)["targets"][target]
                self.assertEqual(item["state"], "UNKNOWN")
                self.assertTrue(item["conflict"])
                self.assertIn("CONTRADICTORY_EVIDENCE", item["reason_codes"])
        item = extract_report("ACL is intact with a tear.", self.frozen)["targets"]["ACL"]
        self.assertEqual(item["state"], "UNKNOWN")
        self.assertTrue(item["conflict"])

    def test_not_normal_or_not_intact_does_not_establish_absence(self):
        self.assertEqual(self.state("The ACL is not intact.", "ACL"), "UNKNOWN")
        self.assertEqual(self.state("The MCL is not normal.", "MCL"), "UNKNOWN")

    def test_unicode_nfkc_case_and_whitespace_normalization(self):
        expected = extract_report("ACL tear.", self.frozen)
        self.assertEqual(extract_report("\uff21\uff23\uff2c\t\uff34\uff25\uff21\uff32\uff0e", self.frozen), expected)
        self.assertEqual(self.state("  NO\t\n", "Fracture"), "UNKNOWN")
        self.assertEqual(self.state("Small Baker\u2019s cyst is seen.", "Baker's"), "POSITIVE")
        self.assertEqual(self.state("\t ACL    TEAR. \r\n", "ACL"), "POSITIVE")

    def test_empty_malformed_unexpected_type_and_unsupported_reports_fail_closed(self):
        values = (None, "", " \t\r\n", 1, True, [], {}, b"ACL tear", "ACL tear\x00", "ACL tear\ud800", "ACL tear\ufffd", "ACL tear \u0915\u094b", "ACL tear nahi hai")
        for report in values:
            with self.subTest(report_type=type(report).__name__):
                result = extract_report(report, self.frozen)
                self.assertTrue(all(item["state"] == "UNKNOWN" for item in result["targets"].values()))
                self.assertNotEqual(result["report_status"], "PROCESSED")

    def test_character_clause_and_parameter_budgets_fail_closed(self):
        self.assertEqual(extract_report("ACL tear." * 3, self.frozen, max_characters=10)["report_status"], "CHARACTER_LIMIT")
        self.assertEqual(extract_report("ACL tear. ACL tear.", self.frozen, max_clauses=1)["report_status"], "CLAUSE_LIMIT")
        for kwargs in ({"max_characters": True}, {"max_characters": 20_001}, {"max_clauses": 0}, {"max_clauses": 257}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "budget"):
                extract_report("ACL tear.", self.frozen, **kwargs)
        # No prefix-only classification when a trailing contradiction exceeds budget.
        result = extract_report("ACL tear. " + " " * 20_000 + "ACL intact.", self.frozen)
        self.assertEqual(result["targets"]["ACL"]["state"], "UNKNOWN")

    def test_results_contain_only_fixed_aggregate_safe_categories(self):
        secret = "PRIVATE_REPORT_SENTINEL_UID_12345"
        result = extract_report(secret + " ACL tear.", self.frozen)
        payload = json.dumps(result, allow_nan=False)
        self.assertNotIn(secret, payload)
        self.assertEqual(set(result), {"targets", "report_status", "rule_identity"})
        for item in result["targets"].values():
            self.assertEqual(set(item), {"state", "conflict", "reason_codes"})
            self.assertLessEqual(len(item["reason_codes"]), 8)
        self.assertLess(len(payload), 4_000)

    def test_freeze_identity_is_stable_readable_and_not_mutable(self):
        identity = self.frozen.identity()
        self.assertEqual(identity, freeze_rules().identity())
        self.assertEqual(identity["version"], RULE_VERSION)
        self.assertEqual(identity["engine_policy"], ENGINE_POLICY)
        self.assertEqual(len(identity["sha256"]), 64)
        self.assertEqual(tuple(rule.target for rule in self.frozen.rules), TARGETS)
        self.assertIsInstance(self.frozen.cues, tuple)
        with self.assertRaises(FrozenInstanceError):
            self.frozen.version = "changed"
        with self.assertRaises(FrozenInstanceError):
            self.frozen.rules[0].mention = "changed"
        identity["version"] = "caller changed copy"
        self.assertEqual(self.frozen.identity()["version"], RULE_VERSION)

    def test_integrity_hash_covers_rules_cues_normalization_and_scope(self):
        variants = (
            replace(self.frozen, rules=(replace(self.frozen.rules[0], mention="CHANGED"), *self.frozen.rules[1:])),
            replace(self.frozen, cues=((*self.frozen.cues[0][:-1], "CHANGED"), *self.frozen.cues[1:])),
            replace(self.frozen, normalization_policy=("CHANGED",)),
            replace(self.frozen, scope_policy=("CHANGED",)),
            replace(self.frozen, version="CHANGED"),
            replace(self.frozen, engine_policy="CHANGED"),
            replace(self.frozen, normalization_policy=([],)),
        )
        for frozen in variants:
            with self.subTest(), self.assertRaisesRegex(ValueError, "^frozen rule-set integrity check failed$"):
                frozen.verify()
        object.__setattr__(self.frozen, "sha256", "0" * 64)
        with self.assertRaisesRegex(ValueError, "integrity check failed"):
            extract_report("ACL tear.", self.frozen)
        with self.assertRaisesRegex(TypeError, "frozen rule set"):
            extract_report("ACL tear.", {})


if __name__ == "__main__":
    unittest.main()
