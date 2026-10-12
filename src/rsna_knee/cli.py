"""Local entry point with no authenticated services or training commands."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from .audit import run_preflight, write_report
from .config import AuditConfig, ConfigError, load_config
from .inputs import InputError


def _source_identity() -> dict:
    package = Path(__file__).parent
    names = ("__init__", "config", "inputs", "contracts", "metrics", "submission", "audit", "cli")
    return {"modules": {name: hashlib.sha256((package / (name + ".py")).read_bytes()).hexdigest() for name in names}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only RSNA input/CSV preflight; no Phase B execution or training")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input-base")
    parser.add_argument("--data-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--inspect-csv", action="store_true", default=None,
                        help="Check bounded CSV contracts; source data is not modified")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config) if args.config else AuditConfig()
        overrides = {name: getattr(args, name) for name in ("input_base", "data_root", "output_dir", "inspect_csv") if getattr(args, name) is not None}
        config = config.with_overrides(**overrides)
        report = run_preflight(config, source_identity=_source_identity())
        path = write_report(report, config)
    except (ConfigError, InputError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, (ConfigError, InputError)) else "Preflight failed; check configuration and filesystem permissions"
        print(json.dumps({"status": "ERROR", "error_type": type(error).__name__, "reason": message}), file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "report_path": str(path),
                      "phase_b_status": report["phase_b_status"], "training_ready": report["training_ready"]}, indent=2))
    return 0 if report["status"] in ("MANIFESTS_LOCATED", "CONTRACTS_VALID") else 2
