"""Deterministic offline Phase C notebook using the verified source loader."""

import ast
import hashlib
from pathlib import Path
import pprint

from .inputs import InputError
from .notebook import (
    SOURCE_MODULES, _canonical_json, _cell, _is_generated_notebook, _source_bundle,
    embedded_loader_source, write_generated_notebook,
)
from .phase_c_config import PhaseCConfig


PHASE_C_MODULES = SOURCE_MODULES + (
    "report_quality", "validation_support", "report_rules", "report_agreement",
    "phase_c_config", "phase_c_validation", "phase_c",
)
PHASE_C_PACKAGE = "rsna_knee_phase_c_bundle"
PHASE_C_LAYOUT = (
    ("phase-c-overview", "markdown"), ("embedded-package", "code"),
    ("phase-c-settings", "code"), ("csv-preflight", "code"),
    ("run-phase-c", "code"), ("write-evidence", "code"),
)
DEFAULT_PHASE_C_CONFIG = {
    "input_base": "/kaggle/input", "output_dir": "/kaggle/working/rsna_audit", "inspect_csv": True,
}


def build_phase_c_notebook(source_root: Path, config: dict | None = None) -> dict:
    """Embed only the fixed audit allowlist; never inspect competition inputs."""
    if config is not None and not isinstance(config, dict):
        raise TypeError("config must be a dictionary or None")
    validated = PhaseCConfig.from_dict({**DEFAULT_PHASE_C_CONFIG, **(config or {})})
    settings = validated.to_dict()
    sources, hashes = _source_bundle(source_root, PHASE_C_MODULES)
    bundle_json = _canonical_json({"sources": sources, "sha256": hashes})
    loader = embedded_loader_source(sources, hashes, PHASE_C_PACKAGE,
        "from rsna_knee_phase_c_bundle.phase_c_config import PhaseCConfig\n"
        "from rsna_knee_phase_c_bundle.audit import run_preflight\n"
        "from rsna_knee_phase_c_bundle.phase_c import run_phase_c, write_phase_c_report\n")
    notebook = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "rsna_audit_bundle": {
                "format_version": 1, "package_name": PHASE_C_PACKAGE,
                "source_sha256": hashes,
                "bundle_sha256": hashlib.sha256(bundle_json.encode("utf-8")).hexdigest(),
            },
        },
        "cells": [
            _cell("markdown", "phase-c-overview", "# RSNA Phase C: report supervision and validation audit\n\n"
                  "Import this complete generated notebook, attach only the official competition input, select CPU / no accelerator "
                  "and Internet Off, then Run All from a clean session. It uses embedded standard-library sources; "
                  "no repository imports, installation, credentials, dataset copies, DICOM decoding, or training occur.\n\n"
                  "Execution order: verify embedded source hashes; create one validated configuration; run CSV preflight; "
                  "aggregate report quality; extract with the rule version and content hash frozen before evaluation; "
                  "evaluate tri-state candidates against observed labels only; assess validation feasibility; "
                  "write compact aggregate JSON. The ordered Phase C pipeline is implemented in the embedded run_phase_c.\n\n"
                  "An unmentioned or ambiguous condition remains UNKNOWN. Unknown source labels stay masked; "
                  "candidate report labels are separate from observed labels. Agreement is exploratory and is not model performance "
                  "or independent validation. Patient grouping and gold provenance require separate evidence. "
                  "No raw report text, report snippets, study identifiers, or per-study candidate tables are printed or saved. "
                  "Blocked and unassessed checks remain explicit; split approval and training readiness remain false. "
                  "Only versioned aggregate JSON is saved under the configured output-size limit. "
                  "Rebuild from project configuration to change limits; do not append configuration cells.\n"),
            _cell("code", "embedded-package", loader),
            _cell("code", "phase-c-settings", "# Generated limits. Change project configuration and rebuild; no manual cells needed.\n"
                  "CONFIG = " + pprint.pformat(settings, sort_dicts=True) + "\nphase_c_config = PhaseCConfig.from_dict(CONFIG)\n"),
            _cell("code", "csv-preflight", "if 'phase_c_config' not in globals() or not isinstance(phase_c_config, PhaseCConfig):\n"
                  "    raise RuntimeError('Missing or stale PhaseCConfig: use Run All to recreate the validated configuration.')\n"
                  "csv_preflight = run_preflight(phase_c_config.preflight_config(), source_identity=_RSNA_SOURCE_IDENTITY)\n"
                  "print(json.dumps({'csv_preflight_status': csv_preflight['status'], 'csv_contracts_status': csv_preflight['csv_contracts']['status']}))\n"),
            _cell("code", "run-phase-c", "if 'phase_c_config' not in globals() or not isinstance(phase_c_config, PhaseCConfig) or 'csv_preflight' not in globals():\n"
                  "    raise RuntimeError('Run the validated configuration and CSV preflight first, or use Run All.')\n"
                  "if csv_preflight.get('source_identity') != _RSNA_SOURCE_IDENTITY or csv_preflight.get('configuration') != phase_c_config.preflight_config().to_dict():\n"
                  "    raise RuntimeError('The CSV preflight is stale: use Run All before the Phase C audit.')\n"
                  "# Ordered pipeline: report aggregate -> frozen extraction -> observed-label agreement -> feasibility.\n"
                  "phase_c_report = run_phase_c(phase_c_config, source_identity=_RSNA_SOURCE_IDENTITY, preflight=csv_preflight)\n"
                  "print(json.dumps(phase_c_report, sort_keys=True, indent=2, allow_nan=False))\n"),
            _cell("code", "write-evidence", "if 'phase_c_config' not in globals() or not isinstance(phase_c_config, PhaseCConfig) or 'phase_c_report' not in globals():\n"
                  "    raise RuntimeError('Run the configuration and Phase C audit first, or use Run All.')\n"
                  "if phase_c_report.get('source_identity') != _RSNA_SOURCE_IDENTITY or phase_c_report.get('configuration') != phase_c_config.to_dict():\n"
                  "    raise RuntimeError('The Phase C report is stale: use Run All before writing evidence.')\n"
                  "report_path = write_phase_c_report(phase_c_report, phase_c_config)\nprint(str(report_path))\n"),
        ],
    }
    validate_phase_c_notebook(notebook)
    return notebook


