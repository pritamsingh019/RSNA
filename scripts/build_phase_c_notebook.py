"""Build the self-contained Phase C report audit without accessing inputs."""

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rsna_knee.phase_c_config import load_phase_c_config
from rsna_knee.phase_c_notebook import build_phase_c_notebook, write_phase_c_notebook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Validated project JSON config; retain explicit Kaggle paths when overriding")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "notebooks" / "kaggle_phase_c_report_audit.ipynb")
    args = parser.parse_args()
    settings = load_phase_c_config(args.config).to_dict() if args.config else None
    notebook = build_phase_c_notebook(PROJECT_ROOT / "src" / "rsna_knee", settings)
    path = write_phase_c_notebook(notebook, args.output, PROJECT_ROOT / "notebooks")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
