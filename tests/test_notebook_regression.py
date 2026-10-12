"""Offline regressions for generated cell order and embedded class identity."""

from __future__ import annotations

import ast
from contextlib import redirect_stdout
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from rsna_knee.contracts import TARGETS
from rsna_knee.notebook import PACKAGE_NAME, SOURCE_MODULES, build_notebook, notebook_bytes, write_notebook
from rsna_knee.inputs import InputError


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "rsna_knee"
CELL_ORDER = (
    ("audit-overview", "markdown"),
    ("embedded-package", "code"),
    ("audit-settings", "code"),
    ("run-preflight", "code"),
    ("write-evidence", "code"),
)
MANIFEST_NAMES = (
    "train.csv", "train_series.csv", "test.csv", "test_series.csv",
    "sample_submission.csv",
)


def _source(cell: dict) -> str:
    return "".join(cell["source"])


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def _write_csv(root: Path, filename: str, columns: list[str], rows: list[dict]) -> None:
    with (root / filename).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(base: Path, inspect_csv: bool) -> tuple[Path, dict]:
    root = base / "input" / "synthetic-competition"
    root.mkdir(parents=True)
    if not inspect_csv:
        # These cannot pass CSV inspection; discovery must only inspect metadata.
        for filename in MANIFEST_NAMES:
            (root / filename).write_bytes(b"synthetic discovery marker\xff\n")
    else:
        first = {target: ("0", "1", "")[index % 3] for index, target in enumerate(TARGETS)}
        second = {target: ("1", "0", "")[index % 3] for index, target in enumerate(TARGETS)}
        _write_csv(root, "train.csv", ["StudyInstanceUID", "Report", *TARGETS], [
            {"StudyInstanceUID": "0001", "Report": "Synthetic training report one.", **first},
            {"StudyInstanceUID": "0002", "Report": "Synthetic training report two.", **second},
        ])
        _write_csv(root, "test.csv", ["StudyInstanceUID"], [{"StudyInstanceUID": "0003"}])
        series_columns = [
            "StudyInstanceUID", "SeriesInstanceUID", "Anatomical_Plane",
            "Fluid_Sensitive", "Fat_Suppression",
        ]
        def series(study: str, uid: str) -> dict:
            return dict(zip(series_columns, (study, uid, "Synthetic", "1", "0")))
        _write_csv(root, "train_series.csv", series_columns, [
            series("0001", "series.train.1"), series("0002", "series.train.2"),
        ])
        _write_csv(root, "test_series.csv", series_columns, [series("0003", "series.test.1")])
        _write_csv(root, "sample_submission.csv", ["StudyInstanceUID", *TARGETS], [
            {"StudyInstanceUID": "0003", **{target: 0.5 for target in TARGETS}},
        ])
    for name in ("train_series", "test_series"):
        (root / name).mkdir()
    return root, {
        "input_base": str(base / "input"), "output_dir": str(base / "evidence"),
        "inspect_csv": inspect_csv,
    }


