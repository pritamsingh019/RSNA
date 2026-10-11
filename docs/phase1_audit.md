# Phase 1 — Repository and Data Audit

**Status:** BLOCKED — inputs not located in the checked paths  
**Audit date:** 2026-10-06  
**Workspace:** `D:\RSNA`

## Executive summary

The repository initially contained only its title `README.md`; after checking the likely local paths listed below, no competition CSVs, series directories, report files, gold-label files, or DICOM images were located. The dataset-dependent parts of Phase 1 therefore cannot be measured. No class prevalence, study/series/slice counts, DICOM metadata distributions, decode results, or representative image can be reported without inventing results.

No model training, report analysis, or test-set modeling was performed. No original data was modified. A starter directory layout is present, including separate locations for raw inputs and decoded cache. Search results and machine-readable blocked-state results are in [data_manifest.md](data_manifest.md) and [phase1_audit.json](../reports/phase1_audit.json). Phase 1 does not pass.

## Continuation search results

The search was bounded to the workspace, likely local data roots, and two conventional user data locations; no whole-machine scan was run.

| Absolute path | Result |
|---|---|
| `D:\RSNA` | Recursively checked for the five named CSVs, `.dcm` files, and `train_series` / `test_series` directories; no matches |
| `D:\RSNA\data\raw` | Exists; contains only the zero-byte `.gitkeep` scaffold marker |
| `D:\data`, `D:\datasets`, `D:\input`, `D:\kaggle` | Do not exist |
| `C:\Users\LENOVO\Downloads` | Recursively checked for the named CSVs, DICOMs, and series directories; no matches |
| `C:\Users\LENOVO\Documents\Codex` | Recursively checked for the named CSVs, DICOMs, and series directories; no matches |
| `C:\Users\LENOVO\.kaggle\competitions` | Does not exist; Kaggle credential files were not inspected |
| `C:\Users\LENOVO\.cache\kaggle` and `C:\Users\LENOVO\.cache\kagglehub` | Do not exist |

The checked path variables `RSNA_DATA_DIR`, `DATA_ROOT`, `INPUT_DIR`, `KAGGLEHUB_CACHE`, `KAGGLE_DATASETS_DIR`, and `KAGGLE_CONFIG_DIR` are unset. Exact search roots and expected input names are also captured in the JSON audit output.

## Kaggle acquisition gate

- `kaggle --version` is available and reports **Kaggle CLI 2.2.4**. It emitted a `RequestsDependencyWarning` about installed `urllib3` / `chardet` / `charset_normalizer` versions; no package was installed or changed.
- `kaggle competitions files rsna-knee-abnormality-detection` returned **Authentication required**. Authentication did not work, so competition access is unverified. The request did not reach a file listing; no rules-acceptance/access error or filenames/sizes were returned.
- No download was started. The exact requested archive size, archive structure, compression, and extraction overhead are unknown because Kaggle did not provide the listing.
- The `D:` volume reports **473,955,299,328 bytes total (441.41 GiB)** and **427,872,174,080 bytes free (398.49 GiB)**. The task's 569+ GB dataset-size reference is not a verified Kaggle listing; even if treated as a 569 GB lower bound, free space is at least 141.13 GB short before extraction, preprocessing/cache, checkpoints, and temporary files. The storage gate fails and no safety margin is available.
- No arbitrary packages were installed and no Kaggle credentials were read or printed.

The gold-resource name search was repeated in the previously checked project locations (`D:\RSNA`, `C:\Users\LENOVO\Downloads`, and `C:\Users\LENOVO\Documents\Codex`) and found no filename containing `gold`. Status: **GOLD_RESOURCE_NOT_LOCATED**. This bounded filename search does not establish provenance or prove absence elsewhere.

**Acquisition outcome:** blocked by missing Kaggle authentication and insufficient storage relative to the stated 569+ GB reference. Competition data remain absent. Overall Phase 1 status remains **BLOCKED**.

## Repository and input inventory

| Item | Expected/use | Observed |
|---|---|---|
| `README.md` | Repository description | Present; title only |
| `train.csv` | Training labels / report identifiers | Missing |
| `train_series.csv` | Training series metadata | Missing |
| `test.csv` | Test study identifiers | Missing |
| `test_series.csv` | Test series metadata | Missing |
| `sample_submission.csv` | Submission schema | Missing |
| Training DICOMs | Image and metadata audit | No DICOM files found |
| Test DICOMs | Inference inventory only | No DICOM files found |
| Reports and 58-study gold resource | Training-only label evaluation | Missing |

At the start of the audit, before adding this report and the scaffold, the workspace contained 1 file (`README.md`), 0 CSV files, and 0 DICOM files. The input root and directory naming convention are not yet known, so no assumed Kaggle directory layout has been treated as fact.

## Audit results

