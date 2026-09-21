# GraphRisk-Agent

GraphRisk-Agent is a portfolio-grade fraud investigation system centered on a reliable LLM Agent. The Agent will combine transaction features, XGBoost, GraphSAGE, graph queries, and policy RAG to produce evidence-grounded investigation reports with human approval for high-risk actions.

The approved scope and delivery gates are defined in [PROJECT_PLAN.md](PROJECT_PLAN.md). Detailed stage status is maintained in [docs/plans/README.md](docs/plans/README.md).

## Current status

Stage 00 is in progress. Business models and Agent features have not started.

## Prerequisites

- Git
- uv
- Windows PowerShell for the provided environment diagnostic script

## Bootstrap

```powershell
uv python install 3.11 --install-dir .uv-python --cache-dir .uv-cache
$projectPython = (Get-ChildItem -LiteralPath ".uv-python" -Filter "python.exe" -Recurse | Select-Object -First 1).FullName
uv sync --locked --python $projectPython --cache-dir .uv-cache
uv run --offline pytest
```

The managed Python runtime, package cache, and virtual environment remain inside the repository and are excluded from Git.

## Environment diagnostic

```powershell
powershell -ExecutionPolicy Bypass -File scripts\diagnose_environment.ps1
```

## Data

Raw datasets are never committed. See:

- [Data access](docs/data/data-access.md)
- [Synthetic fallback](docs/data/synthetic-fallback.md)
- [Storage budget](docs/data/storage-budget.md)

## Secrets

Copy `.env.example` to `.env` and fill values locally when a later stage needs external services. `.env`, credentials, model weights, raw data, generated artifacts, and local environments are ignored by Git.
