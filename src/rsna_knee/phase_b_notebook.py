"""Deterministic Phase B notebook built with the existing verified primitives."""

import ast
import hashlib
from pathlib import Path
import pprint

from .inputs import InputError
from .notebook import (
    SOURCE_MODULES, _canonical_json, _cell, _is_generated_notebook, _source_bundle,
    embedded_loader_source, write_generated_notebook,
)
from .phase_b_config import PhaseBConfig


PHASE_B_MODULES = SOURCE_MODULES + (
    "phase_b_config", "series_inventory", "dicom_audit", "report_quality", "validation_support", "phase_b",
)
PHASE_B_PACKAGE = "rsna_knee_phase_b_bundle"
PHASE_B_LAYOUT = (
    ("phase-b-overview", "markdown"), ("embedded-package", "code"),
    ("phase-b-settings", "code"), ("csv-preflight", "code"),
    ("run-phase-b", "code"), ("write-evidence", "code"),
)
DEFAULT_PHASE_B_CONFIG = {
    "input_base": "/kaggle/input", "output_dir": "/kaggle/working/rsna_audit", "inspect_csv": True,
}


def build_phase_b_notebook(source_root: Path, config: dict | None = None) -> dict:
    if config is not None and not isinstance(config, dict):
        raise TypeError("config must be a dictionary or None")
    settings = {**DEFAULT_PHASE_B_CONFIG, **(config or {})}
    validated = PhaseBConfig.from_dict(settings)
    settings = validated.to_dict()  # Include all resource budgets for review.
    sources, hashes = _source_bundle(source_root, PHASE_B_MODULES)
    bundle_json = _canonical_json({"sources": sources, "sha256": hashes})
    loader = embedded_loader_source(sources, hashes, PHASE_B_PACKAGE,
        "from rsna_knee_phase_b_bundle.phase_b_config import PhaseBConfig\n"
        "from rsna_knee_phase_b_bundle.audit import run_preflight\n"
        "from rsna_knee_phase_b_bundle.phase_b import run_phase_b, write_phase_b_report\n")
    notebook = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "rsna_audit_bundle": {
                "format_version": 1, "package_name": PHASE_B_PACKAGE,
                "source_sha256": hashes,
                "bundle_sha256": hashlib.sha256(bundle_json.encode("utf-8")).hexdigest(),
            },
        },
        "cells": [
            _cell("markdown", "phase-b-overview", "# RSNA Phase B: read-only real-data and DICOM audit\n\n"
                  "Attach only the official competition input. Select CPU / no accelerator and Internet Off, then Run All from a clean session. "
                  "Source hashes are checked before the single validated configuration, CSV preflight, bounded Phase B audit, and JSON write. "
                  "No installation, credentials, repository imports, dataset copies, predictions, or training occur.\n\n"
                  "Only an already installed pydicom can read headers or run up to 12 guarded pixel smoke tests. "
                  "Missing dependencies and exhausted budgets remain BLOCKED or NOT_ASSESSED. "
                  "Series, header, and decoding samples never certify full DICOM coverage or all codecs. "
                  "Slice ordering/gaps use position projected onto orientation; SliceThickness is not a spacing substitute. "
                  "No raw reports, patient identifiers, UID tables, pixels, or arrays are retained. "
                  "Sparse labels require a separate validation review; training_ready always remains false. "
                  "Visible test studies describe this runtime sample, not the hidden evaluation set.\n"),
            _cell("code", "embedded-package", loader),
            _cell("code", "phase-b-settings", "# Generated limits. Change project configuration and rebuild; no manual cells needed.\n"
                  "CONFIG = " + pprint.pformat(settings, sort_dicts=True) + "\nphase_b_config = PhaseBConfig.from_dict(CONFIG)\n"),
            _cell("code", "csv-preflight", "if 'phase_b_config' not in globals() or not isinstance(phase_b_config, PhaseBConfig):\n"
                  "    raise RuntimeError('Missing or stale PhaseBConfig: use Run All to recreate the validated configuration.')\n"
                  "csv_preflight = run_preflight(phase_b_config.preflight_config(), source_identity=_RSNA_SOURCE_IDENTITY)\n"
                  "print(json.dumps({'csv_preflight_status': csv_preflight['status'], 'csv_contracts_status': csv_preflight['csv_contracts']['status']}))\n"),
            _cell("code", "run-phase-b", "if 'csv_preflight' not in globals() or not isinstance(phase_b_config, PhaseBConfig):\n"
                  "    raise RuntimeError('Run the configuration and CSV preflight first, or use Run All.')\n"
                  "phase_b_report = run_phase_b(phase_b_config, source_identity=_RSNA_SOURCE_IDENTITY, preflight=csv_preflight)\n"
                  "print(json.dumps(phase_b_report, sort_keys=True, indent=2, allow_nan=False))\n"),
            _cell("code", "write-evidence", "if 'phase_b_report' not in globals() or not isinstance(phase_b_config, PhaseBConfig):\n"
                  "    raise RuntimeError('Run the audit first, or use Run All.')\n"
                  "if phase_b_report.get('source_identity') != _RSNA_SOURCE_IDENTITY or phase_b_report.get('configuration') != phase_b_config.to_dict():\n"
                  "    raise RuntimeError('The Phase B report is stale: use Run All before writing evidence.')\n"
                  "report_path = write_phase_b_report(phase_b_report, phase_b_config)\nprint(str(report_path))\n"),
        ],
    }
    validate_phase_b_notebook(notebook)
    return notebook


def validate_phase_b_notebook(notebook):
    cells = notebook.get("cells", [])
    if tuple((cell.get("id"), cell.get("cell_type")) for cell in cells) != PHASE_B_LAYOUT:
        raise InputError("Phase B requires exactly six ordered generated cells")
    assignments = {"CONFIG": [], "phase_b_config": []}
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
    if any(len(items) != 1 or items[0][0] != "phase-b-settings" for items in assignments.values()):
        raise InputError("Phase B requires one validated configuration before CSV preflight")
    if ast.unparse(assignments["phase_b_config"][0][1]) != "PhaseBConfig.from_dict(CONFIG)":
        raise InputError("Phase B configuration must use PhaseBConfig.from_dict(CONFIG)")
    values = ast.literal_eval(assignments["CONFIG"][0][1])
    PhaseBConfig.from_dict(values)


def _recognize(notebook):
    return _is_generated_notebook(notebook, PHASE_B_PACKAGE, PHASE_B_MODULES)


def write_phase_b_notebook(notebook, path: Path, output_root: Path | None = None) -> Path:
    return write_generated_notebook(notebook, path, output_root, recognize=_recognize, validate=validate_phase_b_notebook)
