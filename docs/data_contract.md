# Data contract

## Evidence status

These contracts are tested with synthetic fixtures. The user reports successful Kaggle CSV validation (4,407 training studies, 24,371 series, all five manifests), with sparse labels and only 3 visible test studies / 15 series in that runtime. This repository does not contain the raw audit artifact or locally remeasure those inputs. Expected filenames and target names do not independently prove data integrity or label quality.

## Discovery and identities

The expected logical inputs are:

```text
train.csv
train_series.csv
test.csv
test_series.csv
sample_submission.csv
train_series/
test_series/
```

Mounted inputs may use another enclosing directory. Preserve identifiers and relative series paths. Do not rename UIDs or image filenames to fit an expectation.

The initial synthetic CSV contract declares these required columns. They remain expectations until compared with the actual competition files.

| Table | Required columns |
|---|---|
| `train.csv` | `StudyInstanceUID`, the twelve target columns, `Report` |
| `test.csv` | `StudyInstanceUID` |
| Both series CSVs | `StudyInstanceUID`, `SeriesInstanceUID`, `Anatomical_Plane`, `Fluid_Sensitive`, `Fat_Suppression` |
| `sample_submission.csv` | Exactly `StudyInstanceUID` followed by the twelve targets in order |

Study and series contracts permit additional columns without assigning them meaning. The submission contract requires an exact column sequence.

Configuration declares search roots and scan limits. The local defaults use `input_base="data"`, `output_dir="artifacts"`, no explicit `data_root`, and `inspect_csv=false`. Limits are depth 2, 128 directories, 2,048 entries, 8,388,608 bytes per inspected CSV, 100,000 CSV rows, and 1,048,576 report bytes. Invalid or unknown configuration values are rejected.

Discovery must distinguish missing inputs, partial candidates, multiple plausible roots, unsafe paths, and exhausted scan limits. A check that did not finish remains incomplete even if some files were found.

Identifiers remain strings. Do not convert them to floating point, normalize punctuation, or silently change spelling. CSV validation checks headers and row shape before interpreting labels or references. Duplicate IDs, unknown referenced studies, and train/test overlap require explicit failures or incomplete status where inputs are unavailable.

## Labels

The planned target order is `ACL`, `MCL`, `Medial Meniscus`, `Lateral Meniscus`, `Medial OA`, `Lateral OA`, `PF OA`, `Effusion`, `Synovitis`, `Baker's`, `Contusion`, `Fracture`. Compare this with actual competition files before relying on it.

Every target has a value and an observation mask. Verified binary `0` and `1` labels are observed. Missing cells remain unknown. Unexpected values are invalid and cannot become negatives automatically. A numeric fill value for an unknown cell carries no label meaning and requires a false mask.

`parse_label` returns the numeric value or `None`, plus `observed`, `unknown`, or `invalid`. The observation mask is true only for `observed`. Accepted textual binary values are `0`, `1`, `0.0`, and `1.0`. Empty cells and actual floating NaN are unknown; the literal text `NaN`, booleans, and other unexpected tokens are invalid.

`label_vector` materializes all twelve values, observation masks, and states in target order without filling unknowns with zero. Unknown and invalid cells retain `None` and a false mask. It does not generate weak supervision or tensors.

Report positive, negative, unknown, invalid, and total counts per target. State denominators for coverage and prevalence. Co-occurrence may use only jointly observed labels.

Future weak labels need separate values, masks, confidence, evidence spans, extraction identity, and provenance. Observed reference labels take precedence. Unmentioned, uncertain, contradictory, or historical report findings require separate treatment.

## Audit evidence

Record configuration, implementation identity, input paths, inspected file sizes and modification times, and SHA-256 where inspected. A hash establishes byte identity, not semantic correctness or annotation provenance.

Every check needs its scope, coverage or denominator, and explicit result. Disabled and missing checks remain unmeasured. Sampled checks state their sampling. Fixture results state that they are synthetic. Reports must not include credential contents or environment secrets.

CSV progress distinguishes attempted files, successfully parsed files, and completed contract validation. On a parsing failure, `csv_contents_read` may be `null` because bytes could have been consumed. Completed file hashes are retained even when a later reference/schema check fails.

The CLI writes unique `artifacts/preflight_<UTC>_<uuid>.json` results by default. Missing, ambiguous, invalid, or incomplete input states return nonzero status. A valid presence check remains an initial-scaffold result with Phase B unrun and training readiness false.

## DICOM and gold resources

Phase B implements bounded metadata inspection and at most twelve guarded pixel-decoding smoke tests using already installed pydicom. Actual syntax, photometric interpretation, transforms, orientation, position, spacing, dimensions and decoding failures remain unverified until Kaggle evidence is reviewed. Headers alone do not prove decoding works. Sampled success does not certify full image integrity or training eligibility. See [audit limits](phase_b_audit.md).

The separate gold resource remains **GOLD_RESOURCE_NOT_LOCATED**. If supplied, verify exact source, path, size, checksum, observed study count, columns, annotation definitions, UID mapping, overlap, provenance, and usage terms. An expected count of 58 or a synthetic table establishes none of these.

## Predictions and submissions

Future evaluation records require study identity, fold, target, score, observed reference label and mask, and model identity. Weak-label agreement remains distinct from expert-reference image evaluation.

A supplied submission must use the expected identifier membership and exact target order, with one row per required study and finite probabilities in `[0, 1]`. Duplicate, missing, extra identifiers, malformed columns, and invalid probabilities fail validation. Format validity does not imply useful predictions.
