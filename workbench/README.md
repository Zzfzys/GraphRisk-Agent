# GraphRisk-Agent

> **当前入口：[CURRENT.md](CURRENT.md)。** 当前源码位置、网页启动方式、采用版本和下一步统一从该文件读取。下文保留项目介绍及历史阶段记录，不能据此选择本次网页启动版本。


GraphRisk-Agent is a portfolio-grade fraud investigation system centered on a reliable LLM Agent. The Agent combines transaction features, XGBoost, GraphSAGE, graph queries, and policy RAG to produce evidence-grounded investigation reports with human approval for high-risk recommendations.

The approved scope and delivery gates are defined in [PROJECT_PLAN.md](PROJECT_PLAN.md). Detailed stage status is maintained in [docs/plans/README.md](docs/plans/README.md).

On Windows, start each project PowerShell terminal by dot-sourcing scripts/enter_project_env.ps1. This keeps project temporary files and Python download caches on the project drive for that terminal and its child processes. It does not change Windows-wide settings or move installed applications. It also adds local project endpoints to the process NO_PROXY list while preserving existing entries. See the [storage audit and cleanup guide](reports/storage-audit-20261007.md).

## Historical stage snapshots

Stages 00 through 07 have passed their engineering acceptance gates. The MVP combines chronological IEEE-CIS features, XGBoost, GraphSAGE graph evidence, simulated-policy RAG, a bounded LangGraph investigation, FastAPI, a Streamlit workbench, human approval, and Docker Compose. The frozen Agent suite has 50 deterministic cases plus three small local-model samples; [Stage 06 acceptance](reports/stage-06-route-7-acceptance.md) records the real held-out demonstration and limits. Stage 07 adds PostgreSQL recovery and an opt-in pgvector policy retriever. Its frozen v2 suite passed 15/15 on a fresh isolated database alongside the unchanged 50/50 baseline; [Stage 07 acceptance](reports/stage-07-route-8-acceptance.md) covers the real workbench, API restart, database faults, legacy paths, build, and data boundary. GraphSAGE and fusion did not exceed the controlled XGBoost test PR-AUC, and the displayed scores are uncalibrated.

[Stage08 Routes 4–5](reports/stage-08-route-5-retrieval.md) are complete: the independent GraphRAG project has 24 simulated IT documents, 40 sourced relations, 30 frozen questions and text/graph/hybrid retrieval. Two real local embedding builds are byte-identical. Complete evidence coverage is 19/22, 20/22 and 22/22 respectively; these are retrieval results, not model answer scores. All 51 independent contract tests pass. See the [independent project](projects/enterprise-graphrag/README.md) and [reproduction and learning exercises](docs/data/stage-08-graphrag-retrieval.md). [Route 6](reports/stage-08-route-6-grounded-answer.md) now adds bounded evidence selection, citation validation and deterministic fact rendering. All 82 independent tests pass; the final local development smoke answered 2/4 answerable cases, with the other two safely rejected. Four deterministic refusal branches behaved as expected. Routes 7–8 subsequently completed the frozen evaluation, UI and final acceptance; see the latest status below. See the [answering and learning guide](docs/data/stage-08-graphrag-answering.md).

Stage 08 engineering acceptance is complete (2026-10-07). Both local models completed the frozen 20-case provider comparison but scored 0/20 under its output contract; DeepSeek passed 20/20, with 20 additional paid token-calibration calls. The official hourly exports match all 40 requests and tokens exactly: Paid/CNY 0.0283512, versus the conservative internal estimate of 0.028383; per-request rounding explains the full difference. This is hourly aggregate reconciliation, not per-request billing. GraphRAG text/graph/hybrid answered 16/22, 18/22 and 18/22 cases; hybrid meets the gate and ties graph. Existing acceptance evidence includes root 470 passed/0 skipped, Stage07 15/15, Stage06 mechanism cases 50/50, independent core tests, demo UI and clean-install reproduction. See the [final acceptance](reports/stage-08-route-8-acceptance.md), [billing evidence](reports/stage-08-route-3-deepseek.md), [implementation roadmap](docs/plans/stage-08-implementation-roadmap.md) and [learning roadmap](docs/plans/stage-08-learning-roadmap.md). Personal learning exercises remain separately recorded. Stage09 starts with a real local Agent baseline across multiple transactions, followed by GraphSAGE implementation, sampling, feature ablations and tuning diagnostics.