| Audit area | Result |
|---|---|
| Study counts and train/test overlap | Not measurable; study manifests are absent |
| Target prevalence and missing labels | Not measurable; training labels are absent |
| Series per study and slices per series | Not measurable; series manifests/images are absent |
| Plane, Fluid_Sensitive, Fat_Suppression | Not measurable; metadata is absent |
| Pixel dimensions and pixel spacing | Not measurable; DICOMs are absent |
| Slice spacing and physical ordering | Not measurable; DICOMs are absent |
| Orientation and oblique coverage | Not measurable; DICOMs are absent |
| Transfer syntax distribution | Not measurable; DICOMs are absent |
| UID uniqueness / duplicate integrity | Not measurable; identifiers and headers are absent |
| CSV ↔ DICOM metadata consistency | Not measurable; both sides are absent |
| DICOM decode smoke check | Not run; there are no DICOM inputs |
| Representative image visualization | Not available; there are no DICOM inputs |

### Class and prevalence analysis

The 12 requested targets are recorded below. Prevalence, positive/negative counts, and missingness remain **unmeasured** until `train.csv` (and any authoritative gold labels) are available.

| Target | Prevalence / counts |
|---|---|
| ACL | Unmeasured |
| MCL | Unmeasured |
| Medial Meniscus | Unmeasured |
| Lateral Meniscus | Unmeasured |
| Medial OA | Unmeasured |
| Lateral OA | Unmeasured |
| PF OA | Unmeasured |
| Effusion | Unmeasured |
| Synovitis | Unmeasured |
| Baker's | Unmeasured |
| Contusion | Unmeasured |
| Fracture | Unmeasured |

### Series and metadata analysis

The requested distributions—studies per split, series per study, slices per series, anatomical plane, fluid-sensitive/fat-suppression flags, image dimensions, pixel/slice spacing, orientation, and transfer syntax—are all pending input data. They must be reported with missing/invalid counts and quantiles, not only averages. Physical slice spacing should be derived from image positions projected onto the slice normal when positions and orientation are usable; `SpacingBetweenSlices`/`SliceThickness` should be retained as separate metadata fields rather than silently substituted.

### Missing-value and integrity checks still required

When inputs arrive, the audit should check:

1. Required columns, null counts, duplicate rows, duplicate `StudyInstanceUID`/`SeriesInstanceUID`/`SOPInstanceUID`, and references to absent studies or series.
2. Study-level train/test ID disjointness and exact agreement between the manifests and available image directories.
3. Per-series consistency of `SeriesInstanceUID`, `StudyInstanceUID`, plane/sequence metadata, image dimensions, orientation, and transfer syntax.
4. Label value domain, target missingness, and class counts; preserve unknown/uncertain values rather than coercing them to negatives.
5. Decode representative files across each observed transfer syntax and sample decode failures across all series before declaring the audit passed.

## Preliminary preprocessing design

This is a design to validate against the eventual audit, not a claim about observed data:

1. Group instances by study and series UIDs. Reject or quarantine mismatched UID groups and duplicate SOP instances with an audit trail.
2. Decode pixel data through a DICOM library with support for the transfer syntaxes actually observed. Log decode errors by UID and syntax; do not silently drop failed slices.
3. Derive the slice normal from `ImageOrientationPatient`. Order slices by the dot product of `ImagePositionPatient` with that normal, preserving direction and physical positions. Use instance number or filenames only as diagnostics/fallbacks, never as the primary ordering rule.
4. Group series by plane and sequence metadata while preserving the original values and unknown categories. Do not infer missing sequence labels from image content during the first pass.
5. Apply modality rescale slope/intercept where present. Normalize per series with a robust foreground percentile window, and record the fitted parameters. Exclude only verified background-only slices; retain a trace of every selection decision.
6. Resample in-plane only after spacing/orientation distributions are understood. Select central/coverage windows using physical coordinates and an informative-slice policy; build `[i-1, i, i+1]` channels from physically adjacent slices, with explicit boundary handling.
7. Cache deterministic preprocessed tensors keyed by source UID, source file identity, and preprocessing configuration. Keep test inputs out of any fitted statistics, label creation, model selection, or blending.

Risks to quantify before adopting this pipeline include missing/inconsistent orientation or position tags, oblique acquisitions, irregular slice gaps, varying pixel spacing/FOV, inconsistent sequence flags, unsupported compressed transfer syntaxes, and background/outlier pixels distorting normalization.

## Proposed experiment matrix (after the audit passes)

No experiment is started in this phase. Use identical study-level folds and fixed preprocessing for comparisons; retain fold-level and per-target metrics, runtime, memory, and configuration.

