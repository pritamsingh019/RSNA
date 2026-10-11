"""Clean execution checks use tiny synthetic manifests, never competition data."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notebook_builder", ROOT / "scripts" / "build_notebook.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NotebookTests(unittest.TestCase):
    def test_generated_notebook_is_current_and_deterministic(self):
        saved = json.loads((ROOT / "notebooks" / "kaggle_access_check.ipynb").read_text(encoding="utf-8"))
        self.assertEqual(saved, builder.build_notebook())
        self.assertEqual(builder.build_notebook(), builder.build_notebook())
        for cell in saved["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
        source = next(cell for cell in saved["cells"] if cell["id"] == "access-module")
        self.assertEqual(hashlib.sha256("".join(source["source"]).encode()).hexdigest(),
                         saved["metadata"]["rsna_knee"]["embedded_cell_sha256"])

    def execute_clean(self, input_base, output_dir):
        # Each execution gets a fresh namespace. Only the two deployment paths change.
        namespace = {"__name__": "__notebook__"}
        with contextlib.redirect_stdout(io.StringIO()):
            for cell in builder.build_notebook()["cells"]:
                if cell["cell_type"] == "code":
                    exec(compile("".join(cell["source"]), cell["id"], "exec"), namespace)
                    if cell["id"] == "configuration":
                        namespace["INPUT_BASE"] = input_base
                        namespace["OUTPUT_DIR"] = output_dir
        return namespace["report"], namespace["report_path"]

    def test_clean_execution_without_attached_input_records_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            report, path = self.execute_clean(base / "missing", base / "outputs")
            self.assertEqual(report["discovery"]["status"], "INPUT_BASE_MISSING")
            self.assertFalse(report["phase1_pass"])
            self.assertFalse(report["training_started"])
            self.assertTrue(path.is_file())

    def test_clean_execution_preserves_unknown_labels_and_still_requires_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "input" / "competitions" / "synthetic"
            source.mkdir(parents=True)
            names = ("train.csv", "train_series.csv", "test.csv", "test_series.csv", "sample_submission.csv")
            for name in names:
                (source / name).write_text("StudyInstanceUID,ACL\nsynthetic,UNKNOWN\n", encoding="utf-8")
            before = {path.name: path.read_bytes() for path in source.iterdir()}
            report, artifact = self.execute_clean(base / "input", base / "output")
            self.assertEqual(report["discovery"]["status"], "MANIFESTS_LOCATED_AUDIT_PENDING")
            self.assertFalse(report["discovery"]["csv_contents_inspected"])
            self.assertFalse(report["discovery"]["dataset_identity_verified"])
            self.assertFalse(report["phase1_pass"])
            self.assertIsNone(report["model_metrics"])
            self.assertEqual(before, {path.name: path.read_bytes() for path in source.iterdir()})
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    unittest.main()
