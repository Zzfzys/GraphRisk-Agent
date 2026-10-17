# GraphRisk-Agent

GraphRisk-Agent is a portfolio-grade fraud investigation system centered on a reliable LLM Agent. The Agent combines transaction features, XGBoost, GraphSAGE, graph queries, and policy RAG to produce evidence-grounded investigation reports with human approval for high-risk recommendations.

The approved scope and delivery gates are defined in [PROJECT_PLAN.md](PROJECT_PLAN.md). Detailed stage status is maintained in [docs/plans/README.md](docs/plans/README.md).

On Windows, start each project PowerShell terminal by dot-sourcing scripts/enter_project_env.ps1. This keeps project temporary files and Python download caches on the project drive for that terminal and its child processes. It does not change Windows-wide settings or move installed applications. It also adds local project endpoints to the process NO_PROXY list while preserving existing entries. See the [storage audit and cleanup guide](reports/storage-audit-20261007.md).

## Current status

Latest engineering state (2026-10-17): Stages 01–08 and the original Stage 09 engineering routes are accepted. The [cross-stage audit](reports/stage-01-09-recheck-20261017.md) reports 877 main-project tests (including 26 real database tests), 91 independent GraphRAG core tests and 3 UI tests passed: 971 passed, one Windows symlink-permission skip, no failures/errors. The simulated 50-case suite and 15 limited-role probes are counted separately. [All six GitHub CI jobs](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39031393216) passed for baseline commit `e443ed5`.

Stage 10 routes 0–1 are engineering-complete: [implementation](docs/plans/stage-10-implementation-roadmap.md), [learning](docs/plans/stage-10-learning-roadmap.md), and [route 1 acceptance](reports/stage-10-route-1-measurement-contract.md). Ten first cases and two repeats are frozen before new outcomes; the stage-wide HTTP reservation ledger extends the immutable 130/144 baseline by at most 14 attempts. Forty-seven new offline tests are included in the current local CPU result of 898 passed/one platform skip. Twelve mock sends use a separate fixture ledger and a fake clock: they are neither real Agent quality nor performance evidence. Actual execution, performance, recording, clean-environment reproduction and personal review remain pending. Enhanced XGBoost remains the primary score; GraphSAGE was not promoted. See the [safe check/dry-run guide](docs/data/stage-10-measurement.md); this route has no real-run CLI.

Local Git closeout is complete on `codex/stage09-version-closeout`: integration commit `cc1da7d` captures 553 tracked files, and a documentation commit records clean-checkout validation. CPU (815 passed/1 platform skip), isolated database (24 tests and 15 probes), independent GraphRAG (91+3), and two wheel checks passed from that checkout. A verified local Git bundle preserves the final branch. The existing E-drive main worktree is preserved separately; the branch is now published to Zzfzys/GraphRisk-Agent and real GitHub Actions passed on 2026-10-17. Raw data, model weights, secrets, caches and generated run evidence remain outside Git. See the [Git closeout record](reports/stage-09-git-closeout.md).

### Earlier stage evidence

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

### Stage09 路线5B：增强基线、GraphSAGE扩量与公平对照

路线0～5工程完成，[实施路线](docs/plans/stage-09-route-5b-model-enhancement.md)、[学习路线](docs/plans/stage-09-route-5b-learning.md)、[七模型基线](reports/stage-09-route-5b-2-baselines.md)、[九次GraphSAGE](reports/stage-09-route-5b-3-graph-expansion.md)及[五组消融/增强验收与只读复现](reports/stage-09-route-5b-4-conditional-graph.md)已更新；最新[三折确认](reports/stage-09-route-5b-5-confirmation.md)区分基础质量通过与图增量失败。新实验在E盘执行，使用第一折250,979 train/25,098 inner；大数据、权重和缓存留E盘，C盘编辑工作树通过源码快照绑定。

| 输入/模型 | 同集合内层AP | ROC-AUC |
|---|---:|---:|
| core XGBoost（63列） | 0.499383 | 0.866039 |
| GraphSAGE根输入XGBoost（28维） | 0.164530 | 0.780715 |
| 同根输入MLP（28维） | 0.150673 | 0.754283 |
| 当前GraphSAGE候选g3（28维+关系） | 0.179596 | 0.768844 |
| 新GraphSAGE候选c4（core增强191维+关系） | 0.391845 | 0.825771 |
| c5匹配无边神经网络（191维） | 0.374341 | 0.829627 |
| 选择的宽表x3（871列） | 0.726914 | 0.934365 |

宽表x1/x2/x3按预定内层AP选型，完整七模型表与Top-K、训练曲线、供应方匿名字段时点限制见报告。x3的AP仅比x2高0.000147；正式三折基础门禁、图增量与后续服务切换仍待相应路线。七份完整内层预测重载/独立指标复算差异0、60真实根编码抽查差异0；90 passed/1旧Windows符号链接skipped。

路线2时两次运行失败与同参数恢复均保留：XGB8尝试（6完成/2失败），MLP1完成；XGB余3confirmation+1failure。主机睡眠触发deadline后，恢复入口使用进程临时防自动睡眠请求，退出时释放。

