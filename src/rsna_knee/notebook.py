"""Build a deterministic notebook containing the reviewed audit package.

The notebook loads its embedded sources under a private package name in memory.
It neither extracts source files nor imports code from the checkout at runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pprint
import tempfile
from typing import Any

from .inputs import InputError, safe_absolute


SOURCE_MODULES = (
    "__init__",
    "config",
    "inputs",
    "contracts",
    "metrics",
    "submission",
    "audit",
    "cli",
)
PACKAGE_NAME = "rsna_knee_bundle"
DEFAULT_CONFIG = {
    "input_base": "/kaggle/input",
    "output_dir": "/kaggle/working/rsna_audit",
    "inspect_csv": False,
}
MAX_SOURCE_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_NOTEBOOK_BYTES = 8 * 1024 * 1024


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _source_bundle(source_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    package_root = safe_absolute(source_root)
    if not package_root.is_dir():
        raise ValueError("source_root must be the rsna_knee package directory")
    sources: dict[str, str] = {}
    hashes: dict[str, str] = {}
    total_bytes = 0
    for module in SOURCE_MODULES:
        path = safe_absolute(package_root / f"{module}.py")
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Required regular source file is missing: {module}.py")
        if path.resolve(strict=True).parent != package_root:
            raise ValueError(f"Source file leaves the package directory: {module}.py")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError(f"Source file exceeds the bundle limit: {module}.py")
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError(f"Source file exceeds the bundle limit: {module}.py")
        total_bytes += len(raw)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("Source bundle exceeds the size limit")
        source = raw.decode("utf-8")
        # Keep original bytes in the source mapping so its hash proves the
        # source's identity, including original line endings.
        compile(source, f"<bundle:{module}.py>", "exec")
        sources[module] = source
        hashes[f"{module}.py"] = hashlib.sha256(raw).hexdigest()
    return sources, hashes


def _cell(kind: str, cell_id: str, source: str) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": kind,
        "id": cell_id,
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def build_notebook(source_root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic, self-contained audit notebook as a JSON object.

    ``source_root`` is the directory containing the reviewed package modules.
    Configuration is validated with the same contract the notebook will use.
    All required package sources must already exist; this builder never restores
    missing files or discovers extra modules automatically.
    """
    from .config import AuditConfig

    if config is not None and not isinstance(config, dict):
        raise TypeError("config must be a dictionary or None")
    settings = dict(DEFAULT_CONFIG)
    if config is not None:
        settings.update(config)
    AuditConfig.from_dict(settings)
    sources, hashes = _source_bundle(source_root)
    bundle_json = _canonical_json({"sources": sources, "sha256": hashes})
    loader = f'''# Reviewed source bundle: no filesystem extraction or network access.
import hashlib
import importlib.abc
import importlib.util
import json
import sys

_RSNA_BUNDLE = json.loads({bundle_json!r})
_RSNA_PACKAGE = {PACKAGE_NAME!r}
for _module, _source in _RSNA_BUNDLE["sources"].items():
    _actual = hashlib.sha256(_source.encode("utf-8")).hexdigest()
    if _actual != _RSNA_BUNDLE["sha256"][_module + ".py"]:
        raise RuntimeError("Embedded source checksum mismatch: " + _module)
_RSNA_SOURCE_IDENTITY = {{
    "kind": "embedded-notebook",
    "bundle_sha256": hashlib.sha256(json.dumps(_RSNA_BUNDLE, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    "source_sha256": dict(_RSNA_BUNDLE["sha256"]),
}}

class _RsnaBundleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    package_name = _RSNA_PACKAGE

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.package_name:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        prefix = self.package_name + "."
        if fullname.startswith(prefix):
            name = fullname[len(prefix):]
            if name in _RSNA_BUNDLE["sources"] and name != "__init__":
                return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = "__init__" if module.__name__ == self.package_name else module.__name__.split(".")[-1]
        module.__file__ = "<embedded:" + name + ".py>"
        code = compile(_RSNA_BUNDLE["sources"][name], module.__file__, "exec")
        exec(code, module.__dict__)

# Re-running this cell loads exactly this bundle, even in a used kernel.
for _name in tuple(sys.modules):
    if _name == _RSNA_PACKAGE or _name.startswith(_RSNA_PACKAGE + "."):
        del sys.modules[_name]
sys.meta_path[:] = [finder for finder in sys.meta_path if getattr(finder, "package_name", None) != _RSNA_PACKAGE]
sys.meta_path.insert(0, _RsnaBundleLoader())
from rsna_knee_bundle.config import AuditConfig
from rsna_knee_bundle.audit import run_preflight, write_report
'''
    # Python literals, rather than JSON booleans/null, keep this an editable cell.
    settings_code = "# Edit this dictionary for your attached inputs and output location.\nCONFIG = " + pprint.pformat(settings, sort_dicts=True)
    settings_code += "\naudit_config = AuditConfig.from_dict(CONFIG)\n"
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "rsna_audit_bundle": {
                "format_version": 1,
                "package_name": PACKAGE_NAME,
                "source_sha256": hashes,
                "bundle_sha256": hashlib.sha256(bundle_json.encode("utf-8")).hexdigest(),
            },
        },
        "cells": [
            _cell("markdown", "audit-overview", "# RSNA input audit\n\nThis notebook contains the reviewed Python audit sources and uses the standard library only. Attach competition inputs, keep Internet Off, and run the cells in order. No CLI authentication or package installation is needed.\n\nThe default run discovers input structure with bounded traversal. Enable `inspect_csv` only to inspect the declared CSV contracts; this notebook does not decode DICOMs or train models. Synthetic checks of this notebook do not verify competition access, actual data, or model performance.\n"),
            _cell("code", "embedded-package", loader),
            _cell("code", "audit-settings", settings_code),
            _cell("code", "run-preflight", "audit_report = run_preflight(audit_config, source_identity=_RSNA_SOURCE_IDENTITY)\nprint(json.dumps(audit_report, sort_keys=True, indent=2))\n"),
            _cell("code", "write-evidence", "report_path = write_report(audit_report, audit_config)\nprint(str(report_path))\n"),
        ],
    }
    return notebook


