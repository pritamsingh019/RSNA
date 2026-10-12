# Kaggle audit workflow

## Purpose

The generated notebook embeds the source needed to audit attached inputs without repository access or package installation. Its default configuration performs bounded discovery with CSV inspection disabled. Competition access, rules acceptance, current execution restrictions, and GPU quota remain unverified.

## Build locally

From the repository root with Python 3.10 or later:

```powershell
python scripts/check_scaffold.py
python scripts/build_audit_notebook.py
```

The builder writes `notebooks/kaggle_audit.ipynb`. Optional `--output` selects another `.ipynb` path within the project's `notebooks/` directory. It rejects protected or linked paths and preserves unrelated existing notebooks; only a recognizable generated audit notebook may be replaced. Optional `--config` loads a validated audit configuration; it does not merge a partial file into Kaggle defaults. Omitted paths in that file take the local `data` and `artifacts` defaults. Use the default builder command for Kaggle, edit the generated `CONFIG` cell, or provide explicit Kaggle input/output paths in a separate configuration.

The notebook has no saved outputs or execution counts. Source and bundle SHA-256 metadata identify the embedded implementation and appear in its report. Regenerate after source changes.

## Browser steps

1. Confirm signed-in access to the intended competition and accept live rules if required.
2. Import `notebooks/kaggle_audit.ipynb` in the Kaggle browser.
3. Attach the official competition input.
4. Select CPU and Internet Off. Discovery needs no GPU.
5. Review `CONFIG`. Inputs default to `/kaggle/input`; reports go to `/kaggle/working/rsna_audit`. Keep `inspect_csv=False` until real-data audit is approved.
6. Save and run all cells from a clean session.
7. Review the compact JSON result and logs. Missing, ambiguous, unsafe, or incomplete discovery must remain visible.
8. Retain compact evidence after reviewing its contents.

The notebook does not authenticate a CLI, call APIs, download data, install packages, decode DICOMs, retrieve weights, or train. Execution success alone does not verify competition access or full input integrity.

## Storage and interpretation

Mounted inputs remain read-only. Outputs belong in `/kaggle/working`, not the input mount. The scaffold does not extract or copy the dataset. Later image work should use lazy decoding and bounded buffers; large data, checkpoints, and dependencies remain outside Git.

Discovery evidence is not a completed label, report, DICOM, leakage, runtime, or clinical audit. Approved CSV preflight will have separate coverage. The separate resource remains **GOLD_RESOURCE_NOT_LOCATED** unless authoritative provenance and contents are supplied and verified.

The later offline inference bundle will require explicit checkpoints, codecs, target order, and clean runtime profiling. That bundle and a model submission notebook are outside this phase. Confirm live [execution requirements](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/overview/evaluation) before adopting them.
