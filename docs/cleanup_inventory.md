# Proposed cleanup inventory

Inspection date: 2026-10-11. **Review pending; no files have been deleted.**

The initial repository has 20 non-sensitive files: a title README, three historical audit artifacts, and 16 zero-byte scaffold markers. Only `README.md` is tracked. All scaffold and audit directories are existing untracked changes. There are no implementation files, tests, experiment configurations, competition manifests, DICOMs, decoded caches, or trained checkpoints in the inspected workspace. No `AGENTS.md` was found. Credential locations were excluded from file-content inspection.

| Existing item | Proposed disposition | Evidence / reason |
|---|---|---|
| `docs/phase1_audit.md` | Preserve byte-for-byte | Historical blocked audit and acquisition results, dated 2026-10-06 |
| `docs/data_manifest.md` | Preserve byte-for-byte | Search scope, failed authentication, and original storage measurement |
| `reports/phase1_audit.json` | Preserve byte-for-byte | Machine-readable historical evidence; unavailable values remain null |
| `.git/` and existing user changes | Preserve | User manages Git; no reset, staging, commit, PR, or history mutation |
| Credentials and unrelated user files | Preserve; do not inspect contents | No authentication or external account action is necessary for local development |
| `README.md` | Expand with current scope and reproduction commands | Currently a title only |
| `configs/.gitkeep`, `data/raw/.gitkeep`, `data/cache/.gitkeep`, `dicom/.gitkeep`, `ensemble/.gitkeep`, `evaluation/.gitkeep`, `inference/.gitkeep`, `labels/.gitkeep`, `losses/.gitkeep`, `models/.gitkeep`, `notebooks/.gitkeep`, `preprocessing/.gitkeep`, `reports/.gitkeep`, `scripts/.gitkeep`, `tests/.gitkeep`, `training/.gitkeep` | Candidate removal **after review** | Empty placeholders from an architecture never implemented; they contain no experiment evidence |

There is no obsolete training implementation to delete. The old proposal to stage the full competition corpus under local `data/raw/` is superseded by the 2026-10-11 requirement to use Kaggle-hosted inputs. The historical documents remain unchanged so their original dates and findings are retained.

## Compact replacement prepared in Phase A

```text
rsna_knee/                 dependency-free input discovery and environment check
scripts/build_notebook.py  reproducible notebook builder; embeds the reusable module
notebooks/kaggle_access_check.ipynb
tests/test_data_access.py  synthetic filesystem and clean notebook checks
docs/                     historical evidence, inventory, browser workflow, phase report
reports/                  historical audit JSON; real Kaggle audit outputs added later
README.md
.gitignore
```

The replacement is limited to Phase A. No training module, pretrained model, weak-label engine, split strategy, final inference notebook, or populated dataset audit is claimed. The next substantive implementation depends on the actual input and label schemas discovered on Kaggle.
