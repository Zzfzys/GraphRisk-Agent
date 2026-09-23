# GraphRisk-Agent

GraphRisk-Agent is a portfolio-grade fraud investigation system centered on a reliable LLM Agent. The Agent will combine transaction features, XGBoost, GraphSAGE, graph queries, and policy RAG to produce evidence-grounded investigation reports with human approval for high-risk actions.

The approved scope and delivery gates are defined in [PROJECT_PLAN.md](PROJECT_PLAN.md). Detailed stage status is maintained in [docs/plans/README.md](docs/plans/README.md).

## Current status

Stages 00 through 03 are complete. The repository contains an installable package, a deterministic point-in-time data pipeline, three tabular baselines with MLflow tracking, a FastAPI risk prediction service, and a deterministic fraud investigation workflow with typed tools, evidence bindings and separate audit events. Stage 04 routes 0 through 6 have completed the graph environment gate, strict-history graph schema, reproducible IEEE-CIS graph artifacts for 50k/10k/10k target transactions, a versioned GraphSAGE training run, same-ID model comparison with embeddings, a verified graph-risk neighbor tool, and its deterministic workflow integration. Route 7 remains the stage-level acceptance and retrospective.

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

The managed Python runtime, package cache, and virtual environment remain inside the repository and are excluded from Git. `pyproject.toml [tool.uv]` keeps uv's cache inside the workspace; the explicit PyTorch CUDA 12.8 index is configured there too. The first locked sync downloads a large PyTorch wheel.

Check the Stage 04 graph environment after the IEEE-CIS Stage 01/02 artifacts exist:

```powershell
uv run --offline python scripts\check_graph_environment.py --config configs\graph_ieee_cis.yaml
```

The command verifies seven upstream artifacts and runs a small PyG GraphSAGE layer on CPU and the selected device. See [route 0 evidence](reports/stage-04-route-0-preflight.md).

The Stage 04 graph schema, allowed features and strict-history fixture are documented in [the graph schema](docs/data/stage-04-graph-schema.md) and [route 1 evidence](reports/stage-04-route-1-schema-time.md).

Build the Stage 04 temporal graph artifacts after the upstream data and baseline artifacts exist:

```powershell
uv run --offline python scripts\build_graph_artifacts.py --config configs\graph_ieee_cis.yaml
```

The command writes ignored Parquet, ID, encoder and manifest files under `artifacts/stage-04/ieee-cis`. See the [artifact contract](docs/data/stage-04-graph-artifacts.md) and [route 2 evidence](reports/stage-04-route-2-artifacts.md).

Train GraphSAGE on those fixed graph targets:

```powershell
uv run --offline python scripts\train_graphsage.py --config configs\graph_ieee_cis.yaml
```

The versioned checkpoint and training history remain under the ignored artifact directory. See the [training contract](docs/data/stage-04-graphsage-training.md) and [route 3 evidence](reports/stage-04-route-3-training.md).

For your own validation-only tuning runs, follow the [GraphSAGE self-training guide](docs/guides/graphsage-self-training.md). Trial runs use separate names and do not read held-out test labels.

Compare GraphSAGE with a controlled XGBoost trained on the same 50k target IDs, then select a fixed-weight fusion using validation data:

```powershell
uv run --offline python scripts\compare_graphsage.py --config configs\graph_ieee_cis.yaml --run-name my-first-comparison
```

Use a new run name for each repeat. Outputs are ignored under `artifacts/stage-04/ieee-cis/comparisons/<run-name>/`. See the [comparison contract](docs/data/stage-04-model-comparison.md) and [route 4 evidence](reports/stage-04-route-4-comparison.md). The first controlled test did not show a fusion PR-AUC improvement over XGBoost.

Query held-out, strictly earlier scored neighbors with the route 5 graph service:

```powershell
uv run --offline python scripts\query_graph_neighbors.py 3489059 --config configs\graph_ieee_cis.yaml --comparison-run same-id-v4 --limit 5
```

See the [graph service contract](docs/data/stage-04-graph-service.md) and [route 5 evidence](reports/stage-04-route-5-graph-service.md). A training target has no eligible held-out score and returns a structured unavailable result.

Run the full five-tool investigation with the verified graph service and same-ID controlled fusion:

```powershell
uv run --offline python scripts\run_investigation.py 3489059 --data-config configs\ieee_cis.yaml --training-config configs\train_baseline_ieee_cis.yaml --relation-limit 3 --report-output reports\generated\stage04-report.json --audit-output reports\generated\stage04-audit.json
```

For IEEE-CIS, the CLI uses `configs/graph_ieee_cis.yaml` and comparison run `same-id-v4` by default. `--graph-config` and `--comparison-run` override them. The report contains separate Stage 02 XGBoost and route 4 controlled XGBoost scores; the fusion uses only the controlled score and the validation-selected alpha. See the [workflow contract](docs/data/stage-04-investigation-workflow.md) and [route 6 evidence](reports/stage-04-route-6-workflow.md). Raw reports and audits stay under ignored `reports/generated/`.

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

To serve the IEEE-CIS champion instead of the default synthetic champion in PowerShell:

```powershell
$env:GRAPHRISK_MODEL_DIR = (Resolve-Path 'artifacts\stage-02\ieee-cis\champion').Path
$env:GRAPHRISK_FEATURE_DATA = (Resolve-Path 'data\interim\ieee-cis\features_all.parquet').Path
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```

## Stage 03 deterministic investigation

Run a complete read-only IEEE-CIS investigation without an LLM:

```powershell
uv run --offline python scripts\run_investigation.py 2987100 `
  --data-config configs\ieee_cis.yaml `
  --training-config configs\train_baseline_ieee_cis.yaml `
  --relation-limit 3 `
  --report-output reports\generated\stage-03-investigation.json `
  --audit-output reports\generated\stage-03-audit.json
```

The report contains transaction facts, strictly historical features, the tabular risk result, strictly earlier shared-entity evidence, evidence bindings, capability gaps and interpretation limits. The audit records tool order, status and duration separately so runtime metadata does not change deterministic report content. Generated reports remain ignored by Git. See the [Stage 03 validation report](reports/stage-03-validation.md) for acceptance evidence and known limits.

## Secrets

Copy `.env.example` to `.env` and fill values locally when a later stage needs external services. `.env`, credentials, model weights, raw data, generated artifacts, and local environments are ignored by Git.
