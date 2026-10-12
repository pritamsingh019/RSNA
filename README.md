# RSNA Knee Abnormality Detection

An experimental MRI project for sparse-label learning and reproducible Kaggle inference.

## Project status

**Reviewed Phase C implementation, awaiting real report-agreement evidence.** The project provides validated configuration, CSV and label contracts, bounded DICOM audits, frozen tri-state report rules, masked aggregate agreement with confidence intervals, validation-design safeguards, and self-contained offline notebooks. No model has been trained, no pretrained weights have been adopted, and no model-performance results are available.

Phase A and Phase B are merged into `main` at `5a6fcfe`. The reviewed Phase C implementation is maintained separately on `phase-c/report-supervision-audit`. The historical recovery backup is `D:\RSNA_backup_before_reset_20261012_104506`.

The user confirmed successful CPU / Internet Off Kaggle CSV and bounded DICOM audits: 4,407 training studies with nonempty reports; 24,371 training series and matching directories; 15 visible test-series directories; 128 sampled headers and 12 pixel smoke tests successful. There are 58 observed-label studies, 696 observed target cells and 52,188 unknown cells. These are user-supplied runtime observations, not local remeasurements. The three visible test studies are not the complete hidden evaluation set. Full DICOM integrity, trustworthy dataset-wide patient grouping, independent gold provenance, report-rule reliability and reliable twelve-target validation remain unverified.

This project is **not clinically validated** and is not intended for diagnosis or clinical decisions.

## Start locally

Use Python 3.10 or later from the repository root. The scaffold uses the Python standard library. Synthetic checks need no package installation, competition files, Kaggle credentials, GPU, or internet access.

```powershell
python scripts/check_scaffold.py
python scripts/audit.py --config configs/audit.json
python scripts/build_audit_notebook.py
python scripts/build_audit_notebook.py --inspect-csv --output notebooks/kaggle_audit_csv.ipynb
python scripts/build_phase_b_notebook.py
python scripts/build_phase_c_notebook.py
```

The audit defaults to discovery only. Missing local competition files produce an explicit incomplete result and nonzero exit status. Results are written to unique JSON files under `artifacts/`. A valid synthetic check or notebook does not establish that competition inputs passed an audit.

Review [data contracts](docs/data_contract.md), [Kaggle execution](docs/kaggle.md), [Phase B scope](docs/phase_b_audit.md), and [Phase C report supervision](docs/phase_c_report_supervision.md). Phase C evaluates separate report candidates without modifying reference labels. Training, approved splits, external assets and future weak-supervision use require further review.

The [initial scaffold review](docs/scaffold_review.md) records the actual local validation: 80 tests passed and one native symlink test was skipped. This is software evidence on synthetic fixtures; it is not a dataset or model result.

## Architecture

The implemented scaffold establishes auditable boundaries for later experiments.

```mermaid
flowchart LR
    A[Validated configuration] --> B[Bounded input discovery]
    B --> C[Source identity and inventory]
    C --> D[Optional CSV preflight]
    D --> E[Schema and reference checks]
    D --> F[Labels and observation masks]
    E --> G[Explicit audit evidence]
    F --> G
    H[Synthetic fixtures] --> B
    H --> D
    I[Standalone notebook builder] --> J[Offline Kaggle audit entry point]
    J --> B
```

The later image-model architecture remains a proposal, conditional on real-data and validation gates.

```mermaid
flowchart LR
    A[DICOM series and metadata] --> B[Decode and validate geometry]
    B --> C[Physical slice or window sampling]
    C --> D[Compact image encoder]
    D --> E[Study pooling]
    E --> F[Twelve prediction heads]
    F --> G[Twelve probabilities]
```

## Workflow and approval gates

```mermaid
flowchart LR
    A[Initial scaffold] --> B[Approved real-data audit]
    B --> C[Verified labels and frozen splits]
    C --> D[Approved model development]
    D --> E[Training and OOF evidence]
    E --> F[Candidate selection]
    F --> G[Offline inference bundle]
    G --> H[Schema and runtime verification]
    H --> I[submission.csv]
```

