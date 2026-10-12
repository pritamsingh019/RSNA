# Initial scaffold review

Review date: **2026-10-12**. Scope: approved initial scaffold only. All runtime evidence below is local or synthetic. No Phase B audit, model training, external assets, package downloads, account/API action, or GPU use occurred.

## Changes

- Replaced the README and ignore rules with new content for this rebuild. `.gitignore` was created before implementation.
- Added the `src/rsna_knee` package: immutable validated configuration, bounded discovery/CSV reads, explicit label values and observation masks, study/series contracts, preflight reporting, observed-only AUC, strict submission validation, and deterministic notebook bundling.
- Added `configs/audit.json` and three checkout-friendly scripts: `audit.py`, `check_scaffold.py`, and `build_audit_notebook.py`. No installation or authenticated Kaggle CLI is necessary.
- Added eight synthetic test files and documentation for architecture, data contracts, validation, Kaggle execution, and external-asset status.
- Generated the reviewed, output-free `notebooks/kaggle_audit.ipynb`. It embeds eight runtime modules and records their hashes; the builder itself remains a local development module.
- Left former source, notebooks, reports and scaffold markers deleted. Only the explicitly requested README and `.gitignore` paths are reused with fresh replacement content. Old code was not restored.

## Actual validation

| Check | Result |
|---|---|
| `python -B scripts/check_scaffold.py` | 81 tests run in 0.709 seconds; 80 passed, 1 skipped, 0 failures/errors |
| `python -B -m pytest -q -p no:cacheprovider` | 80 passed, 1 skipped; 102 subtests passed; 0.88 seconds |
| Notebook format | Valid nbformat 4.5; five cells; zero saved outputs; null code-cell execution counts |
| Deterministic rebuild | Exact same bytes and SHA-256 after rebuilding |
| Self-contained execution | Passed sequential clean-namespace and isolated `python -I` execution with no checkout imports |
| Local CLI | Returned code 2 and `INPUT_BASE_MISSING`, as expected for absent inputs |
| Source immutability | Synthetic file hashes and supplied tabular rows preserved |
| Git-ignore policy | Data, secrets, dependency directories, caches and checkpoints ignored; source/config/docs/reviewed notebook remain visible |

The native symlink test was skipped because this Windows session denied creating directory links. Simulated symbolic-link and Windows reparse cases pass, but the skipped native case is not claimed as verified. The runners exercise the same suite; their totals are not separate sets of tests.

Notebook size: **53,988 bytes**. SHA-256:

```text
7514fc97f7ec65b71ff97ff3efa78c6f6faa4dfd3da08749c64ef2be8bc884ec
```

The local missing-input JSON artifact is **2,341 bytes** and is ignored under `artifacts/`. It records source hashes, configuration, runtime identity, `phase_b_status=NOT_RUN`, `training_ready=false`, and unassessed checks. No real data measurements or model scores are recorded.

## Safety decisions

Discovery has configurable directory/depth/entry limits. CSV and output sizes are bounded. Explicit roots cannot escape the input base. Configuration, inputs and outputs reject protected directories, links and reparse ancestors. Unknown and invalid labels never become negatives. Full macro AUC is unavailable unless all twelve target AUCs are defined.

Reports are written outside inputs with unique exclusive filenames. Report text is not echoed. Read progress distinguishes attempted/parsed files from completed contract checks. Submission writes refuse overwrites. The notebook builder is restricted to `.ipynb` output within `notebooks/`, preserves unrelated existing notebooks, and replaces only recognizable generated artifacts.

Git is left on `rebuild/clean-slate`, at `9846580116c6da0578489691b20f35d1fa92cfa9`. Nothing was staged, committed, pushed, reset, or rebased. GitHub `main` was not accessed or changed during implementation. The verified recovery backup remains outside the repository at `D:\RSNA_backup_before_reset_20261012_104506`.

## Remaining limitations

- Competition inputs, account access, live rules, and gold provenance remain unverified.
- No actual CSV label coverage, report-quality/language analysis, patient grouping, split, image inventory, physical geometry or DICOM decoding has been performed.
- No weak-label extractor, model, training loop, checkpoint, inference pipeline, or final submission notebook is implemented.
- Notebook cells were executed in local Python, not an actual Jupyter/Kaggle kernel; installed kernel execution tools were unavailable.
- Python 3.14.3 was used locally. The code declares Python 3.10+ compatibility, but other interpreters and the actual Kaggle runtime were not exercised.
- GPU/peak-memory usage, full dataset processing time, model AUC and nine-hour inference feasibility are unmeasured.

Stop here for review. Phase B and any training require further approval.
