"""Build the self-contained audit notebook from the reviewed local sources."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rsna_knee.notebook import build_notebook, write_notebook
from rsna_knee.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "notebooks" / "kaggle_audit.ipynb", help="Output .ipynb inside this checkout's notebooks directory; unrelated existing notebooks are protected")
    parser.add_argument("--config", type=Path, help="Optional full JSON audit configuration (uses local defaults for omitted fields)")
    parser.add_argument("--inspect-csv", action=argparse.BooleanOptionalAction, default=None,
                        help="Enable or disable CSV preflight in the generated configuration without editing notebook cells")
    args = parser.parse_args()
    settings = None
    if args.config is not None:
        settings = load_config(args.config).to_dict()
    if args.inspect_csv is not None:
        settings = dict(settings or {})
        settings["inspect_csv"] = args.inspect_csv
    notebook = build_notebook(PROJECT_ROOT / "src" / "rsna_knee", settings)
    output = write_notebook(notebook, args.output, PROJECT_ROOT / "notebooks")
    print(f"Wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