The scaffold and Phase B audit software are implemented. Local synthetic tests do not establish actual DICOM support. No approved validation split, model, checkpoint, or submission inference has been produced. The submission validator checks a supplied probability table; it does not generate predictions.

```mermaid
flowchart LR
    A[Read-only attached inputs] --> B[CSV preflight and hashes]
    B --> C[Manifest series directory consistency]
    C --> D[Stratified bounded file sample]
    D --> E[Optional pydicom headers]
    E --> F[Up to 12 guarded pixel smoke tests]
    B --> G[Report aggregates and label support]
    C --> H[Compact JSON with coverage and limits]
    F --> H
    G --> H
    H --> I[Human review of audit and validation design]
```

## Repository guide

| Location | Purpose |
|---|---|
| `src/rsna_knee/` | Configuration, discovery, contracts, Phase B audit, metric and submission checks |
| `configs/audit.json` | Conservative discovery configuration |
| `scripts/` | Local checks, preflight entry point, deterministic notebook generation |
| `tests/` | Small synthetic fixtures and regression checks |
| `notebooks/kaggle_audit.ipynb` | Generated notebook with cleared execution state |
| `notebooks/kaggle_audit_csv.ipynb` | Generated CSV preflight notebook |
| `notebooks/kaggle_phase_b_audit.ipynb` | Generated read-only series, report, DICOM and validation audit |
| `notebooks/kaggle_phase_c_report_audit.ipynb` | Frozen report-candidate agreement and prospective validation design |
| [Architecture](docs/architecture.md) | Module boundaries and future gates |
| [Data contract](docs/data_contract.md) | Identifiers, labels, paths, source identity, and coverage |
| [Validation protocol](docs/validation_protocol.md) | Undefined metrics and leakage protections |
| [Kaggle workflow](docs/kaggle.md) | Browser execution of the self-contained notebook |
| [Asset registry](docs/asset_registry.md) | External-asset status and approval requirements |

Keep datasets, DICOMs, credentials, environment secrets, caches, checkpoints, virtual environments, and large artifacts out of Git. Source inputs remain immutable. Review compact evidence before retaining it deliberately.

## Results

The Kaggle evidence establishes sparse support and sampled decoding success. Phase C will measure exploratory report-rule agreement on observed cells only, with denominators and Wilson intervals; actual agreement is pending the Kaggle run. No model metrics or independent validation estimates have been measured.

| Target | Validation positives / negatives | ROC AUC |
|---|---|---|
| ACL | Not measured | — |
| MCL | Not measured | — |
| Medial Meniscus | Not measured | — |
| Lateral Meniscus | Not measured | — |
| Medial OA | Not measured | — |
| Lateral OA | Not measured | — |
| PF OA | Not measured | — |
| Effusion | Not measured | — |
| Synovitis | Not measured | — |
| Baker's | Not measured | — |
| Contusion | Not measured | — |
| Fracture | Not measured | — |
| **Macro across all twelve** | **Not measured** | **Not measured** |

## Current limitations

- The supplied evidence describes one Kaggle runtime and bounded image samples; it does not establish full DICOM integrity or comprehensive codec support.
- Synthetic checks cannot establish real dataset integrity, label quality, DICOM compatibility, or clinical validity.
- Input scans are bounded. Missing inputs, ambiguous roots, unsafe paths, and incomplete coverage must stay visible.
- The separate gold resource remains **GOLD_RESOURCE_NOT_LOCATED**. An expected study count is not evidence of its existence.
- Patient identity, report-rule reliability and simultaneous validation support remain unresolved. English pattern coverage and script heuristics are limited; unknown candidates and true labels remain masked.
- The original notebook defaults to discovery; Phase B samples images; Phase C reads CSVs and reports only. Training, downloads, network requests and pretrained assets are outside these notebooks.

## References and provenance

The competition is [RSNA Knee Abnormality Detection](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection). Check its [data page](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/data) and [evaluation page](https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/overview/evaluation) in an authenticated browser before relying on live rules or access.

The Mermaid diagrams were written for this rebuild. No external visual assets, datasets, teachers, or pretrained weights are included. A repository code license has not been selected; competition data and any future external assets retain separate usage terms.
