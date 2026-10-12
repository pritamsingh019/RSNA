"""Synthetic notebook checks; these do not establish Kaggle/data readiness."""

from __future__ import annotations

from contextlib import redirect_stdout
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from rsna_knee.notebook import (
    PACKAGE_NAME,
    SOURCE_MODULES,
    build_notebook,
    notebook_bytes,
    write_notebook,
)
from rsna_knee.inputs import InputError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "rsna_knee"


class NotebookBundleTests(unittest.TestCase):
    def tearDown(self) -> None:
        # Leave the ordinary source package and all non-bundle imports intact.
        for name in tuple(sys.modules):
            if name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + "."):
                del sys.modules[name]
        sys.meta_path[:] = [
            finder for finder in sys.meta_path
            if getattr(finder, "package_name", None) != PACKAGE_NAME
        ]

    def execute_cells(self, notebook: dict) -> dict:
        namespace = {"__name__": "__main__"}
        ordinary_package = sys.modules["rsna_knee"]
        with redirect_stdout(io.StringIO()):
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code":
                    source = "".join(cell["source"])
                    exec(compile(source, f"<notebook:{cell['id']}>", "exec"), namespace)
        self.assertIs(sys.modules["rsna_knee"], ordinary_package)
        return namespace

    def test_deterministic_bytes_and_stable_cell_ids(self) -> None:
        first = build_notebook(SOURCE_ROOT)
        second = build_notebook(SOURCE_ROOT)
        self.assertEqual(notebook_bytes(first), notebook_bytes(second))
        self.assertEqual(first["nbformat"], 4)
        ids = [cell["id"] for cell in first["cells"]]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in first["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
        serialized = notebook_bytes(first).decode("utf-8")
        self.assertNotIn("generated_at", serialized)
        self.assertNotIn("sys.path", "".join(first["cells"][1]["source"]))

    def test_source_hashes_match_reviewed_allowlist(self) -> None:
        notebook = build_notebook(SOURCE_ROOT)
        recorded = notebook["metadata"]["rsna_audit_bundle"]["source_sha256"]
        self.assertEqual(set(recorded), {module + ".py" for module in SOURCE_MODULES})
        for filename, digest in recorded.items():
            self.assertEqual(digest, hashlib.sha256((SOURCE_ROOT / filename).read_bytes()).hexdigest())
        namespace = {"__name__": "__main__"}
        exec("".join(notebook["cells"][1]["source"]), namespace)
        bundle = namespace["_RSNA_BUNDLE"]
        canonical = json.dumps(bundle, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        self.assertEqual(
            notebook["metadata"]["rsna_audit_bundle"]["bundle_sha256"],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def test_embedded_source_checksum_failure_stops_loading(self) -> None:
        notebook = build_notebook(SOURCE_ROOT)
        loader = "".join(notebook["cells"][1]["source"])
        tree = ast.parse(loader)
        assignment = next(
            statement for statement in tree.body
            if isinstance(statement, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_RSNA_BUNDLE" for target in statement.targets)
        )
        original_json = ast.literal_eval(assignment.value.args[0])
        bundle = json.loads(original_json)
        bundle["sources"]["config"] += "\n# changed without updating the source checksum\n"
        changed_json = json.dumps(bundle, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        changed_loader = loader.replace(repr(original_json), repr(changed_json), 1)
        self.assertNotEqual(loader, changed_loader)
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            exec(changed_loader, {"__name__": "__main__"})

    def test_clean_namespace_execution_for_missing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            notebook = build_notebook(SOURCE_ROOT, {
                "input_base": str(base / "missing"),
                "output_dir": str(base / "evidence"),
            })
            namespace = self.execute_cells(notebook)
            report = namespace["audit_report"]
            self.assertEqual(report["status"], "INPUT_BASE_MISSING")
            self.assertFalse(report["training_ready"])
            self.assertEqual(report["phase_b_status"], "NOT_RUN")
            self.assertEqual(report["source_identity"]["kind"], "embedded-notebook")
            self.assertEqual(
                report["source_identity"]["source_sha256"],
                notebook["metadata"]["rsna_audit_bundle"]["source_sha256"],
            )
            output = Path(namespace["report_path"])
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            self.assertFalse((base / "missing").exists())

    def test_isolated_interpreter_needs_no_checkout_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            notebook = build_notebook(SOURCE_ROOT, {
                "input_base": str(base / "missing"),
                "output_dir": str(base / "evidence"),
            })
            runner = (
                "import json, sys\n"
                "notebook = json.load(sys.stdin)\n"
                "namespace = {'__name__': '__main__'}\n"
                "for cell in notebook['cells']:\n"
                "    if cell['cell_type'] == 'code':\n"
                "        exec(''.join(cell['source']), namespace)\n"
                "assert 'rsna_knee' not in sys.modules\n"
                "assert namespace['audit_report']['status'] == 'INPUT_BASE_MISSING'\n"
                "assert namespace['report_path'].is_file()\n"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", runner],
                input=notebook_bytes(notebook),
                cwd=directory,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_synthetic_discovery_without_csv_or_dicom_processing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            candidate = base / "input" / "synthetic-competition"
            candidate.mkdir(parents=True)
            for filename in (
                "train.csv", "train_series.csv", "test.csv",
                "test_series.csv", "sample_submission.csv",
            ):
                (candidate / filename).write_bytes(b"synthetic discovery marker\n")
            for name in ("train_series", "test_series"):
                (candidate / name).mkdir()
                (candidate / name / "marker.dcm").write_bytes(b"not a DICOM; discovery must not decode")
            original = {
                str(path.relative_to(candidate)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in candidate.rglob("*") if path.is_file()
            }
            notebook = build_notebook(SOURCE_ROOT, {
                "input_base": str(base / "input"),
                "output_dir": str(base / "evidence"),
                "inspect_csv": False,
            })
            namespace = self.execute_cells(notebook)
            report = namespace["audit_report"]
            self.assertEqual(report["status"], "MANIFESTS_LOCATED")
            self.assertEqual(report["csv_contracts"]["status"], "NOT_REQUESTED")
            self.assertFalse(report["training_ready"])
            self.assertEqual(original, {
                str(path.relative_to(candidate)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in candidate.rglob("*") if path.is_file()
            })
            # Package sources remain virtual; audit evidence is the only output.
            self.assertFalse((base / "evidence" / "rsna_knee_bundle").exists())
            self.assertTrue(Path(namespace["report_path"]).is_file())

    def test_builder_rejects_invalid_config_and_missing_sources(self) -> None:
        with self.assertRaises((ValueError, TypeError)):
            build_notebook(SOURCE_ROOT, {"inspect_csv": "yes"})
        with self.assertRaises((ValueError, TypeError)):
            build_notebook(SOURCE_ROOT, {"unknown_setting": 1})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_notebook(Path(directory))

    def test_generated_notebook_matches_current_sources(self) -> None:
        artifact = PROJECT_ROOT / "notebooks" / "kaggle_audit.ipynb"
        self.assertTrue(artifact.is_file(), "Run python scripts/build_audit_notebook.py first")
        self.assertEqual(artifact.read_bytes(), notebook_bytes(build_notebook(SOURCE_ROOT)))

    def test_write_generated_notebook_and_replace_only_its_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "notebooks"
            output = root / "audit.ipynb"
            notebook = build_notebook(SOURCE_ROOT)
            self.assertEqual(write_notebook(notebook, output, root), output)
            self.assertEqual(output.read_bytes(), notebook_bytes(notebook))
            self.assertEqual(write_notebook(notebook, output, root), output)
            self.assertEqual(output.read_bytes(), notebook_bytes(notebook))
            self.assertEqual(list(root.iterdir()), [output])

    def test_output_rejects_outside_protected_and_wrong_extension_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "notebooks"
            notebook = build_notebook(SOURCE_ROOT)
            outside = base / "source.ipynb"
            outside.write_bytes(b"user source remains intact")
            for path in (outside, base / ".git" / "config", root / ".git" / "fake.ipynb", root / "audit.json"):
                with self.subTest(path=path), self.assertRaises(InputError):
                    write_notebook(notebook, path, root)
            self.assertEqual(outside.read_bytes(), b"user source remains intact")
            self.assertFalse(root.exists())
            self.assertFalse((base / ".git").exists())

    def test_existing_unrelated_or_invalid_notebook_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "notebooks"
            root.mkdir()
            output = root / "user.ipynb"
            notebook = build_notebook(SOURCE_ROOT)
            unrelated = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}).encode("utf-8")
            for content in (unrelated, b"not notebook JSON", b'{"metadata":{"rsna_audit_bundle":{}}}'):
                with self.subTest(content=content):
                    output.write_bytes(content)
                    with self.assertRaises(InputError):
                        write_notebook(notebook, output, root)
                    self.assertEqual(output.read_bytes(), content)

    def test_source_byte_budget_still_holds_if_stat_size_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for module in SOURCE_MODULES:
                (root / (module + ".py")).write_bytes(b"#" * 33)
            original_stat = Path.stat

            def stale_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path.suffix == ".py":
                    values = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
                    values["st_size"] = 1
                    return SimpleNamespace(**values)
                return result

            with mock.patch("rsna_knee.notebook.MAX_SOURCE_BYTES", 32), mock.patch.object(Path, "stat", stale_stat):
                with self.assertRaisesRegex(ValueError, "exceeds the bundle limit"):
                    build_notebook(root)

    def test_source_and_output_reject_symbolic_link_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "notebooks"
            root.mkdir()
            other = base / "other"
            other.mkdir()
            linked = root / "linked"
            try:
                linked.symlink_to(other, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Symbolic link creation unavailable: {type(error).__name__}")
            notebook = build_notebook(SOURCE_ROOT)
            with self.assertRaises(InputError):
                write_notebook(notebook, linked / "audit.ipynb", root)
            with self.assertRaises(InputError):
                build_notebook(linked)
            self.assertEqual(list(other.iterdir()), [])

    def test_source_and_output_reject_simulated_reparse_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "notebooks"
            ancestor = root / "reparse"
            ancestor.mkdir(parents=True)
            notebook = build_notebook(SOURCE_ROOT)
            original_lstat = Path.lstat

            def simulated_reparse(path, *args, **kwargs):
                result = original_lstat(path, *args, **kwargs)
                if path == ancestor:
                    return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
                return result

            with mock.patch.object(Path, "lstat", simulated_reparse):
                with self.assertRaises(InputError):
                    write_notebook(notebook, ancestor / "audit.ipynb", root)
                with self.assertRaises(InputError):
                    build_notebook(ancestor)
            self.assertEqual(list(ancestor.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
