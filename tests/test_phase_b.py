"""Phase B synthetic evidence only; no real competition or image measurements."""

from contextlib import redirect_stdout
import ast
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from rsna_knee.config import AuditConfig, ConfigError
from rsna_knee.contracts import TARGETS
from rsna_knee.inputs import InputError, MANIFEST_NAMES
from rsna_knee.notebook import notebook_bytes
from rsna_knee.phase_b import CHECK_STATES, run_phase_b, write_phase_b_report
from rsna_knee.phase_b_config import PhaseBConfig, load_phase_b_config
from rsna_knee.phase_b_notebook import (
    PHASE_B_LAYOUT, PHASE_B_MODULES, PHASE_B_PACKAGE, build_phase_b_notebook, write_phase_b_notebook,
)


PROJECT = Path(__file__).resolve().parents[1]
SOURCES = PROJECT / "src" / "rsna_knee"
PRIVATE_REPORT = "PrivateSentinelReportText Latin देवनागरी"


def fixture(base):
    root = base / "input" / "competition"
    root.mkdir(parents=True)
    def write(name, fields, rows):
        with (root / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    train = [
        {"StudyInstanceUID": "private.study.1", "Report": PRIVATE_REPORT, **{target: "1" for target in TARGETS}},
        {"StudyInstanceUID": "private.study.2", "Report": PRIVATE_REPORT, **{target: "0" for target in TARGETS}},
        {"StudyInstanceUID": "private.study.3", "Report": "", **{target: "" for target in TARGETS}},
    ]
    write("train.csv", ["StudyInstanceUID", "Report", *TARGETS], train)
    write("test.csv", ["StudyInstanceUID"], [{"StudyInstanceUID": "private.study.4"}])
    columns = ["StudyInstanceUID", "SeriesInstanceUID", "Anatomical_Plane", "Fluid_Sensitive", "Fat_Suppression"]
    train_series = [dict(zip(columns, [row["StudyInstanceUID"], "private.series." + str(i + 1), "axial", "1", "0"])) for i, row in enumerate(train)]
    test_series = [dict(zip(columns, ["private.study.4", "private.series.4", "sagittal", "0", "1"]))]
    write("train_series.csv", columns, train_series)
    write("test_series.csv", columns, test_series)
    write("sample_submission.csv", ["StudyInstanceUID", *TARGETS], [{"StudyInstanceUID": "private.study.4", **{target: .5 for target in TARGETS}}])
    for split, rows in [("train", train_series), ("test", test_series)]:
        for row in rows:
            directory = root / (split + "_images") / row["StudyInstanceUID"] / row["SeriesInstanceUID"]
            directory.mkdir(parents=True)
            (directory / "synthetic.dcm").write_bytes(b"Invalid synthetic DICOM; no real pixels")
    settings = {"input_base": str(base / "input"), "output_dir": str(base / "evidence")}
    return root, settings


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}


class PhaseBConfigurationTests(unittest.TestCase):
    def test_defaults_and_inherited_csv_contract(self):
        config = PhaseBConfig()
        self.assertEqual(config, PhaseBConfig.from_dict(config.to_dict()))
        self.assertIs(type(config.preflight_config()), AuditConfig)
        self.assertTrue(config.preflight_config().inspect_csv)
        self.assertEqual(config.max_pixel_decodes, 12)
        with self.assertRaises(ConfigError):
            PhaseBConfig.from_dict({"inspect_csv": False})

    def test_limits_types_unknown_keys_and_duplicate_json(self):
        for overrides in ({"max_pixel_decodes": 13}, {"max_sample_series": True}, {"max_inventory_seconds": .1},
                          {"max_header_reads": 0}, {"max_dicom_file_bytes": 10**12}, {"token": "not-read"}):
            with self.subTest(overrides=overrides), self.assertRaises(ConfigError):
                PhaseBConfig.from_dict(overrides)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"max_pixel_decodes": 1, "max_pixel_decodes": 2}', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_phase_b_config(path)


