"""Exact schema, probability boundaries and exclusive submission writing."""

import csv
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rsna_knee.contracts import ContractError, STUDY_ID, TARGETS
from rsna_knee.inputs import InputError
from rsna_knee.submission import SUBMISSION_COLUMNS, validate_submission, write_submission


def row(uid="001", value=0.5):
    return {STUDY_ID: uid, **{target: value for target in TARGETS}}


class SubmissionTests(unittest.TestCase):
    def test_accept_exact_ids_order_and_probability_endpoints(self):
        result = validate_submission([row("001", 0), row("test-A", 1)], ["test-A", "001"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["columns"], list(SUBMISSION_COLUMNS))

    def test_reject_duplicate_missing_and_extra_studies(self):
        cases = [([row("a"), row("a")], ["a", "b"]), ([row("a")], ["a", "b"]), ([row("a"), row("c")], ["a", "b"]), ([row("a")], ["a", "a"]), ([], ["a"]), ([row("a")], [])]
        for rows, ids in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(ContractError):
                validate_submission(rows, ids)

    def test_reject_header_order_missing_or_extra_columns(self):
        reversed_columns = (STUDY_ID, *reversed(TARGETS))
        reordered_row = {column: row()[column] for column in reversed_columns}
        with self.assertRaises(ContractError):
            validate_submission([reordered_row], ["001"])
        with self.assertRaises(ContractError):
            validate_submission([row()], ["001"], reversed_columns)
        missing = row()
        del missing["ACL"]
        extra = {**row(), "private_target": 0.5}
        for invalid_row in (missing, extra):
            with self.assertRaises(ContractError):
                validate_submission([invalid_row], ["001"], SUBMISSION_COLUMNS)

    def test_reject_nonmapping_row_with_a_contract_error(self):
        with self.assertRaises(ContractError):
            validate_submission([None], ["001"])

    def test_reject_nonfinite_out_of_range_bool_and_text_probabilities(self):
        for probability in (float("nan"), float("inf"), float("-inf"), -0.01, 1.01, True, False, "0.5", None, 10 ** 1000):
            with self.subTest(probability=type(probability).__name__), self.assertRaises(ContractError):
                validate_submission([row(value=probability)], ["001"])

    def test_invalid_identifier_never_changes_identity(self):
        for uid in ("001 ", "../001", "x\\001"):
            with self.assertRaises(ContractError):
                validate_submission([row(uid)], [uid])

    def test_write_preserves_exact_id_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "submission.csv"
            result = write_submission([row("001")], ["001"], path)
            self.assertGreater(result["size_bytes"], 0)
            original = path.read_bytes()
            with path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, list(SUBMISSION_COLUMNS))
                self.assertEqual(next(reader)[STUDY_ID], "001")
            with self.assertRaises(FileExistsError):
                write_submission([row("001", 0.8)], ["001"], path)
            self.assertEqual(path.read_bytes(), original)

    def test_failed_validation_does_not_create_an_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "submission.csv"
            with self.assertRaises(ContractError):
                write_submission([row(value=float("nan"))], ["001"], path)
            self.assertFalse(path.exists())

    def test_reject_protected_output_ancestors_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            for component in (".git", ".kaggle", "credentials", ".env.private"):
                path = Path(temporary) / component / "submission.csv"
                with self.subTest(component=component), self.assertRaises(InputError):
                    write_submission([row()], ["001"], path)
                self.assertFalse(path.exists())
                self.assertFalse(path.parent.exists())

    def test_reject_simulated_junction_ancestor_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            ancestor = Path(temporary) / "linked"
            ancestor.mkdir()
            destination = ancestor / "submission.csv"
            original_lstat = Path.lstat

            def inspected_lstat(path):
                if path == ancestor:
                    return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                return original_lstat(path)

            with patch.object(Path, "lstat", inspected_lstat), self.assertRaises(InputError):
                write_submission([row()], ["001"], destination)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