def notebook_bytes(notebook: dict[str, Any]) -> bytes:
    """Serialize notebook JSON deterministically, with one final newline."""
    return (json.dumps(notebook, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode("utf-8")


def _is_generated_notebook(value: Any) -> bool:
    """Recognize our notebook format without requiring an optional dependency."""
    if not isinstance(value, dict) or value.get("nbformat") != 4:
        return False
    if type(value.get("nbformat_minor")) is not int or not isinstance(value.get("cells"), list):
        return False
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        return False
    bundle = metadata.get("rsna_audit_bundle")
    if not isinstance(bundle, dict) or bundle.get("format_version") != 1 or bundle.get("package_name") != PACKAGE_NAME:
        return False
    hashes = bundle.get("source_sha256")
    if not isinstance(hashes, dict) or set(hashes) != {module + ".py" for module in SOURCE_MODULES}:
        return False
    for digest in (*hashes.values(), bundle.get("bundle_sha256")):
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            return False
    for cell in value["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") not in {"code", "markdown", "raw"}:
            return False
        if not isinstance(cell.get("metadata"), dict):
            return False
        source = cell.get("source")
        if not isinstance(source, str) and not (isinstance(source, list) and all(isinstance(line, str) for line in source)):
            return False
        if cell["cell_type"] == "code":
            count = cell.get("execution_count")
            if count is not None and type(count) is not int:
                return False
            if not isinstance(cell.get("outputs"), list):
                return False
    return True


def _existing_generated_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise InputError("Notebook output must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_NOTEBOOK_BYTES + 1)
    if len(raw) > MAX_NOTEBOOK_BYTES:
        raise InputError("Existing notebook exceeds the output size limit")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise InputError("Refusing to replace an unrecognized existing notebook") from None
    if not _is_generated_notebook(value):
        raise InputError("Refusing to replace an unrelated existing notebook")
    return raw


def write_notebook(notebook: dict[str, Any], path: Path, output_root: Path | None = None) -> Path:
    """Write inside the declared notebook directory, protecting existing files.

    Only a recognized generated audit notebook may be replaced. Every path and
    ancestor is checked for protected components, symlinks and reparse points.
    The script supplies its checkout's ``notebooks`` directory; a separate root
    is available for isolated synthetic checks.
    """
    root = safe_absolute(output_root if output_root is not None else Path(__file__).parents[2] / "notebooks")
    output = safe_absolute(path)
    if output == root or not output.is_relative_to(root):
        raise InputError("Notebook output must be inside the declared notebooks directory")
    if output.suffix.casefold() != ".ipynb":
        raise InputError("Notebook output must have the .ipynb extension")
    if not _is_generated_notebook(notebook):
        raise InputError("Output must be a generated RSNA audit notebook")
    payload = notebook_bytes(notebook)
    if len(payload) > MAX_NOTEBOOK_BYTES:
        raise InputError("Notebook exceeds the output size limit")
    original = _existing_generated_bytes(output) if output.exists() else None
    output.parent.mkdir(parents=True, exist_ok=True)
    safe_absolute(output)
    if original is None:
        # Exclusive create refuses a file or link that appeared after checks.
        with output.open("xb") as stream:
            stream.write(payload)
        return output
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".rsna_notebook_", suffix=".tmp", dir=output.parent, delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
        safe_absolute(output)
        if _existing_generated_bytes(output) != original:
            raise InputError("Existing notebook changed during generation; refusing replacement")
        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return output