class PhaseBIntegrationTests(unittest.TestCase):
    def test_schema_privacy_immutability_missing_optional_dependency_and_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            before = hashes(root)
            with mock.patch("rsna_knee.dicom_audit.importlib.import_module", side_effect=ImportError("PrivatePatientSentinel")):
                report = run_phase_b(PhaseBConfig.from_dict(settings))
            self.assertEqual(report["schema"], "rsna-knee-phase-b-v1")
            self.assertEqual(report["checks"]["csv_preflight"]["status"], "PASS")
            self.assertEqual(report["checks"]["dicom"]["status"], "BLOCKED")
            self.assertEqual(report["checks"]["report_quality"]["status"], "PASS")
            self.assertEqual(report["checks"]["input_immutability"]["status"], "PASS")
            self.assertTrue(all(check["status"] in CHECK_STATES for check in report["checks"].values()))
            self.assertFalse(report["training_ready"])
            self.assertIsNone(report["model_metrics"])
            raw = json.dumps(report, allow_nan=False)
            for sentinel in (PRIVATE_REPORT, "private.study", "private.series", "PrivatePatientSentinel", "synthetic.dcm"):
                self.assertNotIn(sentinel, raw)
            self.assertLess(len(raw), 64 * 1024)
            self.assertEqual(hashes(root), before)
            path = write_phase_b_report(report, PhaseBConfig.from_dict(settings))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)

    def test_missing_and_invalid_csvs_block_further_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            missing = run_phase_b(PhaseBConfig(input_base=str(base / "missing")))
            self.assertEqual(missing["status"], "BLOCKED")
            self.assertEqual(missing["checks"]["dicom"]["status"], "NOT_ASSESSED")
            root, settings = fixture(base)
            (root / "train.csv").write_bytes(b"invalid\n")
            invalid = run_phase_b(PhaseBConfig.from_dict(settings))
            self.assertEqual(invalid["status"], "FAIL")
            self.assertEqual(invalid["checks"]["csv_preflight"]["status"], "FAIL")
            self.assertEqual(invalid["checks"]["dicom"]["status"], "NOT_ASSESSED")

    def test_empty_series_and_blocked_decoding_cannot_be_overall_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            for path in root.rglob("*.dcm"):
                path.unlink()  # Synthetic fixture only.
            report = run_phase_b(PhaseBConfig.from_dict(settings))
            self.assertEqual(report["checks"]["series_file_sample"]["status"], "FAIL")
            self.assertEqual(report["status"], "FAIL")
        with tempfile.TemporaryDirectory() as directory:
            _, settings = fixture(Path(directory))
            fake = {"status": "PASS", "headers": {"status": "PASS", "attempts": 1, "successes": 1, "failures": 0},
                    "decoding": {"status": "BLOCKED", "attempts": 0, "successes": 0, "failures": 0}}
            with mock.patch("rsna_knee.dicom_audit.inspect_dicom", return_value=fake):
                report = run_phase_b(PhaseBConfig.from_dict(settings))
            self.assertEqual(report["checks"]["pixel_decoding"]["status"], "BLOCKED")
            self.assertEqual(report["status"], "BLOCKED")

    def test_successful_scoped_checks_do_not_approve_training(self):
        with tempfile.TemporaryDirectory() as directory:
            _, settings = fixture(Path(directory))
            successful = {"status": "PASS", "attempts": 4, "successes": 4, "failures": 0}
            with mock.patch("rsna_knee.dicom_audit.inspect_dicom", return_value={
                    "status": "PASS", "headers": dict(successful), "decoding": dict(successful)}):
                report = run_phase_b(PhaseBConfig.from_dict(settings))
            self.assertEqual(report["status"], "PASS")
            self.assertFalse(report["training_ready"])
            self.assertFalse(report["split_approved"])
            self.assertIsNone(report["model_metrics"])

    def test_unrecognized_extensions_are_unassessed_not_proof_of_absent_dicoms(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            for path in root.rglob("*.dcm"):
                path.rename(path.with_suffix(""))  # Synthetic fixture only.
            report = run_phase_b(PhaseBConfig.from_dict(settings))
            sample = report["checks"]["series_file_sample"]
            self.assertEqual(sample["empty_series"], 0)
            self.assertEqual(sample["candidate_dicom_files"], 0)
            self.assertEqual(sample["extension_counts"]["other"], 4)
            self.assertEqual(sample["status"], "BLOCKED")
            self.assertNotIn("verified_no_dicom", json.dumps(sample))

    def test_output_budget_input_destination_and_stale_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            config = PhaseBConfig.from_dict(settings)
            with self.assertRaises(InputError):
                write_phase_b_report({"large": "x" * 100}, config.with_overrides(max_report_bytes=20))
            self.assertFalse(Path(config.output_dir).exists())
            with self.assertRaises(InputError):
                write_phase_b_report({}, config.with_overrides(output_dir=str(root / "write")))
            with self.assertRaises(InputError):
                run_phase_b(config, preflight={"configuration": {}})
            with self.assertRaisesRegex(InputError, "REPORT_SIZE_LIMIT"):
                run_phase_b(config.with_overrides(max_report_bytes=20))
            self.assertFalse((root / "write").exists())


class PhaseBNotebookTests(unittest.TestCase):
    def tearDown(self):
        for name in tuple(sys.modules):
            if name == PHASE_B_PACKAGE or name.startswith(PHASE_B_PACKAGE + "."):
                del sys.modules[name]
        sys.meta_path[:] = [finder for finder in sys.meta_path if getattr(finder, "package_name", None) != PHASE_B_PACKAGE]

    def execute(self, notebook, namespace=None, only=None):
        namespace = namespace if namespace is not None else {"__name__": "__main__"}
        with redirect_stdout(io.StringIO()):
            for cell in notebook["cells"]:
                if cell["cell_type"] == "code" and (only is None or cell["id"] in only):
                    exec(compile("".join(cell["source"]), "<synthetic:" + cell["id"] + ">", "exec"), namespace)
        return namespace

    def test_layout_hashes_modes_deterministic_rebuild_and_artifact(self):
        first = build_phase_b_notebook(SOURCES)
        self.assertEqual(notebook_bytes(first), notebook_bytes(build_phase_b_notebook(SOURCES)))
        self.assertEqual(tuple((cell["id"], cell["cell_type"]) for cell in first["cells"]), PHASE_B_LAYOUT)
        for filename, digest in first["metadata"]["rsna_audit_bundle"]["source_sha256"].items():
            self.assertEqual(hashlib.sha256((SOURCES / filename).read_bytes()).hexdigest(), digest)
        self.assertEqual(set(first["metadata"]["rsna_audit_bundle"]["source_sha256"]), {name + ".py" for name in PHASE_B_MODULES})
        settings = ast.parse("".join(first["cells"][2]["source"]))
        values = ast.literal_eval(settings.body[0].value)
        self.assertTrue(values["inspect_csv"])
        self.assertEqual(values["max_pixel_decodes"], 12)
        self.assertEqual((PROJECT / "notebooks" / "kaggle_phase_b_audit.ipynb").read_bytes(), notebook_bytes(first))
        for cell in first["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])

    def test_invalid_layout_config_and_unrelated_file_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for problem in ("duplicate", "reorder", "factory"):
                notebook = build_phase_b_notebook(SOURCES)
                if problem == "duplicate":
                    notebook["cells"].append(notebook["cells"][2])
                elif problem == "reorder":
                    notebook["cells"][2], notebook["cells"][3] = notebook["cells"][3], notebook["cells"][2]
                else:
                    notebook["cells"][2]["source"] = ["CONFIG = {}\nphase_b_config = CONFIG\n"]
                with self.subTest(problem=problem), self.assertRaises(InputError):
                    write_phase_b_notebook(notebook, base / "notebooks" / "bad.ipynb", base / "notebooks")
            output = base / "notebooks" / "user.ipynb"
            output.parent.mkdir()
            output.write_bytes(b"private user's unrelated file")
            with self.assertRaises(InputError):
                write_phase_b_notebook(build_phase_b_notebook(SOURCES), output, output.parent)
            self.assertEqual(output.read_bytes(), b"private user's unrelated file")
            with self.assertRaises(InputError):
                write_phase_b_notebook(build_phase_b_notebook(SOURCES), base / "outside.ipynb", output.parent)

    def test_same_loader_rerun_class_identity_stale_report_and_checksum_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            _, settings = fixture(Path(directory))
            notebook = build_phase_b_notebook(SOURCES, settings)
            namespace = self.execute(notebook, only={"embedded-package", "phase-b-settings"})
            previous = namespace["phase_b_config"]
            self.execute(notebook, namespace, only={"embedded-package"})
            self.assertIs(type(previous), namespace["PhaseBConfig"])
            self.execute(notebook, namespace, only={"csv-preflight", "run-phase-b"})
            namespace["phase_b_config"] = previous.with_overrides(max_pixel_decodes=0)
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.execute(notebook, namespace, only={"write-evidence"})
            self.assertFalse(Path(settings["output_dir"]).exists())
            loader = "".join(notebook["cells"][1]["source"])
            bundle_call = next(node.value for node in ast.parse(loader).body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "_RSNA_BUNDLE")
            original = ast.literal_eval(bundle_call.args[0])
            bundle = json.loads(original)
            bundle["sources"]["phase_b_config"] += "\n# tampered\n"
            changed = loader.replace(repr(original), repr(json.dumps(bundle, sort_keys=True, ensure_ascii=True, separators=(",", ":"))), 1)
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                exec(changed, {"__name__": "__main__"})

    def test_isolated_offline_execution_no_checkout_import_or_input_modification(self):
        runner = '''import json, socket, sys
def deny_network(*args, **kwargs):
    raise AssertionError("Network forbidden")
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
socket.getaddrinfo = deny_network
notebook = json.load(sys.stdin)
namespace = {"__name__": "__main__"}
for cell in notebook["cells"]:
    if cell["cell_type"] == "code":
        exec(compile("".join(cell["source"]), "<offline:" + cell["id"] + ">", "exec"), namespace)
assert 'rsna_knee' not in sys.modules
report = namespace['phase_b_report']
assert report['checks']['csv_preflight']['status'] == 'PASS'
assert report['training_ready'] is False and report['model_metrics'] is None
assert report['runtime']['gpu_used'] is False
assert type(namespace['phase_b_config']) is namespace['run_phase_b'].__globals__['PhaseBConfig']
assert json.loads(namespace['report_path'].read_text(encoding='utf-8')) == report
for sentinel in ('PrivateSentinelReportText', 'private.study', 'private.series', 'synthetic.dcm'):
    assert sentinel not in json.dumps(report)
'''
        with tempfile.TemporaryDirectory() as directory:
            root, settings = fixture(Path(directory))
            before = hashes(root)
            result = subprocess.run([sys.executable, "-I", "-B", "-c", runner],
                                    input=notebook_bytes(build_phase_b_notebook(SOURCES, settings)),
                                    cwd=directory, capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(hashes(root), before)
            self.assertEqual(len(list(Path(settings["output_dir"]).glob("*.json"))), 1)
            self.assertNotIn(PRIVATE_REPORT, result.stdout.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
