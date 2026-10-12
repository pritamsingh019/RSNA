# Validation protocol

## Current status

Synthetic tests validate software behavior. No competition split, training run, reference set, or model score has been established. This protocol defines gates before model development.

The user reports 58 observed labels with both classes and 4,349 unknown labels per target in the successful Kaggle CSV audit. Phase B recomputes exact positive/negative support without changing masks. Necessary two/three-fold support does not prove joint feasibility; tiny exact class-coverage diagnostics are not approved splits. Dataset-wide patient grouping and an independent gold resource remain unresolved. Sparse labels may prevent a reliable twelve-target validation estimate; validation design must be reviewed before training. See [Phase B audit](phase_b_audit.md).

## Labels and metrics

Reference AUC uses verified observed labels only. Missing labels are unknown. Future agreement with a report teacher is a separate diagnostic and cannot substitute for independent image-model evaluation.

Publish positive and negative support per target. Binary AUC is undefined without an observed positive or an observed negative. The full twelve-target macro is unavailable if any constituent AUC is undefined. Any partial macro must name its eligible target count and exclusions.

Tied scores receive half credit. Scores must be finite. A constant-score fixture with both classes present has AUC 0.5; this is software behavior, not a measured competition result.

## Grouping and leakage

Keep every series and slice of a study together. Group repeat studies by patient when trustworthy identity exists. Investigate duplicate identities and images. If reliable patient grouping is unavailable, disclose residual patient-leakage risk.

Choose folds after measuring support; do not assume five folds are feasible. Freeze membership, label definitions, eligibility, target order, and comparison criteria before experiments. Record split hashes and source identity.

Class weights, fitted preprocessing, calibration, report rules, pseudo-label refinement, and domain adaptation must respect training-fold boundaries. Test images provide inference inputs, not validation labels or promotion criteria.

## Report and gold development

Gold cases used to tune extraction rules or weak-label confidence become development data. They cannot also provide an untouched image-model estimate. Use independent reference data or fit extraction inside each outer training fold.

A supplied gold resource needs provenance and overlap checks. Its expected count establishes neither annotation quality nor independence. Record every reference-set reuse in an experiment ledger.

## Comparisons

Compare paired predictions on identical studies, masks, and folds. Examine per-target changes, full-macro availability, fold and seed consistency, and eligibility changes. Investigate gains driven by a few positives.

Estimate uncertainty at the study or patient group level. State support, bootstrap design, invalid resamples, and undefined metrics. Repeated holdout evaluation does not create independent evidence. Limit early experiments to a baseline and at most two controlled improvements.

## Scaffold checks

```powershell
python scripts/check_scaffold.py
```

Synthetic checks cover configuration, input boundaries, references, label masks, source immutability, undefined AUC, and submission errors. Notebook checks cover deterministic generation, clean execution state, self-contained source, and execution on small fixtures. Report actual run results; these checks cannot make a real-data audit PASS or create model metrics.
