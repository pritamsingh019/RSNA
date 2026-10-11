# RSNA Knee Abnormality Detection

Compact rebuild, currently **Phase A: repository and data-access audit**. No local competition inputs, usable checkpoints, or measured model scores are available. Full data stays on Kaggle; local development uses source code and small synthetic fixtures. The old acquisition reports remain historical evidence dated 2026-10-06.

## Start here

- [Cleanup inventory](docs/cleanup_inventory.md): proposed removals await review; no files deleted.
- [Phase A report](docs/phase_a_rebuild.md): current evidence, checks, limitations, and next action.
- [Kaggle browser workflow](docs/kaggle_workflow.md): manual access and offline execution.
- [Uploadable access-check notebook](notebooks/kaggle_access_check.ipynb): self-contained, CPU-only Phase A input discovery.

## Local reproduction

Python 3.10 or newer is sufficient for Phase A; no package installation or authenticated Kaggle CLI is required. From the repository root:

```powershell
python -m unittest discover -s tests -v
python scripts/build_notebook.py
python -m rsna_knee.data_access --input-base data/raw --output-dir artifacts
```

The local access command saves a small JSON artifact and returns exit code 2 when required manifests are missing, ambiguous, or discovery is incomplete. It does not download files, read credentials, decode images, or fit anything. This repository currently has no input data, so a missing-input result is expected. Treat any detected filenames as presence evidence only.

Rebuild the generated notebook whenever `rsna_knee/data_access.py` changes. It embeds that module and records its SHA-256; the notebook requires no repository attachment or internet connection. On Kaggle, the default source is `/kaggle/input` and the output is `/kaggle/working/rsna_audit`. If multiple attachments contain the five manifests, set `DATA_ROOT` explicitly from the displayed input path.

## Work gates

| Phase | Current state | Required evidence before advancing |
|---|---|---|
| A: repository and access | Local review completed; browser access unverified | Review cleanup inventory and run the access-check notebook with competition input attached |
| B: input/schema audit | Pending; implementation deferred | Actual CSV schemas, label coverage/co-occurrence, report availability/quality, series inventory, metadata/decoding/UID integrity, and submission schema |
| C: labels and splits | Pending | Distinguish missing labels from negatives; patient/study leakage check; documented gold provenance if available |
| D: inexpensive baseline | Pending | Reviewed label/split audit and compute budget |
| E–G: experiments, selection, submission | Pending | Measured fold AUC and per-target counts, reproducibility, offline runtime, clean saved execution, exact submission validation and fallback |

Model selection will use validation evidence. External weights/data require competition-rule and provenance review plus explicit approval before adoption. Test reports and hidden targets must not be used. No complete local dataset download, cloud purchase, Git commit, or competition submission is part of Phase A.