Stage 07 route 4 adds a PostgreSQL-backed investigation mode. It persists the derived report, request/audit identifiers, local cost ledger, and LangGraph human-approval checkpoint, so a new API process can read the same `run_id` and finish a pending approval. The Stage 06 memory mode remains the default. PostgreSQL connection strings belong only in ignored local environment files; run the separate migration before enabling the API:

```powershell
uv sync --locked --offline --group demo
uv run --offline python scripts/migrate_stage07.py
# Set GRAPHRISK_STAGE06_CONFIG, GRAPHRISK_STAGE07_STORAGE=postgres,
# and GRAPHRISK_STAGE07_DSN in the local process environment.
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```

See the [PostgreSQL operating guide](docs/data/stage-07-postgres-service.md) for the limited application role, TTL cleanup, tests, and safe error behavior. The [route 4 evidence](reports/stage-07-route-4-postgres-recovery.md) covers a real held-out investigation and cross-instance human rejection. Route 5 also adds an opt-in pgvector policy backend. After migrating `scripts/migrate_policy_pgvector.py`, building the pinned dense index, importing it with `scripts/import_policy_pgvector.py`, and granting the app role, set `GRAPHRISK_STAGE06_CONFIG=configs/stage07_pgvector.yaml` with PostgreSQL storage. The default TF-IDF configuration remains unchanged. See the [pgvector operating guide](docs/data/stage-07-pgvector-retrieval.md) and [same-suite quality evidence](reports/stage-07-route-5-pgvector.md). Stage 07 engineering acceptance is complete; follow the [Compose guide](docs/data/stage-07-compose.md), [Chinese step-by-step demo](docs/data/stage-07-demo-guide.md), and [route 8 evidence](reports/stage-07-route-8-acceptance.md). The Stage 06 memory mode stays the default and returns 410 for runs after an API restart. Stage 07 PostgreSQL/pgvector is explicit; a database outage returns 503. PostgreSQL mode does not open approval for partial graph evidence, while the frozen legacy memory behavior remains. Reviewer identity is not authenticated, policies are simulated, scores are uncalibrated, and production multi-worker/backup recovery is untested.

## Stage 07 local Compose demo

Prepare the authorized IEEE-CIS/model/graph artifacts, pinned local Ollama models, and ignored secret files as described in the [Compose operating guide](docs/data/stage-07-compose.md). Stage 07 uses an independent Compose file, keeps the database port private by default, and binds the API/workbench to loopback:

~~~powershell
docker compose -f compose.stage07.yaml up -d --no-build
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
# Open http://127.0.0.1:8501 for transaction 3488959.
docker compose -f compose.stage07.yaml down
~~~

A normal down/up preserves the named database volume; do not remove that volume during restart practice. The [Chinese demo and 90-second interview guide](docs/data/stage-07-demo-guide.md) explains the pending approval restart, human rejection, no-policy case, and safe database failure. A prior isolated evaluation database returned 14/15 because a fixed idempotency key reused an expired run; the fresh isolated rerun passed 15/15. Keep evaluation databases isolated when reproducing the frozen suite.

## Secrets

Copy `.env.example` to `.env` and fill values locally when a later stage needs external services. `.env`, credentials, model weights, raw data, generated artifacts, and local environments are ignored by Git.

Earlier runtime recheck (2026-10-07, before recovery): 452 root tests passed and 18 database tests skipped; Docker/WSL failed with ERROR_NO_SYSTEM_RESOURCES. The later [Docker recovery recheck](reports/stage-01-08-docker-recheck-20261007.md) supersedes that blockage. Stage09 route0 completed isolated database checks; Docker was then stopped normally in route1 to free RAM. The latest native route4 regression is 557 passed/18 skipped without isolated DSNs; it does not rerun container/database acceptance.


### Stage09 路线3：采样与同根特征基线

[工程验收](reports/stage-09-route-3-sampling-baselines.md)与[60分钟学习讲义](docs/data/stage-09-sampling-baselines.md)已就绪：50k/10k、28维根向量，XGBoost/MLP各一次真实训练；路线3生成独立cap10图manifest；路线4已完成重训，路线5已接入独立配置。全量532 passed/18缺DSN数据库skipped，保存模型评分复现差异0；工程通过不代表L3本人完成或GNN结构增益已证实。

