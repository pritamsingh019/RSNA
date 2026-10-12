# Kaggle audit workflow

## Purpose

The generated notebook embeds the source needed to audit attached inputs without repository access or package installation. Its default configuration performs bounded discovery with CSV inspection disabled. Competition access, rules acceptance, current execution restrictions, and GPU quota remain unverified.

## Build locally

From the repository root with Python 3.10 or later:

```powershell
python scripts/check_scaffold.py
python scripts/build_audit_notebook.py
```

The builder writes `notebooks/kaggle_audit.ipynb`. Optional `--output` selects another `.ipynb` path within the project's `notebooks/` directory. It rejects protected or linked paths and preserves unrelated existing notebooks; only a recognizable generated audit notebook may be replaced. Optional `--config` loads a validated project JSON configuration; it does not merge a partial file into Kaggle defaults. Omitted paths in that file take the local `data` and `artifacts` defaults. Use the default builder command for Kaggle or provide explicit Kaggle input/output paths through a project configuration and regenerate the complete notebook.

Notebook-cell editing is unnecessary. Generate a CSV-inspection notebook through the project source:

```powershell
python scripts/build_audit_notebook.py --inspect-csv --output notebooks/kaggle_audit_csv.ipynb
```

`--inspect-csv` overrides the JSON configuration to true; `--no-inspect-csv` overrides it to false. The normal builder command retains discovery-only defaults. Both versions contain exactly one configuration cell, which constructs `AuditConfig.from_dict(CONFIG)` before the audit executes.

The notebook has no saved outputs or execution counts. Source and bundle SHA-256 metadata identify the embedded implementation and appear in its report. Regenerate after source changes.

## Browser steps

1. Confirm signed-in access to the intended competition and accept live rules if required.
2. Import the entire `notebooks/kaggle_audit.ipynb` file into a fresh notebook. For CSV inspection, import `notebooks/kaggle_audit_csv.ipynb` instead. Do not append cells from either file to the previous broken notebook.
3. Attach the official competition input.
4. Select CPU and Internet Off. Discovery needs no GPU.
5. The generated configuration uses `/kaggle/input` and `/kaggle/working/rsna_audit`. Choose the generated file for the intended mode; no added or rearranged configuration cells are needed. CSV inspection and the separate Phase B audit are now authorized.
6. Save and run all cells from a clean session.
7. Review the compact JSON result and logs. Missing, ambiguous, unsafe, or incomplete discovery must remain visible.
8. Retain compact evidence after reviewing its contents.

The notebook does not authenticate a CLI, call APIs, download data, install packages, decode DICOMs, retrieve weights, or train. Execution success alone does not verify competition access or full input integrity.

Re-running the same verified embedded source keeps the configuration class identity intact. When importing a changed source bundle, use Run All so the single configuration cell constructs its matching validated object. Missing/stale configurations and stale reports produce explicit execution-order guidance.

## Storage and interpretation

Mounted inputs remain read-only. Outputs belong in `/kaggle/working`, not the input mount. The scaffold does not extract or copy the dataset. Later image work should use lazy decoding and bounded buffers; large data, checkpoints, and dependencies remain outside Git.

Discovery evidence is not a completed label, report, DICOM, leakage, runtime, or clinical audit. Approved CSV preflight will have separate coverage. The separate resource remains **GOLD_RESOURCE_NOT_LOCATED** unless authoritative provenance and contents are supplied and verified.

The later offline inference bundle will require explicit checkpoints, codecs, target order, and clean runtime profiling. That bundle and a model submission notebook are outside this phase. Confirm live [execution requirements](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/overview/evaluation) before adopting them.

## Phase B notebook

Generate `notebooks/kaggle_phase_b_audit.ipynb` with `python scripts/build_phase_b_notebook.py`. Import the whole file into a fresh Kaggle notebook, attach only the official competition input, choose CPU / no accelerator and Internet Off, then Save and Run All. It has six ordered cells: introduction, verified embedded source, one validated configuration, CSV preflight, Phase B audit, and report write. No manual configuration-cell editing is required.

The result is `/kaggle/working/rsna_audit/phase_b_<UTC>_<suffix>.json`. Review each check's PASS / FAIL / BLOCKED / NOT_ASSESSED status, actual sample counts and limits. If pydicom or a decoder is absent, retain the blocked evidence; do not install dependencies to make the audit pass. Header readability, sampled pixel success and notebook execution do not approve training. The attached visible test sample is not the hidden evaluation set.

See [Phase B scope and limits](phase_b_audit.md) for exact coverage, regeneration and interpretation. The original two notebooks remain preflight-only and never decode DICOMs.

## Phase C report audit

Generate `notebooks/kaggle_phase_c_report_audit.ipynb` with `python scripts/build_phase_c_notebook.py`. Import the whole generated file into a fresh notebook, attach only official competition inputs, choose CPU/no accelerator and Internet Off, then Run All. The six-cell source verifies its bundle, creates a single validated configuration, performs CSV preflight, runs report aggregation/frozen rules/masked agreement/validation diagnostics, and writes compact aggregate JSON under `/kaggle/working/rsna_audit/`.

Actual report agreement is pending execution. Results remain EXPLORATORY, with explicit denominators, Wilson intervals, abstentions and patient-grouping limitations. No DICOMs are read or decoded, raw reports/IDs/per-study candidates are never emitted, and training readiness stays false. See [Phase C supervision and leakage design](phase_c_report_supervision.md) before using its evidence.
