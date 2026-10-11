# Kaggle browser workflow

Prepared 2026-10-11 using public official Kaggle documentation. **The user's authentication, rule acceptance, competition input access, current quota, and actual data schemas remain unverified.** The historical CLI failure does not prevent building or uploading a notebook through the browser.

## Phase A: establish attached-input evidence

1. Sign in to Kaggle in your browser. Open the [competition Rules page](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/rules), review the full terms, and accept them if offered and appropriate. Account participation and data availability must be established in the browser; the local project does not authenticate for you.
2. Create a notebook from the competition's Code area or Kaggle's Create → New Notebook flow. Use **File → Import Notebook** to upload `notebooks/kaggle_access_check.ipynb`. Exact UI labels can change. Kaggle documents notebook import in its [official feature announcement](https://www.kaggle.com/product-announcements/470030).
3. Use **Add Input** / **Add Data** to attach `rsna-knee-abnormality-detection`. Confirm the attached competition and its displayed mount path. If this fails, record the browser's access/rules message and stop; no local download is required.
4. For this access check, select **CPU / no accelerator**, set **Internet Off**, and keep the notebook private. The notebook uses Python's standard library and makes no network requests. GPU training is deferred until the data and label gates have been reviewed.
5. Leave `INPUT_BASE = Path('/kaggle/input')`. Automatic discovery checks at most two directory levels, 128 directories, and 1,024 directory entries, excludes links and the known image containers, and reads no CSV/DICOM contents. If several attached datasets contain the five manifests, set `DATA_ROOT` to the actual competition path displayed in the editor. Paths beyond those limits require an explicit root rather than a bigger disk scan.
6. Choose **Save Version → Save & Run All**. Review the saved version's logs and outputs after execution. A quick save does not establish that the notebook executed from a clean state. See [Kaggle Notebooks documentation](https://www.kaggle.com/docs/notebooks).
7. Download only the small `rsna_audit/access_check_*.json` output from `/kaggle/working`. Kaggle supports downloading generated output files from the notebook viewer/editor; see [official output-download guidance](https://www.kaggle.com/product-feedback/109715). Competition images remain on Kaggle.

`MANIFESTS_LOCATED_AUDIT_PENDING` means the five filenames exist in a selected runtime directory. It does not establish that they belong to this competition, contain valid schemas, have usable labels, or reference readable images. Other statuses identify missing, partial, ambiguous, or incomplete discovery. Each run creates a new JSON artifact and preserves previous outputs. The notebook caps its JSON artifact at 2 MiB.

## Phase B evidence to collect next

Implement the data audit only after reviewing the access evidence. Save compact JSON/tables before any training:

- Exact manifest schemas, dtypes, identifier domains, nulls, duplicate rows/IDs, references, and train/test overlap. Study/series directory layout must be observed rather than inferred from naming examples.
- Per-target observed positive/negative/missing/invalid values and prevalence with an explicit denominator. Unknown or uncertain values are not negatives. Quantify complete/partial/unlabeled studies and label co-occurrence using jointly observed labels only.
- Availability of training reports, language and quality assessment with its method/uncertainty; preserve the distinction between reports, official labels, and any separately sourced gold resource. Do not echo full reports into published outputs.
- Series counts and distributions, metadata combinations, image dimensions, spacing/orientations, transfer syntaxes, missing files, UID consistency, and decode failures. Physical slice gaps must be derived from projected positions where usable. Record whether header/decode coverage is sampled or complete.
- Patient grouping where an available, documented patient identifier permits it; identify unresolved leakage risks before splits. Do not derive normalization, labels, model decisions, or ensemble weights from test population statistics.
- The exact submission identifier, column order, row count, and probability domain from the actual manifests and sample submission. Test images/metadata may be inventoried for integrity and inference compatibility; hidden/private targets are not inspected.

Use `/kaggle/input` as the read-only source and `/kaggle/working` for compact outputs. Kaggle's [official guidance](https://www.kaggle.com/general/223073) describes the read-only input mount. Read the current notebook's resource display before budgeting working storage or accelerator time. Do not extract or cache the whole corpus in working storage.

## Later experiments and submission

After the label/split audit, enable a currently available GPU for an explicitly budgeted small baseline. Check actual package versions and decoding support in that runtime. External pretrained weights/datasets require documented source, license/rules review, and explicit approval before adoption; none are bundled here.

The [competition evaluation page](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/overview/evaluation) lists notebook-only submissions, CPU/GPU runtime of at most nine hours, disabled internet, and `submission.csv`. These public constraints must be rechecked against the account-visible rules before a final candidate; full rules and data licenses were not verified during this Phase A review.

A future submission notebook must load approved attached weights without network access or training, decode/preprocess with a bounded cache, record full runtime and memory, and validate exactly one unique row per required test study, all 12 target columns in the required order, and finite probabilities in the permitted range. A fallback must also pass that validation and use only training-derived parameters. None of those model-dependent paths has been implemented or benchmarked in Phase A.

Save the final inference notebook with **Internet Off → Save & Run All**, inspect `submission.csv` and the fresh execution logs, and then use the saved version's **Submit** control when you choose to submit. [Kaggle competition documentation](https://www.kaggle.com/docs/competitions) describes notebook submissions. Saving the current access-check notebook creates evidence only; it is not a competition submission.
