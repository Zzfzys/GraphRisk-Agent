# GraphRisk-Agent

GraphRisk-Agent is a portfolio-grade fraud investigation system centered on a reliable LLM Agent. The Agent will combine transaction features, XGBoost, GraphSAGE, graph queries, and policy RAG to produce evidence-grounded investigation reports with human approval for high-risk actions.

The approved scope and delivery gates are defined in [PROJECT_PLAN.md](PROJECT_PLAN.md). Detailed stage status is maintained in [docs/plans/README.md](docs/plans/README.md).

## Current status

Stages 00 and 01 are complete. The repository contains an installable package and a deterministic point-in-time data pipeline. Stage 02 model development has not started.

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

## Secrets

Copy `.env.example` to `.env` and fill values locally when a later stage needs external services. `.env`, credentials, model weights, raw data, generated artifacts, and local environments are ignored by Git.
