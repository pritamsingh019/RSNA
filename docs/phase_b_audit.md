# Phase B: real-data and DICOM audit

## Evidence and approval

This is a reviewed read-only audit implementation, awaiting a Kaggle run. The user reports a successful CPU / Internet Off CSV audit: 4,407 training studies, 24,371 training series, 3 visible test studies and 15 series, all five CSVs read, all training reports nonempty, no train/test CSV identifier overlap, and 58 observed / 4,349 unknown cells per target with both classes represented. Exact positive/negative counts were not supplied and are not invented here. The observed labels do not establish a separate 58-study gold resource. The visible test sample does not establish hidden evaluation coverage.

Local tests use synthetic fixtures and simulated DICOM readers. They do not prove Kaggle-runtime compatibility, actual image integrity, codec support or a patient map. No real DICOMs were copied locally or processed during implementation. No training, prediction generation, checkpoints, weak labels or performance measurements are included.

## Run and regenerate

```powershell
python -B scripts/check_scaffold.py
python -B scripts/build_audit_notebook.py
python -B scripts/build_audit_notebook.py --inspect-csv --output notebooks/kaggle_audit_csv.ipynb
python -B scripts/build_phase_b_notebook.py
```

For different limits, create a project JSON configuration and run `python scripts/build_phase_b_notebook.py --config configs/phase_b_kaggle.json`. The optional configuration uses local defaults for omitted paths; explicitly include `input_base: "/kaggle/input"` and `output_dir: "/kaggle/working/rsna_audit"` for Kaggle. `inspect_csv` must be true. Unknown fields, booleans masquerading as limits, unsafe config paths and duplicate JSON keys are rejected. No environment or account settings are read.

1. Import the entire generated `notebooks/kaggle_phase_b_audit.ipynb` into a fresh Kaggle notebook. Do not append its cells to an existing notebook.
2. Attach only the official RSNA Knee Abnormality Detection competition input.
3. Select CPU / no accelerator and Internet Off.
4. Save and Run All from a clean session. Cell order is introduction, embedded source/checksums, validated configuration, CSV preflight, Phase B, report write.
5. Review the compact output and the uniquely named JSON in `/kaggle/working/rsna_audit/`. Preserve statuses and coverage; an absent dependency stays blocked. Do not install packages or decoder plugins.
6. Return the compact JSON for review before validation design or any training. Do not publish reports, identifiers, pixels, large arrays or competition data.

## Budgets and sampling

| Check | Default budget | Coverage |
|---|---:|---|
| Input-root discovery | Depth 2; 128 directories; 2,048 entries | Bounded enclosing-root discovery, skips image payload trees |
| CSV reads | 32 MiB each; 100,000 rows each | All five named manifests if reads and contracts finish |
| Series topology inventory | Depth 6; 60,000 directories; 1,200,000 entries; 180 seconds | Manifest matching in observed directory namespace; matched series payload directories are pruned |
| File sample | 32 series; 4,096 entries per series; 32,768 total; 8 selected files per series | Immediate entries in selected series; exact counts only for fully enumerated samples |
| Header sample | 128 reads; 2 MiB read budget per header | Deterministic, stratified where represented, never full dataset coverage |
| Pixel smoke tests | At most 12 attempts; 64 MiB file; 4,194,304 declared array elements | Guarded samples only; no caches or model preprocessing |
| File selection / DICOM audit | 90 seconds each | Cooperative deadlines; checked between operations and reader calls |
| JSON output | 1 MiB | Size checked before writing; unique report path outside inputs |

Limits have validated upper bounds. File enumeration materializes only bounded entries before sorting. If a directory is truncated, selection may depend on filesystem enumeration order and reproducibility is explicitly marked incomplete. No unlimited directory sorting or repeated full DICOM traversal occurs. Time limits cannot interrupt an already running filesystem operation or native codec; the report states this limitation. Pixel dimension and byte guards reduce memory exposure but do not bound all decoder internals.

Strata combine train/test, Anatomical_Plane, Fluid_Sensitive and Fat_Suppression. Series are selected round-robin across sorted strata and sorted identifiers; files are allocated round-robin across selected series before additional files. If strata exceed the sample size, some remain unrepresented. Per-split coverage and actual series/file/header/decode counts must be read from the resulting audit; planned budgets are not achieved coverage.

## Directory and image evidence

Series names are matched to the real manifests while traversing observed containers. No fixed study/series path is assumed. Candidate conventions use placeholders, never UID lists. Missing counts and exhaustive uniqueness apply only where the stated inventory finishes; limits, errors and skipped subtrees leave unlocated series unresolved. Pruning matched payload directories excludes their internal subtrees from topology uniqueness claims. Missing or ambiguous paths and ancestry mismatches are failures; inaccessible paths and limits remain blocked. DICOM totals remain unknown: sample counts are never extrapolated to the 819,640-file expectation.