路线3三个规模AP0.111899/0.155513/0.176763，同时改变样本量和时间覆盖；六参数全部完成，按AP选择g3，仍明显落后宽表x3。九份完整预测重载与独立指标复算差异0、276,077根向量差异0，92 passed/1旧符号链接skipped，新进程只读核验通过。两个RAM失败与同参数恢复完整保留；GNN11/30尝试（9完成/2失败）、6,400.69秒，共享20尝试（16完成/4失败）。保护九次最终确认后开发训练余约73分钟；下一5B-4条件增强先做少量消融，融合/三折质量/服务覆盖待验收。新LLM/付费0、旧生成130/144、默认服务未切换；LB0～LB3本人待完成。


## 2026-10-10：路线5B-4工程完成

[五组消融/条件增强报告、曲线与只读复现](reports/stage-09-route-5b-4-conditional-graph.md)已就绪：同250,979 train/25,098 inner，c4增强191维AP0.391845/ROC0.825771，匹配无边c5 AP0.374341/ROC0.829627；AP差+0.017504为单折探索，不能替代强XGB x3 AP0.726914的融合晋级。五真实组、六尝试；全276,077图重连审计通过，118相关测试通过/1旧符号链接skip，五权重完整新进程重载最大分数误差8.35e-7、指标复算差异0。c5原600秒失败不退款，同参数恢复前17轮五指标等价；实际源v2/v4归档、522源文件核验通过。

共享26尝试（21完成/5失败），GNN17/30、累计10,618.24秒、failure3/3用尽；九次确认9小时仍保护，开发训练余约3分钟。按预定AP选c4候选，融合网格已登记但未拟合；Hetero/R-GCN因预算与单折证据不足Deferred。默认评分服务未切换，外层/test/新LLM/付费0，旧生成130/144不变。下一5B-5融合/三折确认，再5B-6覆盖；LB4本人学习仍Pending，Stage09整体In Progress。

## 2026-10-10：路线5B-5真实三折确认完成

[路线5验收报告](reports/stage-09-route-5b-5-confirmation.md)和[150分钟学习合同](docs/data/stage-09-confirmation.md)已就绪。12次真实确认（9图+3树）全部完成、新失败0；三折150,588外层平均XGB ROC-AUC0.905089/AP0.544367，最低0.80及均值0.90探索达到；三个alpha=1、融合ΔAP0、正收益0/3、两项95%区间[0,0]，GraphSAGE不晋级并退出主评分资格，保留关系与实验记录。88,581历史test描述性复核ROC-AUC0.903670/AP0.537789，测试前后决定完全相等；16次完整权重重放分数最大差8.64e-7≤1e-6，封存预测指标独立复算≤1e-12；25个真实日抽样/300项加权指标比较最大差2.22e-16。原生--check通过，共享38尝试（33完成/5既有失败）、RAM最低3.447GiB，新LLM/付费0、旧生成130/144不变。默认服务未切换，5B路线6覆盖/接入及7验收待执行，原路线6～8未开始；LB0～LB7本人学习和人工复核独立待完成，Stage09整体In Progress。

日常复现使用报告中的E盘环境初始化及 `run_stage09_confirmation.py --check <已封存目录>`，不会训练或生成。完整12fit、九种子外层、校准网格、历史复核、三张图和辅助工具错误均保留；重新CUDA推理允许预定分数容差，不宣称图权重重载逐位相同。

## 2026-10-15：路线5B-6工程验收完成

[完整验收、低内存结果与操作步骤](reports/stage-09-route-5b-6-coverage.md)：177,162笔时点图/分数全覆盖，5案真权重和重复3次通过；真实HTTP、工作台、33份旧记录与31份报告/审批hash通过，173项相关回归全通过。新配置为configs/stage09_coverage.yaml，启动脚本scripts/run_stage09_coverage_service.ps1，默认只读核验scripts/check_stage09_coverage.py。主进程峰值889MiB、独立树540MiB；不是整机同时峰值。修复日历语义和旧API误导入，保留失败；无新训练/真实LLM/付费调用。GraphSAGE仍不晋级，Stage09/5B路线7及本人学习待完成。

## 2026-10-15：路线5B-7工程总验收完成

[专项总验收与只读复现](reports/stage-09-route-5b-7-acceptance.md)：0～6原生/归档复核、完整806项通过/1旧符号链接skip、新增核验器11项通过、24真库全部执行；当前源码容器的调查/审批/重启/断库恢复及真实HTTP工作台通过。Linux真权重新事件树差0、图均值差1.09e-8；本轮新增真实训练/标签批次/LLM/费用0。默认只读入口scripts/check_stage09_5b.py，无需保持Docker运行。另见[模型卡](docs/data/stage-09-5b-model-card.md)、[数据卡](docs/data/stage-09-5b-data-card.md)和[90分钟个人复盘](docs/data/stage-09-5b-review-template.md)。5B工程完成、GNN仍不晋级、本人学习Pending；下一原Stage09路线6分层回归与CI，Stage09整体尚未完成。
