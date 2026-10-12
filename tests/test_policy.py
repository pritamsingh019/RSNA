from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryPolicyTests(unittest.TestCase):
    def test_git_ignores_data_secrets_and_generated_artifacts(self):
        # These are nonexistent names, not credential or dataset fixtures.
        names = ["data/raw/train.csv", "artifacts/preflight.json", "checkpoints/model.pt",
                 "outputs/predictions.csv", "cache/decoded.bin", "fixture.dcm", "fixture.dicom",
                 "fixture.safetensors", "fixture.npz", ".env", ".env.local", ".aws/credentials",
                 ".kaggle/kaggle.json", "credentials", "access_token", "private.key", "private.pem",
                 ".venv/package.py", "node_modules/example/index.js", "src/rsna_knee/__pycache__/example.pyc"]
        process = subprocess.run(["git", "--no-optional-locks", "check-ignore", "--no-index", "-z", "--stdin"],
                                 input=("\0".join(names) + "\0").encode("utf-8"), cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(set(process.stdout.decode("utf-8").split("\0")[:-1]), set(names))

    def test_reviewed_sources_and_notebook_are_not_ignored(self):
        names = ["src/rsna_knee/config.py", "configs/audit.json", "tests/test_config.py", "docs/architecture.md", "notebooks/kaggle_audit.ipynb"]
        process = subprocess.run(["git", "--no-optional-locks", "check-ignore", "--no-index", "-z", "--stdin"],
                                 input=("\0".join(names) + "\0").encode("utf-8"), cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(process.stdout, b"")