| Order | Experiment | Purpose | Cost / decision rule |
|---|---|---|---|
| 0 | Audit and label-readiness gate | Establish valid inputs, leakage-safe study IDs, label quality, and decode coverage | Required before training; stop on unresolved UID, split, or decode integrity issues |
| 1 | Compact 2.5D CNN baseline, 224 px, three physically adjacent slices, window attention, 12 heads | Establish study-level BCE and ROC-AUC baseline | Lowest-cost reference; retain only if folds train stably and inference fits budget |
| 2 | Same encoder with hierarchical window → series → study aggregation and series metadata embeddings | Test whether multi-series evidence improves discrimination | Compare against row 1 using paired fold scores and added inference cost |
| 3 | Target-conditioned pooling on the best stable hierarchy | Test whether the 12 labels benefit from distinct image evidence | Keep only for reproducible OOF macro-AUC improvement without severe target regressions |
| 4 | One DINO-family encoder candidate and one compact alternate CNN candidate | Measure model-family diversity and gain per compute | Promote only if OOF lift or ensemble complementarity justifies training/inference cost |
| 5 | BCE vs weighted BCE vs calibrated soft-label BCE; ranking term as a controlled ablation | Align learning objective with macro ROC-AUC and report uncertainty | Tune only within training folds; assess calibration and fold variance |
| 6 | Self-supervised/domain adaptation on training MRI only | Test whether unlabeled in-domain images help | Defer unless supervised baselines expose a clear data-efficiency need; require a held-out fold gain |
| 7 | Small family blend using OOF probabilities/ranks | Test complementary errors | OOF-only weights; no public leaderboard fitting; validate a final untouched fold if available |

All splits must be by study. The 58 gold studies should remain an explicitly tracked calibration/evaluation resource and must not be casually mixed into report-derived pseudo-label training. Test reports are unavailable and test data must not influence preprocessing statistics, pseudo-labels, model/ensemble selection, or training.

## Initial repository structure

```text
configs/             experiment and preprocessing configurations
data/raw/            local competition inputs; do not commit source data
data/cache/          deterministic decoded/preprocessed tensors
dicom/               DICOM inventory, decoding, geometry, and UID utilities
reports/             report parsing and evidence extraction
labels/              label calibration and gold-set evaluation
preprocessing/       physical sampling and tensor construction
models/               encoders and hierarchical MIL modules
losses/               BCE, ranking, and consistency objectives
training/             study-level folds, training loops, and checkpoints
inference/            offline test inference and submission generation
ensemble/             OOF-only blending
evaluation/           ROC-AUC, calibration, and audit summaries
tests/                focused unit/integration checks for later implementation
notebooks/            thin Kaggle submission notebook(s)
scripts/              reproducible command-line entry points
docs/                 audit and experiment documentation
```

## Inputs needed to finish Phase 1

Place or expose the training and test manifests, sample submission, corresponding DICOM directories, and the separate 58-study gold labels/report mapping under a known local path. The actual directory names can differ; the audit should accept explicit paths. With those files available, the pending measurements, DICOM decoding smoke check, representative image, and preprocessing risks can be completed without starting model training.

## Gold resource provenance

The specification mentions a separate 58-study gold resource, but no resource file or source path was found in the searched locations. Its study count is therefore **unverified**; the value 58 is an expected count from the specification, not an observed count. Filenames, source, label columns, label semantics, provenance, license/usage restrictions, and mapping to `StudyInstanceUID` are all **UNKNOWN**. It must not be used for calibration, model selection, or training until its origin and labels are reviewed.

## Preprocessing decision gate

These decisions require actual DICOM headers and pixels; none can be settled from this workspace:

1. **Physical-coordinate ordering reliability:** unknown until `ImageOrientationPatient` and `ImagePositionPatient` coverage and consistency are measured.
2. **Missing orientation/position tags:** frequency unknown.
3. **Uniform slice spacing:** unknown; derive adjacent projected position gaps and report their distribution, separately from `SliceThickness` and `SpacingBetweenSlices`.
4. **Oblique acquisitions:** frequency unknown; calculate plane normals and deviations from canonical axes.
5. **Per-series normalization:** unvalidated candidate only; inspect foreground intensity distributions and failure cases before choosing it.
6. **Scanner/site intensity differences:** unknown; scanner/site metadata and training-only intensity summaries are unavailable.
7. **Background fraction:** unknown; pixel data is unavailable.
8. **Physical meaning of `[i-1, i, i+1]`:** unknown until through-plane gaps and irregularity are measured; adjacent index alone is not sufficient evidence of consistent physical distance.
9. **Slice/window policy:** not selected. Compare physical coverage sampling and informative-slice selection only after anatomy, spacing, and sequence distributions are audited.
10. **Fold-only fitted information:** any population-level intensity reference, resampling target, data-derived thresholds, sampling heuristics, class weights, or learned metadata vocabulary must be fit within each training fold. Deterministic per-series transforms may use that series' own metadata/pixels; they must not use test-set population statistics.

No preprocessing decision is treated as validated. There are no observed data integrity problems to characterize because no source inputs were located; the integrity state is **unknown**, not clean.
