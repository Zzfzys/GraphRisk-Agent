# GraphRisk-Agent

GraphRisk-Agent is a portfolio-grade fraud investigation system centered on a reliable LLM Agent. The Agent will combine transaction features, XGBoost, GraphSAGE, graph queries, and policy RAG to produce evidence-grounded investigation reports with human approval for high-risk actions.

The approved scope and delivery gates are defined in [PROJECT_PLAN.md](PROJECT_PLAN.md). Detailed stage status is maintained in [docs/plans/README.md](docs/plans/README.md).

## Current status

Stages 00 through 02 are complete. The repository contains an installable package, a deterministic point-in-time data pipeline, three tabular baselines with MLflow tracking, and a FastAPI risk prediction service. Stage 03 investigation tools have not started.

## Prerequisites

- Git
- uv
- Windows PowerShell for the provided environment diagnostic script

## Bootstrap

```powershell
uv python install 3.11 --install-dir .uv-python --cache-dir .uv-cache
$projectPython = (Get-ChildItem -LiteralPath ".uv-python" -Filter "python.exe" -Recurse | Select-Object -First 1).FullName
uv sync --locked --python $projectPython
uv run --offline pytest
```

The managed Python runtime, package cache, and virtual environment remain inside the repository and are excluded from Git. `uv.toml` keeps uv's cache inside the workspace so the same commands work in restricted environments.

## Environment diagnostic

```powershell
powershell -ExecutionPolicy Bypass -File scripts\diagnose_environment.ps1
```

## Data

Raw datasets are never committed. See:

- [Data access](docs/data/data-access.md)
- [Synthetic fallback](docs/data/synthetic-fallback.md)
- [Storage budget](docs/data/storage-budget.md)

## Stage 01 data pipeline

Generate the deterministic synthetic fallback dataset:

```powershell
uv run --offline python scripts\generate_synthetic_data.py --config configs\dev.yaml
```

Build normalized data, historical features, chronological splits, and the feature report:

```powershell
uv run --offline python scripts\build_features.py --config configs\dev.yaml
```

Generated outputs are intentionally ignored by Git:

```text
data/raw/synthetic/transactions.csv
data/interim/transactions.parquet
data/interim/features_all.parquet
data/processed/train_features.parquet
data/processed/validation_features.parquet
data/processed/test_features.parquet
reports/generated/feature_report.md
```

The canonical schema and time semantics are documented in [docs/data/data-dictionary.md](docs/data/data-dictionary.md).

### IEEE-CIS data pipeline

Place the authenticated competition CSV files under `data/raw/ieee-cis/`, then build separate real-data artifacts without overwriting the synthetic pipeline:

```powershell
uv run --offline python scripts\build_features.py --config configs\ieee_cis.yaml
```

The adapter joins `train_transaction.csv` and `train_identity.csv` on `TransactionID`, preserves `TransactionDT` ordering, creates canonical graph entities, and writes chronological splits under `data/processed/ieee-cis/`. See [the adapter specification](docs/data/ieee-cis-adapter.md) and [full validation evidence](reports/ieee-cis-adapter-validation.md).

## Stage 02 model training and API

Train Dummy, Logistic Regression, and XGBoost on the same chronological splits. The command selects the champion by validation PR-AUC, writes ignored local model artifacts, and records each run in local MLflow SQLite storage:

```powershell
uv run --offline python scripts\train_baselines.py --config configs\train_baseline.yaml
uv run --offline python scripts\list_mlflow_runs.py --config configs\train_baseline.yaml
```

Start the risk service after training:

```powershell
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The service exposes:

- `GET /health` for artifact readiness;
- `GET /model/info` for champion metadata and evaluation metrics;
- `POST /risk/predict` with `{"transaction_id": "T00000001"}` for a score, risk level, and model version.

Stage 02 used the synthetic fallback data. Its metrics verify the engineering and evaluation pipeline only. See [the Stage 02 validation report](reports/stage-02-validation.md) for the model comparison, API evidence, and known limits.

The same baselines can be trained independently on the adapted IEEE-CIS data:

```powershell
uv run --offline python scripts\train_baselines.py --config configs\train_baseline_ieee_cis.yaml
uv run --offline python scripts\list_mlflow_runs.py --config configs\train_baseline_ieee_cis.yaml
```

## Secrets

Copy `.env.example` to `.env` and fill values locally when a later stage needs external services. `.env`, credentials, model weights, raw data, generated artifacts, and local environments are ignored by Git.
