# RSNA Knee Data Manifest

**Audit date:** 2026-10-06  
**Status:** No competition or gold input artifacts located.  
**Machine-readable companion:** [phase1_audit.json](../reports/phase1_audit.json)

## Search roots

| Absolute path | Existence / search result | Input size | Modified time / hash |
|---|---|---:|---|
| `D:\RSNA` | Present; recursive targeted search returned no expected manifests, series directories, or `.dcm` files | No input files | N/A |
| `D:\RSNA\data\raw` | Present; only `.gitkeep` scaffold marker found | 0 input bytes | Directory timestamp `2026-10-06T19:30:26.7330434+05:30`; no input hash |
| `D:\data` | Absent | N/A | N/A |
| `D:\datasets` | Absent | N/A | N/A |
| `D:\input` | Absent | N/A | N/A |
| `D:\kaggle` | Absent | N/A | N/A |
| `C:\Users\LENOVO\Downloads` | Present; recursive targeted filename/directory search returned no expected inputs | No matching input files | Not hashed; unrelated contents were not inventoried |
| `C:\Users\LENOVO\Documents\Codex` | Present; recursive targeted filename/directory search returned no expected inputs | No matching input files | Not hashed; unrelated contents were not inventoried |
| `C:\Users\LENOVO\.kaggle\competitions` | Absent | N/A | N/A |
| `C:\Users\LENOVO\.cache\kaggle` | Absent | N/A | N/A |
| `C:\Users\LENOVO\.cache\kagglehub` | Absent | N/A | N/A |

Checked path variables `RSNA_DATA_DIR`, `DATA_ROOT`, `INPUT_DIR`, `KAGGLEHUB_CACHE`, `KAGGLE_DATASETS_DIR`, and `KAGGLE_CONFIG_DIR` were unset. Kaggle credentials were not read.

## Acquisition attempt and destination capacity

| Check | Result |
|---|---|
| Kaggle CLI | `kaggle --version` → Kaggle CLI 2.2.4; a Requests dependency compatibility warning was emitted |
| Competition file listing | `kaggle competitions files rsna-knee-abnormality-detection` → Authentication required; no file list was returned |
| Authentication / competition access | Authentication failed; competition access and any rules-acceptance requirement remain unverified |
| Download | Not started; no source archive name, size, or structure was available |
| Destination | `D:\RSNA\data\raw` |
| `D:` total capacity | 473,955,299,328 bytes = 441.41 GiB |
| `D:` free capacity | 427,872,174,080 bytes = 398.49 GiB |
| Requested dataset size | Unknown from Kaggle; the task references 569+ GB, which is not verified against a file listing |
| Extraction/cache/checkpoint/temp overhead | Unknown; must be budgeted in addition to download size |
| Safety margin | Insufficient. Free space is at least 141.13 GB below a 569 GB lower-bound reference, before extraction or other working files |

The storage gate fails. No download was attempted, and nothing was installed to address authentication.

The bounded filename search for `gold` was repeated in `D:\RSNA`, `C:\Users\LENOVO\Downloads`, and `C:\Users\LENOVO\Documents\Codex`; no candidate filename was found. Status: **GOLD_RESOURCE_NOT_LOCATED**.

## Expected input artifacts

No source file or dataset directory was located. Consequently, there is no source path, file size, modification time, or hash to record for these items. The following are **missing in the searched roots**, not proven absent from the entire machine:

| File/folder | Type | Classification | Source path | Size / modified / hash | Expected / observed structure |
|---|---|---|---|---|---|
| `train.csv` | File | Competition data | Unknown; not found | N/A | Expected training studies and 12 labels; unavailable |
| `train_series.csv` | File | Competition data | Unknown; not found | N/A | Expected training study/series manifest; unavailable |
| `test.csv` | File | Competition data | Unknown; not found | N/A | Expected test studies; unavailable |
| `test_series.csv` | File | Competition data | Unknown; not found | N/A | Expected test study/series manifest; unavailable |
| `sample_submission.csv` | File | Competition data | Unknown; not found | N/A | Expected submission schema; unavailable |
| `train_series/` | Folder | Competition data | Unknown; not found | N/A | Expected training DICOM series; unavailable |
| `test_series/` | Folder | Competition data | Unknown; not found | N/A | Expected test DICOM series; unavailable |
| 58-study gold labels/reports/mapping | File(s) unknown | Separate external/gold resource | **UNKNOWN; not found** | N/A | Expected count of 58 is stated in the project specification only; observed count and contents unverified |

## Gold provenance and restrictions

The exact gold source, filenames, observed study count, target columns, label semantics, provenance, license/usage restrictions, and mapping to `StudyInstanceUID` are all **UNKNOWN**. The resource was not located and was not used. Do not treat it as ground truth or use it for model selection until provenance and label semantics are reviewed.

## Integrity and immutability

No original competition or external data was found or modified. Hashes were not computed because no input files were located. CSV schema/null/duplicate checks, study overlap checks, UID integrity, DICOM metadata/decode audits, and representative image creation are all pending the actual inputs. Their status is **not assessed**, not clean.