The file sample selects `.dcm` and `.dicom` filename candidates; extensions alone never verify DICOM contents. Other extensions or extensionless files are counted as unexpected and remain unassessed, not proof that DICOM data are absent. Empty directories fail the sample check; directories without recognized candidates or with unscanned subdirectories remain blocked. A different actual naming convention needs a separately reviewed discovery change.

pydicom is imported only if already present. Header reads use `stop_before_pixels=True`, do not force-parse invalid headers, and enforce bounded I/O. Counts cover UID presence, duplicate SOPs, within-series identifier/PatientID consistency, manifest/header discrepancies, categorical metadata, numeric geometry and malformed fields. Descriptions are counted by presence; raw PatientID, UIDs and report text stay private in memory.

Deflated Explicit VR datasets are blocked before pydicom parsing: pydicom may otherwise inflate the whole dataset despite header-only mode. This audit does not claim support for that transfer syntax. The bounded Part 10 metadata check identifies it before inflation; absence of an encountered syntax is still unassessed.

Physical ordering uses the slice normal from ImageOrientationPatient and the projection of ImagePositionPatient onto that normal. Gaps require compatible orientation and sufficient unique positions. Duplicate projections, nonuniform gaps, missing geometry and differing orientations are flagged. SliceThickness is an independent metadata field and is never used as a substitute for physical inter-slice spacing. Sparse samples cannot certify a complete series' order or gap distribution.

Header success is separate from pixel success. Decoding records encountered transfer syntaxes, detected decoder availability, attempt/success/failure counts, sanitized failure categories, shape and finite-value sanity. Syntaxes not encountered remain NOT_ASSESSED. No comprehensive codec claim follows from a small sample. Pixel arrays are released and are never written or included in JSON.

## Reports, labels and output

Report quality includes missing/empty/normalized duplicates, character/whitespace-token distributions, Latin/Devanagari indicators and fixed quality flags. These are script heuristics, not reliable language identification: Latin is not proof of English and Devanagari is not proof of Hindi. No report-derived labels are created.

Validation support reuses verified target counts and checks unchanged observation masks. Necessary positive/negative support is evaluated for two and three study-grouped folds. Joint feasibility is distinct from marginal support: an exact search is used only on tiny synthetic-sized problems (at most 12 labeled studies for two folds or 8 for three); larger problems remain NOT_ASSESSED. No assignments or identifiers are exported and no split is approved. Sampled PatientID metadata cannot establish trustworthy dataset-wide grouping. Sparse labels may prevent a reliable twelve-target validation estimate; validation design must be reviewed before training.

The JSON declares configuration, source hashes, elapsed time, actual samples and check statuses. PASS means the stated check completed within its declared scope; it does not certify all images, all codecs, patient independence or training readiness. FAIL is observed inconsistency or invalid evidence. BLOCKED means dependency, access or resource limits prevented completion. NOT_ASSESSED means the check did not run or its scope cannot support a conclusion. CSV SHA-256 is checked before/after; DICOMs are opened read-only and not globally hashed. `training_ready=false`, `split_approved=false` and `model_metrics=null` are always retained.

## Local implementation validation (2026-10-12)

- `python -B scripts/check_scaffold.py`: 167 tests run, 165 passed, 2 skipped; no failures.
- `python -B -m pytest -q -ra -p no:cacheprovider`: 165 passed, 2 skipped, 132 subtests passed.
- Skips: native Windows directory-symlink creation is unavailable; the optional real synthetic DICOM roundtrip cannot run because pydicom is absent. Simulated link/reparse checks and fake-reader/decoder tests passed. No dependencies were installed.
- All three stored notebooks passed nbformat validation, source-hash comparison and byte-identical deterministic rebuild. The original two retain five cells and false/true CSV modes; Phase B has six ordered cells and a single validated configuration.
- All three actual stored notebooks executed in fresh `python -I -B` processes against tiny synthetic inputs with socket network operations forbidden. Only deployment paths were overridden after the original configuration cell. Report roundtrips and before/after input hashes passed. Phase B correctly reported BLOCKED for absent pydicom while writing compact evidence.
- Python was 3.14.3. nbclient/ipykernel are absent, so execution was isolated Python rather than a live Jupyter kernel. No real competition inputs, Kaggle runtime, GPU, training or performance evaluation were exercised.

| Generated notebook | Bytes | SHA-256 |
|---|---:|---|
| `kaggle_audit.ipynb` | 60,174 | `35cb85fa8dc71f339dfe117cefc7e0a9731c01020f706b057439f02dbaba1dba` |
| `kaggle_audit_csv.ipynb` | 60,173 | `38398ad4562b01f43a9b98bbc884b278f12804fa3c04b5d0f703f557eaae8362` |
| `kaggle_phase_b_audit.ipynb` | 161,967 | `270abe4061d2652686636d56b59d826c21042441807be2ba6f45be8f92ab57f8` |
