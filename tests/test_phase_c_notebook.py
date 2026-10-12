"""Self-contained Phase C notebook regressions using synthetic CSVs only."""

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

from rsna_knee.config import ConfigError
from rsna_knee.contracts import TARGETS
from rsna_knee.inputs import InputError
from rsna_knee.notebook import notebook_bytes
from rsna_knee.phase_c_notebook import (
    PHASE_C_LAYOUT, PHASE_C_MODULES, PHASE_C_PACKAGE,
    build_phase_c_notebook, validate_phase_c_notebook, write_phase_c_notebook,
)


PROJECT = Path(__file__).resolve().parents[1]
SOURCES = PROJECT / "src" / "rsna_knee"
PRIVATE_REPORT = "PrivateReportSentinel ACL tear. No fracture."
PRIVATE_UID = "private.synthetic.study."


def fixture(base):
    root = base / "input" / "competition"
    root.mkdir(parents=True)
    def write(name, fields, rows):
        with (root / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    studies = [PRIVATE_UID + str(i) for i in range(1, 4)]
    write("train.csv", ["StudyInstanceUID", "Report", *TARGETS], [
        {"StudyInstanceUID": studies[0], "Report": PRIVATE_REPORT, **{target: "1" for target in TARGETS}},
        {"StudyInstanceUID": studies[1], "Report": "PrivateNegativeSentinel ACL intact. No fracture.", **{target: "0" for target in TARGETS}},
        {"StudyInstanceUID": studies[2], "Report": "", **{target: "" for target in TARGETS}},
    ])
    test_study = PRIVATE_UID + "4"
    write("test.csv", ["StudyInstanceUID"], [{"StudyInstanceUID": test_study}])
    fields = ["StudyInstanceUID", "SeriesInstanceUID", "Anatomical_Plane", "Fluid_Sensitive", "Fat_Suppression"]
    def series(study, number):
        return dict(zip(fields, (study, "private.synthetic.series." + str(number), "axial", "1", "0")))
    write("train_series.csv", fields, [series(study, i) for i, study in enumerate(studies)])
    write("test_series.csv", fields, [series(test_study, 4)])
    write("sample_submission.csv", ["StudyInstanceUID", *TARGETS], [{"StudyInstanceUID": test_study, **{target: "0.5" for target in TARGETS}}])
    # Phase C must never need image files, decoders or DICOM dependencies.
    for name in ("train_series", "test_series"):
        (root / name).mkdir()
    return root, {"input_base": str(base / "input"), "output_dir": str(base / "evidence")}


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def configuration(notebook):
    return ast.literal_eval(next(node.value for node in ast.parse("".join(notebook["cells"][2]["source"])).body
                                 if isinstance(node, ast.Assign) and node.targets[0].id == "CONFIG"))


class PhaseCNotebookTests(unittest.TestCase):
    def tearDown(self):
        for name in tuple(sys.modules):
            if name == PHASE_C_PACKAGE or name.startswith(PHASE_C_PACKAGE + "."):
                del sys.modules[name]
        sys.meta_path[:] = [finder for finder in sys.meta_path if getattr(finder, "package_name", None) != PHASE_C_PACKAGE]

    def execute(self, notebook, namespace=None, only=None):
        namespace = namespace if namespace is not None else {"__name__": "__main__"}
        with redirect_stdout(io.StringIO()), \
                mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden")):
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code" and (only is None or cell["id"] in only):
                    exec(compile("".join(cell["source"]), "<synthetic:" + cell["id"] + ">", "exec"), namespace)
        return namespace

    def test_layout_single_validated_config_hashes_limits_and_determinism(self):
        notebook = build_phase_c_notebook(SOURCES)
        self.assertEqual(tuple((cell["id"], cell["cell_type"]) for cell in notebook["cells"]), PHASE_C_LAYOUT)
        self.assertEqual(notebook_bytes(notebook), notebook_bytes(build_phase_c_notebook(SOURCES)))
        assignments = {"CONFIG": [], "phase_c_config": []}
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            for node in ast.walk(ast.parse("".join(cell["source"]))):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in assignments:
                            assignments[target.id].append((cell["id"], node.value))
        for items in assignments.values():
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0][0], "phase-c-settings")
        self.assertEqual(ast.unparse(assignments["phase_c_config"][0][1]), "PhaseCConfig.from_dict(CONFIG)")
        settings = configuration(notebook)
        self.assertIs(settings["inspect_csv"], True)
        self.assertEqual(settings["max_file_bytes"], 32 * 1024 * 1024)
        self.assertEqual(settings["max_report_bytes"], 1024 * 1024)
        self.assertEqual(settings["max_report_chars"], 20000)
        self.assertEqual(settings["max_report_clauses"], 256)
        self.assertEqual(settings["max_extraction_seconds"], 120)
        metadata = notebook["metadata"]["rsna_audit_bundle"]
        self.assertEqual(metadata["package_name"], PHASE_C_PACKAGE)
        self.assertEqual(set(metadata["source_sha256"]), {name + ".py" for name in PHASE_C_MODULES})
        loader_tree = ast.parse("".join(notebook["cells"][1]["source"]))
        bundle_call = next(node.value for node in loader_tree.body
                           if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                           and node.targets[0].id == "_RSNA_BUNDLE")
        bundle_json = ast.literal_eval(bundle_call.args[0])
        bundle = json.loads(bundle_json)
        self.assertEqual(hashlib.sha256(bundle_json.encode("utf-8")).hexdigest(), metadata["bundle_sha256"])
        self.assertEqual(bundle["sha256"], metadata["source_sha256"])
        self.assertNotIn("dicom_audit", PHASE_C_MODULES)
        self.assertNotIn("phase_b", PHASE_C_MODULES)
        for filename, digest in metadata["source_sha256"].items():
            self.assertEqual(hashlib.sha256((SOURCES / filename).read_bytes()).hexdigest(), digest)
            self.assertEqual(bundle["sources"][filename[:-3]].encode("utf-8"), (SOURCES / filename).read_bytes())
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])

    def test_artifact_matches_builder_and_notebook_format(self):
        notebook = build_phase_c_notebook(SOURCES)
        self.assertEqual((PROJECT / "notebooks" / "kaggle_phase_c_report_audit.ipynb").read_bytes(), notebook_bytes(notebook))
        if importlib.util.find_spec("nbformat"):
            import nbformat
            nbformat.validate(nbformat.from_dict(notebook))

    def test_existing_audit_artifacts_and_configuration_modes_are_preserved(self):
        from rsna_knee.notebook import build_notebook
        from rsna_knee.phase_b_notebook import build_phase_b_notebook
        cases = (
            ("kaggle_audit.ipynb", build_notebook(SOURCES), 5, False),
            ("kaggle_audit_csv.ipynb", build_notebook(SOURCES, {"inspect_csv": True}), 5, True),
            ("kaggle_phase_b_audit.ipynb", build_phase_b_notebook(SOURCES), 6, True),
        )
        for filename, expected, cell_count, csv_mode in cases:
            with self.subTest(filename=filename):
                raw = (PROJECT / "notebooks" / filename).read_bytes()
                self.assertEqual(raw, notebook_bytes(expected))
                self.assertEqual(len(expected["cells"]), cell_count)
                self.assertIs(configuration(expected)["inspect_csv"], csv_mode)

    def test_configuration_types_and_modes_rejected(self):
        for settings in ({"inspect_csv": False}, {"inspect_csv": "true"}, {"max_report_chars": True}, {"unexpected": 1}):
            with self.subTest(settings=settings), self.assertRaises(ConfigError):
                build_phase_c_notebook(SOURCES, settings)
        with self.assertRaises(TypeError):
            build_phase_c_notebook(SOURCES, [])

    def test_writer_rejects_duplicate_reordered_unvalidated_and_wrong_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "notebooks"
            for problem in ("duplicate", "reorder", "factory", "mode"):
                notebook = build_phase_c_notebook(SOURCES)
                if problem == "duplicate":
                    notebook["cells"].append(notebook["cells"][2])
                elif problem == "reorder":
                    notebook["cells"][2], notebook["cells"][3] = notebook["cells"][3], notebook["cells"][2]
                elif problem == "factory":
                    notebook["cells"][2]["source"] = ["CONFIG = {}\nphase_c_config = CONFIG\n"]
                else:
                    notebook["cells"][2]["source"] = ["CONFIG = {'inspect_csv': False}\nphase_c_config = PhaseCConfig.from_dict(CONFIG)\n"]
                with self.subTest(problem=problem), self.assertRaises((InputError, ConfigError)):
                    write_phase_c_notebook(notebook, root / "bad.ipynb", root)
                self.assertFalse(root.exists())

    def test_writer_preserves_unrelated_files_and_rejects_outside_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "notebooks"
            root.mkdir()
            unrelated = root / "user.ipynb"
            original = b"private user's unrelated notebook"
            unrelated.write_bytes(original)
            notebook = build_phase_c_notebook(SOURCES)
            for output in (unrelated, base / "outside.ipynb", root / "file.txt"):
                with self.subTest(output=output.name), self.assertRaises(InputError):
                    write_phase_c_notebook(notebook, output, root)
            self.assertEqual(unrelated.read_bytes(), original)
            path = write_phase_c_notebook(notebook, root / "generated.ipynb", root)
            before = path.read_bytes()
            self.assertEqual(write_phase_c_notebook(notebook, path, root).read_bytes(), before)

    def test_same_loader_preserves_class_identity_and_reports_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            before = hashes(root)
            notebook = build_phase_c_notebook(SOURCES, settings)
            namespace = self.execute(notebook, only={"embedded-package", "phase-c-settings"})
            original = namespace["phase_c_config"]
            self.execute(notebook, namespace, only={"embedded-package"})
            self.assertIs(type(original), namespace["PhaseCConfig"])
            self.assertIs(namespace["phase_c_config"], original)
            self.assertIs(type(original), namespace["run_phase_c"].__globals__["PhaseCConfig"])
            self.execute(notebook, namespace, only={"csv-preflight", "run-phase-c", "write-evidence"})
            report = namespace["phase_c_report"]
            self.assertEqual(report["phase"], "PHASE_C")
            self.assertFalse(report["training_ready"])
            self.assertFalse(report["split_approved"])
            self.assertIsNone(report["model_metrics"])
            self.assertEqual(json.loads(namespace["report_path"].read_text(encoding="utf-8")), report)
            raw = json.dumps(report, allow_nan=False)
            for sentinel in (PRIVATE_REPORT, PRIVATE_UID, "PrivateNegativeSentinel", "private.synthetic.series"):
                self.assertNotIn(sentinel, raw)
            self.assertEqual(hashes(root), before)

    def test_missing_configuration_and_stale_preflight_or_report_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            _, settings = fixture(Path(directory))
            notebook = build_phase_c_notebook(SOURCES, settings)
            namespace = self.execute(notebook, only={"embedded-package"})
            for cell in ({"csv-preflight"}, {"run-phase-c"}, {"write-evidence"}):
                with self.subTest(cell=cell), self.assertRaises(RuntimeError):
                    self.execute(notebook, namespace, only=cell)
            self.execute(notebook, namespace, only={"phase-c-settings", "csv-preflight"})
            original = namespace["phase_c_config"]
            namespace["phase_c_config"] = original.with_overrides(max_csv_rows=99999)
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.execute(notebook, namespace, only={"run-phase-c"})
            namespace["phase_c_config"] = original
            self.execute(notebook, namespace, only={"run-phase-c"})
            namespace["phase_c_config"] = original.with_overrides(max_report_chars=19999)
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.execute(notebook, namespace, only={"write-evidence"})
            self.assertFalse(Path(settings["output_dir"]).exists())

    def test_source_checksum_tamper_refused_before_import(self):
        notebook = build_phase_c_notebook(SOURCES)
        loader = "".join(notebook["cells"][1]["source"])
        bundle_call = next(node.value for node in ast.parse(loader).body
                           if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                           and node.targets[0].id == "_RSNA_BUNDLE")
        original = ast.literal_eval(bundle_call.args[0])
        bundle = json.loads(original)
        bundle["sources"]["phase_c_config"] += "\n# checksum tamper\n"
        changed = loader.replace(repr(original), repr(json.dumps(bundle, sort_keys=True, ensure_ascii=True, separators=(",", ":"))), 1)
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            exec(changed, {"__name__": "__main__"})
        self.assertNotIn(PHASE_C_PACKAGE, sys.modules)

    def test_changed_source_requires_recreating_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _, settings = fixture(base)
            first = build_phase_c_notebook(SOURCES, settings)
            namespace = self.execute(first, only={"embedded-package", "phase-c-settings"})
            changed_sources = base / "changed-source"
            changed_sources.mkdir()
            for module in PHASE_C_MODULES:
                raw = (SOURCES / (module + ".py")).read_bytes()
                if module == "phase_c_config":
                    raw += b"\n# Synthetic bundle version change.\n"
                (changed_sources / (module + ".py")).write_bytes(raw)
            changed = build_phase_c_notebook(changed_sources, settings)
            self.assertNotEqual(first["metadata"]["rsna_audit_bundle"]["bundle_sha256"], changed["metadata"]["rsna_audit_bundle"]["bundle_sha256"])
            self.execute(changed, namespace, only={"embedded-package"})
            with self.assertRaisesRegex(RuntimeError, "stale PhaseCConfig"):
                self.execute(changed, namespace, only={"csv-preflight"})
            self.execute(changed, namespace, only={"phase-c-settings", "csv-preflight", "run-phase-c", "write-evidence"})
            self.assertIs(type(namespace["phase_c_config"]), namespace["PhaseCConfig"])

    def test_cli_secure_config_and_generated_output(self):
        script = PROJECT / "scripts" / "build_phase_c_notebook.py"
        spec = importlib.util.spec_from_file_location("rsna_phase_c_builder_cli_regression", script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            configured = project / "config.json"
            configured.write_text(json.dumps({"max_report_chars": 1234, "max_report_clauses": 20}), encoding="utf-8")
            output = project / "notebooks" / "phase_c.ipynb"
            with mock.patch.object(builder, "PROJECT_ROOT", project), \
                    mock.patch.object(builder, "build_phase_c_notebook", side_effect=lambda root, settings: build_phase_c_notebook(SOURCES, settings)), \
                    mock.patch.object(sys, "argv", [str(script), "--config", str(configured), "--output", str(output)]), redirect_stdout(io.StringIO()):
                self.assertEqual(builder.main(), 0)
            notebook = json.loads(output.read_text(encoding="utf-8"))
            validate_phase_c_notebook(notebook)
            self.assertEqual(configuration(notebook)["max_report_chars"], 1234)
            self.assertTrue(configuration(notebook)["inspect_csv"])
            original = output.read_bytes()
            configured.write_text('{"max_report_chars": 1234, "max_report_chars": 1234}', encoding="utf-8")
            with mock.patch.object(builder, "PROJECT_ROOT", project), \
                    mock.patch.object(sys, "argv", [str(script), "--config", str(configured), "--output", str(output)]), \
                    self.assertRaises(ConfigError):
                builder.main()
            self.assertEqual(output.read_bytes(), original)

    def test_isolated_offline_execution_no_repository_import_or_private_output(self):
        runner = '''import json, socket, sys
def deny_network(*args, **kwargs):
    raise AssertionError("Network forbidden during synthetic execution")
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
socket.getaddrinfo = deny_network
notebook = json.load(sys.stdin)
namespace = {"__name__": "__main__"}
for cell in notebook["cells"]:
    if cell["cell_type"] == "code":
        exec(compile("".join(cell["source"]), "<offline:" + cell["id"] + ">", "exec"), namespace)
assert "rsna_knee" not in sys.modules
assert not any(name.endswith(".dicom_audit") or name == "pydicom" for name in sys.modules)
report = namespace["phase_c_report"]
assert report["phase"] == "PHASE_C"
assert report["training_ready"] is False and report["split_approved"] is False
assert report["model_metrics"] is None
assert report["runtime"]["gpu_used"] is False
assert type(namespace["phase_c_config"]) is namespace["run_phase_c"].__globals__["PhaseCConfig"]
assert namespace["CONFIG"]["inspect_csv"] is True
assert namespace["csv_preflight"]["csv_contracts"]["status"] == "VALID"
assert json.loads(namespace["report_path"].read_text(encoding="utf-8")) == report
assert namespace["report_path"].stat().st_size <= namespace["phase_c_config"].max_report_bytes
for key in ("reports", "study_ids", "predictions", "candidates", "train_rows"):
    assert key not in namespace
for sentinel in ("PrivateReportSentinel", "PrivateNegativeSentinel", "private.synthetic.study", "private.synthetic.series"):
    assert sentinel not in json.dumps(report, allow_nan=False)
'''
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            before = hashes(root)
            result = subprocess.run([sys.executable, "-I", "-B", "-c", runner],
                                    input=notebook_bytes(build_phase_c_notebook(SOURCES, settings)),
                                    cwd=directory, capture_output=True, timeout=45, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(hashes(root), before)
            for sentinel in (PRIVATE_REPORT, PRIVATE_UID, "PrivateNegativeSentinel", "private.synthetic.series"):
                self.assertNotIn(sentinel, result.stdout.decode("utf-8", errors="replace"))
            self.assertEqual(len(list(Path(settings["output_dir"]).glob("*.json"))), 1)

    def test_csv_only_fixture_has_missing_series_not_four_empty_series(self):
        from rsna_knee.phase_b import run_phase_b
        from rsna_knee.phase_b_config import PhaseBConfig

        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            before = hashes(root)
            report = run_phase_b(PhaseBConfig.from_dict(settings))
            inventory = report["checks"]["series_inventory"]["split_statistics"]
            sample = report["checks"]["series_file_sample"]
            self.assertEqual(inventory["train"]["missing_series"], 3)
            self.assertEqual(inventory["test"]["missing_series"], 1)
            self.assertEqual(sample["selected_series"], 0)
            self.assertEqual(sample["empty_series"], 0)
            self.assertEqual(sample["selected_files"], 0)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(hashes(root), before)


if __name__ == "__main__":
    unittest.main()
