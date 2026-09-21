# IEEE-CIS adapter validation

> Validation date: 2026-09-21  
> Source: authenticated IEEE-CIS Fraud Detection competition files  
> Configuration: `configs/ieee_cis.yaml`

## Result

The IEEE-CIS adapter passed its schema, row-preservation, canonical-contract, point-in-time feature, chronological split, reproducibility, model-training, MLflow, and live API gates. The dataset can be used by the current GraphRisk pipeline.

This validates competition-dataset integration. It does not establish production fraud performance or recover real customer identities.

## Source validation

| Source file | Data rows | Role |
|---|---:|---|
| `train_transaction.csv` | 590,540 | Labeled transaction source |
| `train_identity.csv` | 144,233 | Optional identity attributes |
| `test_transaction.csv` | 506,691 | Unlabeled Kaggle inference only |
| `test_identity.csv` | 141,907 | Unlabeled identity attributes |
| `sample_submission.csv` | 506,691 | Kaggle submission template only |

The adapter verified every required source column. `TransactionID` is unique for all 590,540 training transactions, and the left join retained all rows. The canonical result contains 20,663 fraud rows, matching the source label count.

## Canonical output

| Metric | Value |
|---|---:|
| Rows | 590,540 |
| Unique transaction IDs | 590,540 |
| Fraud rows | 20,663 |
| Fraud rate | 3.4990% |
| Source `TransactionDT` range | 86,400–15,811,131 seconds |
| Missing canonical identity keys | 0 |
| Transaction-unique device fallbacks | 449,685 |
| Transaction-unique email fallbacks | 83,392 |
| Transaction-unique address fallbacks | 65,706 |
| Transaction-unique card fallbacks | 0 |

Missing source entities receive transaction-unique fallback IDs so unrelated transactions are not connected through a shared `unknown` node.

`TransactionDT` has no published real calendar origin. The adapter uses `2000-01-01T00:00:00Z` as an artificial anchor while preserving the exact source ordering and elapsed intervals. Generated calendar dates and day-of-week values are not real event dates.

## Feature and split validation

```powershell
uv run --offline python scripts\build_features.py --config configs\ieee_cis.yaml
```

| Split | Rows |
|---|---:|
| Train | 413,378 |
| Validation | 88,581 |
| Test | 88,581 |

The full pipeline completed in approximately seven seconds on this machine. It was run twice; all canonical, feature and split Parquet files retained identical SHA-256 hashes:

| Artifact | SHA-256 |
|---|---|
| Canonical transactions | `0106569663D4E1DD5BCA6E241DEBF520291AE3CB3E4E1B9ACD112055C08B2720` |
| All historical features | `9659D9EBD34829D974CB2E3E013E420BC8CC3FA875D37CF5A605D884D0538441` |
| Train split | `3CF099990F3CFDFE748B899CFB3C71E1CA44978C385CBC78455FC6EF436D3438` |
| Validation split | `261B5BAA879609B805508382AF4305F6B3B7198F7AEA74B8450658DEE611C7F5` |
| Test split | `1D1051966748D16075DC8389EDFCDCD9EDBB381660BF2654FF656DE1D6173566` |

## Stage 02 compatibility

```powershell
uv run --offline python scripts\train_baselines.py --config configs\train_baseline_ieee_cis.yaml
uv run --offline python scripts\list_mlflow_runs.py --config configs\train_baseline_ieee_cis.yaml
```

All three MLflow runs finished successfully. The champion was selected only by validation PR-AUC.

| Model | Validation PR-AUC | Test PR-AUC | Test ROC-AUC | Test Recall | Test Recall@Top-5% | P95 model latency |
|---|---:|---:|---:|---:|---:|---:|
| Dummy | 0.0343 | 0.0348 | 0.5000 | 0.0000 | 0.0399 | 601.6 ms |
| Logistic Regression | 0.1473 | 0.0766 | 0.7063 | 0.4213 | 0.1700 | 650.3 ms |
| XGBoost | **0.1736** | **0.1522** | **0.7648** | **0.7541** | **0.2848** | 722.9 ms |

The fixed comparison snapshot is stored in `reports/ieee-cis-baseline-results.csv`. These models intentionally use the existing compact canonical feature set; using the full anonymized IEEE-CIS feature space is a later modeling enhancement.

## Live API validation

The API loaded the separate IEEE-CIS XGBoost champion and feature store through environment overrides:

| Check | Result |
|---|---|
| `GET /health` | 200, artifacts available |
| `GET /model/info` | 200, `xgboost`, source `ieee_cis` |
| `POST /risk/predict`, transaction `2987000` | 200, score `0.299632`, level `low` |
| End-to-end request time | approximately 802 ms |

The response contract is compatible, but single-row preprocessing is too slow for the final service target. This is a measured performance limitation to address before the delivery stage, not an adapter failure.

## Automated verification

```text
uv run --offline pytest                            31 passed
uv lock --check --offline                         PASS
uv run --offline python -m compileall ...          PASS
```

The adapter tests cover source-column validation, identity joining, relative-time preservation, missing-entity isolation, invalid-label rejection, and the end-to-end IEEE-CIS pipeline branch.