class NotebookRegressionTests(unittest.TestCase):
    def tearDown(self) -> None:
        for name in tuple(sys.modules):
            if name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + "."):
                del sys.modules[name]
        sys.meta_path[:] = [
            finder for finder in sys.meta_path
            if getattr(finder, "package_name", None) != PACKAGE_NAME
        ]

    def execute(self, notebook: dict, namespace: dict | None = None, ids=None) -> dict:
        namespace = {"__name__": "__main__"} if namespace is None else namespace
        with redirect_stdout(io.StringIO()), \
                mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden")):
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code" and (ids is None or cell["id"] in ids):
                    exec(compile(_source(cell), f"<regression:{cell['id']}>", "exec"), namespace)
        return namespace

    def assert_report(self, namespace: dict, inspect_csv: bool) -> None:
        report = namespace["audit_report"]
        self.assertIs(type(namespace["audit_config"]), namespace["AuditConfig"])
        self.assertIs(type(namespace["audit_config"]), namespace["run_preflight"].__globals__["AuditConfig"])
        self.assertEqual(report["schema"], "rsna-knee-preflight-v1")
        self.assertEqual(report["phase"], "INITIAL_SCAFFOLD")
        self.assertEqual(report["configuration"]["inspect_csv"], inspect_csv)
        self.assertEqual(report["phase_b_status"], "NOT_RUN")
        self.assertFalse(report["phase1_pass"])
        self.assertFalse(report["training_ready"])
        self.assertFalse(report["runtime"]["gpu_used"])
        self.assertIsNone(report["model_metrics"])
        self.assertFalse(report["discovery"]["dicom_contents_read"])
        self.assertEqual(report["source_identity"]["kind"], "embedded-notebook")
        if inspect_csv:
            self.assertEqual(report["status"], "CONTRACTS_VALID")
            self.assertEqual(report["csv_contracts"]["status"], "VALID")
            self.assertTrue(report["discovery"]["csv_read_completed"])
            stats = report["csv_contracts"]["train"]["target_statistics"]
            self.assertEqual((stats["ACL"]["negative"], stats["ACL"]["positive"]), (1, 1))
            self.assertEqual(stats["Medial Meniscus"]["unknown"], 2)
            self.assertEqual(stats["Medial Meniscus"]["negative"], 0)
        else:
            self.assertEqual(report["status"], "MANIFESTS_LOCATED")
            self.assertEqual(report["csv_contracts"]["status"], "NOT_REQUESTED")
            self.assertFalse(report["discovery"]["csv_contents_read"])
        self.assertEqual(json.loads(namespace["report_path"].read_text(encoding="utf-8")), report)

    def test_exact_cell_order_and_one_validated_configuration(self) -> None:
        notebook = build_notebook(SOURCE_ROOT)
        self.assertEqual([(cell["id"], cell["cell_type"]) for cell in notebook["cells"]], list(CELL_ORDER))
        assignments = {"CONFIG": [], "audit_config": []}
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            for statement in ast.parse(_source(cell)).body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if isinstance(target, ast.Name) and target.id in assignments:
                            assignments[target.id].append((cell["id"], statement.value))
        for name, found in assignments.items():
            self.assertEqual(len(found), 1, f"Expected exactly one top-level {name} assignment")
            self.assertEqual(found[0][0], "audit-settings")
        factory = assignments["audit_config"][0][1]
        self.assertIsInstance(factory, ast.Call)
        self.assertEqual(ast.dump(factory.func), ast.dump(ast.parse("AuditConfig.from_dict", mode="eval").body))
        self.assertEqual(len(factory.args), 1)
        self.assertIsInstance(factory.args[0], ast.Name)
        self.assertEqual(factory.args[0].id, "CONFIG")
        self.assertEqual(factory.keywords, [])
        self.assertIs(ast.literal_eval(assignments["CONFIG"][0][1])["inspect_csv"], False)

    def test_builder_cli_controls_csv_mode_without_cell_edits(self) -> None:
        script = SOURCE_ROOT.parents[1] / "scripts" / "build_audit_notebook.py"
        spec = importlib.util.spec_from_file_location("rsna_builder_cli_regression", script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            configured = project / "config.json"
            configured.write_text(json.dumps({"inspect_csv": True}), encoding="utf-8")
            cases = (([], False), (["--inspect-csv"], True),
                     (["--config", str(configured), "--no-inspect-csv"], False))
            for number, (flags, expected) in enumerate(cases):
                output = project / "notebooks" / f"audit_{number}.ipynb"
                arguments = [str(script), "--output", str(output), *flags]
                # Exercise the real parser, builder and writer with a temporary
                # output root and reviewed source files; no checkout artifacts.
                with self.subTest(flags=flags), mock.patch.object(builder, "PROJECT_ROOT", project), \
                        mock.patch.object(builder, "build_notebook", side_effect=lambda root, settings: build_notebook(SOURCE_ROOT, settings)), \
                        mock.patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()):
                    self.assertEqual(builder.main(), 0)
                    notebook = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual([(c["id"], c["cell_type"]) for c in notebook["cells"]], list(CELL_ORDER))
                    tree = ast.parse(_source(notebook["cells"][2]))
                    settings = ast.literal_eval(next(node.value for node in tree.body if isinstance(node, ast.Assign) and node.targets[0].id == "CONFIG"))
                    self.assertIs(settings["inspect_csv"], expected)

    def test_writer_rejects_duplicate_reordered_and_unvalidated_configuration(self) -> None:
        original = build_notebook(SOURCE_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "notebooks"
            for problem in ("duplicate", "reordered", "factory"):
                notebook = json.loads(json.dumps(original))
                if problem == "duplicate":
                    notebook["cells"].append(notebook["cells"][2])
                elif problem == "reordered":
                    notebook["cells"][2], notebook["cells"][3] = notebook["cells"][3], notebook["cells"][2]
                else:
                    notebook["cells"][2]["source"] = ["CONFIG = {}\naudit_config = CONFIG\n"]
                with self.subTest(problem=problem), self.assertRaises(InputError):
                    write_notebook(notebook, output_root / "invalid.ipynb", output_root)
                self.assertFalse(output_root.exists())

    def test_stale_report_is_not_written_after_configuration_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, settings = _fixture(Path(directory), True)
            notebook = build_notebook(SOURCE_ROOT, settings)
            namespace = self.execute(notebook, ids={"embedded-package", "audit-settings", "run-preflight"})
            namespace["audit_config"] = namespace["AuditConfig"].from_dict({**settings, "inspect_csv": False})
            with self.assertRaisesRegex(RuntimeError, "report is stale"):
                self.execute(notebook, namespace, ids={"write-evidence"})
            self.assertFalse((Path(directory) / "evidence").exists())

    def test_clean_execution_respects_both_inspection_modes_and_preserves_inputs(self) -> None:
        for inspect_csv in (False, True):
            with self.subTest(inspect_csv=inspect_csv), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root, settings = _fixture(base, inspect_csv)
                before = _hashes(root)
                notebook = build_notebook(SOURCE_ROOT, settings)
                namespace = self.execute(notebook)
                self.assert_report(namespace, inspect_csv)
                self.assertEqual(_hashes(root), before)
                self.assertEqual(
                    namespace["audit_report"]["source_identity"]["source_sha256"],
                    notebook["metadata"]["rsna_audit_bundle"]["source_sha256"],
                )
                self.assertEqual(list((base / "evidence").iterdir()), [namespace["report_path"]])

    def test_same_source_loader_rerun_preserves_validated_configuration_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, settings = _fixture(Path(directory), True)
            before = _hashes(root)
            notebook = build_notebook(SOURCE_ROOT, settings)
            namespace = self.execute(notebook, ids={"embedded-package", "audit-settings"})
            original_config = namespace["audit_config"]
            original_class = type(original_config)
            self.execute(notebook, namespace, ids={"embedded-package"})
            self.assertIs(namespace["audit_config"], original_config)
            self.assertIs(namespace["AuditConfig"], original_class)
            self.assertIs(namespace["run_preflight"].__globals__["AuditConfig"], original_class)
            self.execute(notebook, namespace, ids={"run-preflight", "write-evidence"})
            self.assert_report(namespace, True)
            self.assertEqual(_hashes(root), before)

    def test_changed_source_requires_rerunning_configuration_with_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, settings = _fixture(base, True)
            before = _hashes(root)
            first = build_notebook(SOURCE_ROOT, settings)
            namespace = self.execute(first, ids={"embedded-package", "audit-settings"})
            changed_sources = base / "changed-source"
            changed_sources.mkdir()
            for module in SOURCE_MODULES:
                raw = (SOURCE_ROOT / (module + ".py")).read_bytes()
                if module == "config":
                    raw += b"\n# Synthetic version change for stale-configuration regression.\n"
                (changed_sources / (module + ".py")).write_bytes(raw)
            changed = build_notebook(changed_sources, settings)
            self.assertNotEqual(
                first["metadata"]["rsna_audit_bundle"]["bundle_sha256"],
                changed["metadata"]["rsna_audit_bundle"]["bundle_sha256"],
            )
            self.execute(changed, namespace, ids={"embedded-package"})
            with self.assertRaisesRegex(RuntimeError, r"(?i)(rerun|re-run|run again).*config|config.*(rerun|re-run|run again)"):
                self.execute(changed, namespace, ids={"run-preflight"})
            self.assertFalse((base / "evidence").exists())
            self.execute(changed, namespace, ids={"audit-settings", "run-preflight", "write-evidence"})
            self.assert_report(namespace, True)
            self.assertEqual(_hashes(root), before)

    def test_isolated_clean_python_executes_both_modes_without_checkout_or_network(self) -> None:
        runner = '''import json, socket, sys
def deny_network(*args, **kwargs):
    raise AssertionError("Network forbidden during synthetic notebook execution")
socket.socket.connect = deny_network
socket.create_connection = deny_network
notebook = json.load(sys.stdin)
namespace = {"__name__": "__main__"}
for cell in notebook["cells"]:
    if cell["cell_type"] == "code":
        exec(compile("".join(cell["source"]), "<isolated:" + cell["id"] + ">", "exec"), namespace)
assert "rsna_knee" not in sys.modules
assert type(namespace["audit_config"]) is namespace["run_preflight"].__globals__["AuditConfig"]
report = namespace["audit_report"]
assert report["phase_b_status"] == "NOT_RUN"
assert report["training_ready"] is False
assert report["model_metrics"] is None
assert report["runtime"]["gpu_used"] is False
assert report["discovery"]["dicom_contents_read"] is False
if namespace["CONFIG"]["inspect_csv"]:
    assert report["status"] == "CONTRACTS_VALID"
    assert report["csv_contracts"]["status"] == "VALID"
    assert report["csv_contracts"]["train"]["target_statistics"]["Medial Meniscus"]["unknown"] == 2
else:
    assert report["status"] == "MANIFESTS_LOCATED"
    assert report["csv_contracts"]["status"] == "NOT_REQUESTED"
    assert report["discovery"]["csv_contents_read"] is False
assert json.loads(namespace["report_path"].read_text(encoding="utf-8")) == report
'''
        for inspect_csv in (False, True):
            with self.subTest(inspect_csv=inspect_csv), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root, settings = _fixture(base, inspect_csv)
                before = _hashes(root)
                notebook = build_notebook(SOURCE_ROOT, settings)
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", runner], input=notebook_bytes(notebook),
                    cwd=directory, capture_output=True, timeout=30, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                self.assertEqual(_hashes(root), before)
                self.assertEqual(len(list((base / "evidence").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