def validate_phase_c_notebook(notebook: dict) -> None:
    """Reject duplicate, reordered, or unvalidated generated configuration."""
    cells = notebook.get("cells", [])
    if tuple((cell.get("id"), cell.get("cell_type")) for cell in cells) != PHASE_C_LAYOUT:
        raise InputError("Phase C requires exactly six ordered generated cells")
    assignments = {"CONFIG": [], "phase_c_config": []}
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        raw = cell["source"]
        tree = ast.parse(raw if isinstance(raw, str) else "".join(raw))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in assignments:
                        assignments[target.id].append((cell["id"], node.value))
    if any(len(items) != 1 or items[0][0] != "phase-c-settings" for items in assignments.values()):
        raise InputError("Phase C requires one validated configuration before CSV preflight")
    if ast.unparse(assignments["phase_c_config"][0][1]) != "PhaseCConfig.from_dict(CONFIG)":
        raise InputError("Phase C configuration must use PhaseCConfig.from_dict(CONFIG)")
    PhaseCConfig.from_dict(ast.literal_eval(assignments["CONFIG"][0][1]))


def _recognize(notebook):
    return _is_generated_notebook(notebook, PHASE_C_PACKAGE, PHASE_C_MODULES)


def write_phase_c_notebook(notebook: dict, path: Path, output_root: Path | None = None) -> Path:
    return write_generated_notebook(notebook, path, output_root, recognize=_recognize, validate=validate_phase_c_notebook)
