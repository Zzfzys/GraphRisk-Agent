# GraphRisk-Agent

[中文说明](README.zh-CN.md) · [Startup guide](docs/data/stage-10-startup.md) · [Architecture](docs/architecture/stage-10-system.md) · [Evaluation](reports/stage-10-final-evaluation.md)

A portfolio fraud-investigation application for LLM/Agent engineering roles. A bounded LangGraph workflow collects transaction history, XGBoost risk scores, experimental GraphSAGE signals, entity relationships, and cited simulated policies. It validates structured model judgments and produces reports for human review.

## What you can inspect

- Transaction and strictly earlier history; simultaneous events are excluded from history.
- XGBoost as the primary risk model; GraphSAGE remains experimental after failing the fusion promotion gate.
- Evidence, policy versions, citations, unknowns, and approval/rejection in a Streamlit workbench backed by FastAPI.
- Memory storage or explicit PostgreSQL reports/checkpoints; optional pgvector policy retrieval.
- A separate [Enterprise GraphRAG](projects/enterprise-graphrag/README.md) project over original simulated IT documents.

Human decisions are recorded against a report hash and approval session. The application executes no account freezing or transaction blocking. Tool order is controlled by code; the project does not claim open-ended model planning or production authorization.

## Current evidence — 2026-10-18

| Evaluation | Result | Scope |
|---|---|---|
| Primary XGBoost | Mean AP **0.544367**, ROC-AUC **0.905089** | Three temporal outer folds; historical, observed data |
| GraphSAGE fusion | **No promotion** | All three calibration folds select XGBoost weight 1; AP gain 0 |
| Current real local Agent | **6/10 Fail**, threshold 9/10 | Ten first cases; two fixed repeats pass separately; four invalid outputs blocked |
| Independent GraphRAG | text **16/22**, graph/hybrid **18/22** | Small, previously used simulated corpus/question set; hybrid ties graph |
| End-to-end HTTP | Median **11.36s**, n=12, four HTTP 503 rejections | Local serial measurement; small-sample P99 unknown |

Definitions, versions, denominators and private-source bindings are in [claims E01–E12](docs/data/stage-10-final-claims.md). [CI at 0e326b0](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39042905865) passed six jobs: 1,035 tests across separate layers, one platform skip; 15 additional mechanism probes are separate. CI success does not establish model quality. Later documentation commits have their own version status.

Stages 01–09 have engineering evidence; personal learning/review remains pending. Stage10 routes0–6 engineering delivery is complete; route7 career/interview/review materials are ready, with personal review and rehearsal pending; route8 remains planned. A new checkout and two new environments passed cached-offline reproduction; cold-cache installation and raw-data retraining were not tested. The user selected actual page pictures instead of video; the [picture walkthrough](docs/data/stage-10-picture-demo.md) replays saved enhanced-profile reports with zero new inference. Current Agent quality is still below its frozen gate. [Known limitations](docs/data/stage-10-known-limitations.md) and [stage status](docs/plans/README.md) stay explicit.

## Start with the public mechanism demo

Choose a writable project drive, install Git and uv, and open PowerShell in the directory where you want the repository. Python **3.11** is required by the project. The first dependency sync needs the configured package indexes unless everything is cached.

```powershell
git clone https://github.com/Zzfzys/GraphRisk-Agent.git GraphRisk-Agent
Set-Location GraphRisk-Agent
git switch codex/stage09-version-closeout
. .\scripts\enter_project_env.ps1 | Out-Null
uv sync --locked --group demo
uv run --offline python scripts/evaluate_stage06_agent.py
```

Expected: `fake=50/50 real_samples=0`, with a timestamped JSON under `reports/generated/stage06/`. This is a CLI demonstration of report, citation, failure and approval mechanisms using deterministic fixtures. It requires neither IEEE-CIS nor Ollama, Docker, a paid key, or trained fraud weights. It does not start a website or measure real LLM quality. Keep `--real` out of this public fixture command.

For synthetic data generation, real local investigations, persistent storage, and historical replay, follow the [startup guide](docs/data/stage-10-startup.md). The first install can be substantial because the root project includes PyTorch. The independent GraphRAG [corpus validator](docs/data/stage-10-startup.md#independent-graphrag) offers a smaller standard-library-only entry point.

The author's C-drive source/E-drive runtime arrangement has [separate commands](docs/data/stage-10-author-workspace.md). Those absolute paths are not prerequisites for another machine.

## Choose a running mode

| Mode | Prerequisites | What it demonstrates |
|---|---|---|
| Public CLI fixture/synthetic | Checked-in files and locked dependencies | Engineering behavior/data pipeline; zero real model quality claims |
| Current real investigation | Authorized IEEE-CIS, bound feature/model/graph/policy artifacts, pinned local Ollama | Current enhanced XGBoost/experimental graph profile; creation sends real model requests |
| PostgreSQL investigation | Real-mode inputs plus migrated database, restricted role and private DSN | Report/checkpoint persistence, idempotency and approval recovery |
| Historical report replay | Saved private reports, Docker and pinned database image | Old real report content with new local approval practice; zero new inference |

The default local coverage launcher explicitly uses **memory**. Reports and approvals are lost on API restart; old run IDs can return 410. PostgreSQL is opt-in, does not silently fall back to memory, and has a stricter partial-evidence approval boundary. See [mode contracts and sequence diagrams](docs/architecture/stage-10-system.md).

## Repository and evidence

| Path | Role |
|---|---|
| `src/graphrisk/`, `api/`, `streamlit_app.py` | Application, HTTP routes and workbench |
| `configs/`, `scripts/`, `tests/` | Versioned configuration, executable entrypoints and checks |
| `docs/data/`, `docs/architecture/` | Contracts, cards, operating guides and diagrams |
| `docs/plans/`, `reports/` | Stage index and public acceptance/evaluation reports |
| `projects/enterprise-graphrag/` | Independent GraphRAG package, corpus, tests and workbench |

Raw transactions, model weights, prompts, row-level outputs, secrets, caches, local environments and generated evidence are excluded from Git. Obtain dataset permissions and generate/obtain the exact bound artifacts before using real mode. Merely downloading the Kaggle CSV files does not produce a ready investigation service.

Start each Windows project shell with `scripts/enter_project_env.ps1`; it sets only that process's TEMP/TMP, caches and local proxy exclusions. [Testing and troubleshooting](docs/data/stage-10-startup.md#checks-and-troubleshooting) explain optional database tests, liveness/readiness and startup failures. [Route6 reproduction](docs/data/stage-10-clean-reproduction.md) passed in a new checkout/new environment using an existing download cache: CPU915/one platform skip, public50-case fixtures/synthetic pipeline, and existing real-artifact checks. Historical byte-hash checks require the explicit frozen source snapshot when checkout line endings differ. Cold-cache and raw-data retraining remain untested.

The roadmap is in [PROJECT_PLAN.md](PROJECT_PLAN.md). Begin learning with [L4's 90-minute walkthrough](docs/data/stage-10-architecture-learning.md); follow the [stage index](docs/plans/README.md) for historical experiments and acceptance rather than treating older scores as current results.

## Career walkthrough

[Project statements and sample talks](docs/career/stage-10-presentation.md), [20 interview drills](docs/career/stage-10-interview-drills.md), and [human review instructions](docs/career/stage-10-human-review.md) are available. They are preparation materials, not evidence that the author has completed personal rehearsal or ten usefulness reviews. Current first-case Agent quality remains 6/10 Fail.