```powershell
. .\scripts\enter_project_env.ps1
uv run --offline python scripts/check_stage09_root_baselines.py --output-dir reports/generated/stage09/route3/20261007-baselines-v1
```

该命令只读，不重复训练；原评分模型仍保留。路线4已完成真实消融与调参，见下方新证据。

### Stage09 路线4：真实消融、有限调参与模型决策

[诊断报告](reports/stage-09-route-4-graphsage-diagnosis.md)与[90分钟学习讲义](docs/data/stage-09-graphsage-diagnosis.md)已就绪：12次真实CUDA训练、五项消融/基础、六组参数和胜出配置三个种子。平均验证AP 0.179247±0.009721；晋级=False；路线5已在独立配置接入实验图，旧配置保留。冻结验证决策后只做一次历史已观察测试复核；全量557 passed/18缺DSN skipped，12权重CPU重载通过。学习和路线1人类复核仍待本人完成。

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline python scripts/check_stage09_graphsage.py --output-dir reports/generated/stage09/route4/20261007-diagnosis-v4
~~~

这是只读验证，不新训或重读源测试标签；路线5已经完成，见下方真实重放/回滚证据。路线4收尾冻结属于历史；本路线实施的预期代码差异在路线5登记，当前复核使用路线5命令。


### Stage09 路线5：模型决策接入与真实同集重放

[验收与失败复盘](reports/stage-09-route-5-agent-replay.md)、[75分钟学习讲义](docs/data/stage-09-agent-replay.md)已就绪。显式 configs/stage09.yaml 使用XGBoost主评分和candidate-4实验性GraphSAGE，无融合；旧配置支持回滚。30笔首次+3固定重复都真实本地生成，18完整/10部分/2失败，来源绑定28/30，原始uncertainty仅10/30正确；不能说成推理准确率或GNN带来Agent增益。主评分30/30不变，20图分数更新/14邻居重排，10未覆盖诚实保留。新训练/付费0，Stage09累计66/144。

最终根回归 586 passed, 18 skipped, 1 warning in 48.01s（18缺隔离DSN）；真实API审批边界、记录真实投影UI检查和30笔工具回滚通过。L5与人类语义复核待本人完成；路线5A已按用户要求插入并完成工程验收，见下方新证据；Stage09仍In Progress。

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline python scripts/check_stage09_replay.py --output-dir reports/generated/stage09/route5/20261007-replay-v1
uv run --offline python scripts/run_stage06_local.py --config configs/stage09.yaml
~~~

审计不新增模型调用；启动演示后创建调查会真实调用本地模型。旧配置回滚使用同启动脚本的 --config configs/stage05.yaml，不覆盖权重。

### Stage09 路线5A：真实本地Agent质量改进

[验收与首轮失败](reports/stage-09-route-5a-agent-quality.md)、[学习路线](docs/plans/stage-09-route-5a-learning.md)和[核对讲义](docs/data/stage-09-agent-quality.md)已就绪。新增事实资料和受限判断合同，以程序核验来源/未知项；首轮15次真实开发失败保留，4B仅2/5，一次追加开发5/5后冻结。原30笔来源绑定从28/30达到30/30，新10笔10/10；原始受限判断分别30/30、10/10，3次重复报告一致。45不同交易、63本地生成，Stage09累计129/144，新训练/付费0。图缺口保留，XGBoost仍主评分，GraphSAGE仍实验信号。

根610 passed/18缺隔离DSN skipped/1 warning；真实API边界和记录真实投影的工作台检查通过。本轮验收本地单worker，不宣称数据库/浏览器视觉复测、自由文本推理或欺诈准确率通过。至少10案人工实用性复核及L5A待本人完成，路线6未开始。

~~~powershell
. .\scripts\enter_project_env.ps1
# 复查现有冻结结果，不新增生成。
uv run --offline python scripts/evaluate_stage09_quality.py check
# 显式新版演示；创建调查会真实调用本地模型，建议预留约6GiB可用内存。
uv run --offline python scripts/run_stage06_local.py --config configs/stage09_quality_4b.yaml
~~~

选用已安装并固定digest的qwen3:4b，think=false/keep_alive=0；原30笔耗时中位数7.38秒，新10笔11.06秒，包含加载，与旧0.5B常驻延迟条件不同。旧配置configs/stage09.yaml保留可回退，默认旧协议兼容。
