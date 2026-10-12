"""Build the offline read-only Phase B notebook; never access competition inputs."""

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rsna_knee.phase_b_config import load_phase_b_config
from rsna_knee.phase_b_notebook import build_phase_b_notebook, write_phase_b_notebook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Validated JSON config; keep explicit Kaggle paths when overriding")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "notebooks" / "kaggle_phase_b_audit.ipynb")
    args = parser.parse_args()
    settings = load_phase_b_config(args.config).to_dict() if args.config else None
    notebook = build_phase_b_notebook(PROJECT_ROOT / "src" / "rsna_knee", settings)
    path = write_phase_b_notebook(notebook, args.output, PROJECT_ROOT / "notebooks")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
