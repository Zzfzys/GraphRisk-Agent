# GraphRisk-Agent repository instructions

## Project objective

Build a portfolio-grade fraud investigation Agent for LLM/Agent application engineering roles. The Agent must ground its output in transaction data, ML/GNN model results, graph evidence, and cited policy documents. High-risk actions require human approval.

## Environment

- Python 3.11 only;
- Use uv and the checked-in `uv.lock`;
- Keep uv cache, managed Python, and the virtual environment inside the repository;
- Run tests with `uv run --offline pytest` after dependencies are synchronized;
- Do not introduce a new framework when an existing dependency covers the requirement.

## Source layout

- Application code: `src/graphrisk/`;
- Executable wrappers: `scripts/`;
- Configuration: `configs/`;
- Tests: `tests/unit/` and `tests/integration/`;
- Stage plans and evidence: `docs/plans/`;
- Data contracts and provenance: `docs/data/` and `data/provenance.example.json`;
- Generated data, reports, model files, local environments, caches, and secrets are not committed.

## Data correctness

- Split fraud data chronologically; never shuffle before train/validation/test assignment;
- Historical features may only use events with earlier event time;
- Treat equal timestamps as simultaneous unless a documented source ordering exists;
- Do not use labels in feature construction;
- Training graphs must not contain future validation or test relationships;
- Keep synthetic and real-dataset metrics separate;
- `identity_key` is a heuristic, not a verified customer ID.

## Engineering rules

- New behavior requires meaningful tests;
- Tools and data functions use explicit inputs and structured outputs;
- Fail validation with actionable errors instead of silently coercing invalid data;
- Keep secrets out of source, logs, tests, and documentation;
- Prefer small deterministic fixtures for tests;
- Update the active stage document whenever scope, behavior, evidence, or a decision changes;
- Update `PROJECT_PLAN.md` when a change affects multiple stages or the overall scope;
- Do not mark a stage done until every acceptance criterion has evidence.

## Current commands

```powershell
uv sync --locked
uv run --offline pytest
uv run --offline python scripts\generate_synthetic_data.py --config configs\dev.yaml
uv run --offline python scripts\build_features.py --config configs\dev.yaml
uv run --offline python scripts\build_features.py --config configs\ieee_cis.yaml
uv run --offline python scripts\train_baselines.py --config configs\train_baseline.yaml
uv run --offline python scripts\train_baselines.py --config configs\train_baseline_ieee_cis.yaml
uv run --offline python scripts\list_mlflow_runs.py --config configs\train_baseline.yaml
uv run --offline python scripts\run_investigation.py 2987100 --data-config configs\ieee_cis.yaml --training-config configs\train_baseline_ieee_cis.yaml --relation-limit 3
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```
