# Stage 06 路线 7：端到端验收与复盘

> 2026-09-27：工程门禁通过，Stage 06 可标记 `Done`；个人 L0～L7 自测仍由学习者独立完成。

## 冻结输入与真实链路

采用[演示契约](../docs/data/stage-06-demo-contract.md)的 IEEE-CIS held-out 测试交易 `3488959`（train/validation/test 为 0/0/1）、政策日期 `2026-07-01`、关系上限 3、本地 `qwen2.5:0.5b` 固定 digest。仓库内 `uv run --offline python scripts/run_stage06_local.py` 一键启动单 worker API 和 Streamlit；`/api/v1/ready` 的数据、风险模型、图、政策索引、Ollama 五项均为 `true`，工作台 `/_stcore/health`、`/docs`、`/openapi.json` 均返回 200，OpenAPI 包含调查与审批路径。

使用 Streamlit `AppTest` 操作真实本地 API（不替换 API client），从表单发起交易后得到 `verified`。报告同时包含 XGBoost 分数约 0.1967、GraphSAGE 目标分数约 0.2969 与图查询状态、模拟政策的 1 条 `source_quote_verified` 引文和 5 条工具审计；分数均未校准。引文文本在其 `data/policies/stage05/graph-signal-v1.md` 来源文件中确实存在。该固定交易的严格历史图邻居为 **0**；这表示本样例不能展示一条实际邻边，不能把空邻居解读成安全。建议为 `human_review`，审批从 `pending` 经工作台填写复核员和备注后变为 `rejected`；冻结报告哈希不变、审计为 `created → rejected`、外部处置 0。再次运行工作台只重读同一 `run_id`。本地本次运行 ID 为 `run-7d2476014720-85d0127d7fee4b49ace9a4839f80f493`；脱敏摘要保存于忽略目录 `reports/generated/stage06/route7-local-e2e.json`，该文件随后被容器复验摘要覆盖，故以本报告记录两个运行 ID。

政策日期改为 `2025-12-31` 时，同一真实交易返回 `insufficient_evidence`，政策引用空、`generation_attempts=0`、没有审批；工作台显示无可引用政策警告且没有批准按钮。模型不可用分支用 `uv run --offline python scripts/validate_stage05_end_to_end.py` 的受控断连 provider 复验：同一真实交易与本地工件走完整前段后得到 `generation_error`，不产生可审批报告。此故障注入不是一次真实 Ollama 服务停机，避免影响同机服务；API 的 503 `generation_unavailable` 映射另有集成测试与 50 案评估覆盖。

## Compose、回归和交付检查

| 门禁 | 本次结果 |
|---|---|
| Compose | `docker compose config --quiet`、`up -d --no-build`、API/工作台 healthy；对容器 API 重跑上述真实 UI/API 检查得到 `verified → rejected`、无政策安全分支；`down` 成功。容器运行 ID `run-6a03b38f2826-8f9628af20bb4774bedfb7069f02d3ad`。 |
| 就绪与文件边界 | 容器 API 五项就绪均为 `true`；数据、模型、政策五个挂载只读，只有 `reports/generated` 可写；工作台挂载数 0。镜像 ID `sha256:db57b1cdbc7786fe916c3ae70ce88b425adce934e70462d43480b918edeff312` 与[路线 5 已扫描镜像](stage-06-route-5-local-compose.md)相同，镜像内容未变。 |
| 评估 | `uv run --offline python scripts/evaluate_stage06_agent.py` 再次为 fake **50/50**；路线 6 的三个固定真实本地样本与分组指标见[评估报告](stage-06-route-6-agent-evaluation.md)。此处没有把 fake 通过率当作真实欺诈准确率。 |
| 自动测试 | `uv run --offline pytest -q` 为 **305 passed，1 warning，48.34s**；warning 是 Starlette 使用 anyio 已弃用别名。 |
| 锁与包 | `uv sync --locked --offline --group demo`、`uv lock --check --offline` 均通过；`uv build --offline --out-dir reports/generated/stage06/dist` 成功生成 wheel 与 sdist。uv 提醒仓库内 `.uv-cache` 可能进入 sdist；实际检查 wheel 65 个、sdist 245 个条目，未含 `.uv-cache`、`.venv`、原始/中间/处理数据、模型工件或生成报告。受限路径扫描只遇到公开 `.env.example`、空目录 `.gitkeep` 和名称为 `models` 的代码模块。 |
| Git 隐私 | `git ls-files` 与 `git ls-files --others --exclude-standard` 检查：无原始 IEEE-CIS 文件、Parquet、模型权重、`.env`、密钥或运行报告进入跟踪候选。已跟踪的两个 `reports/*baseline-results.csv` 仅是汇总指标表，不含逐笔交易。忽略目录包括 `data/raw`、`data/interim`、`data/processed`、`artifacts`、`reports/generated` 和本地环境。 |

以上检查使用既有构建镜像，**本次未重新 Docker build**；路线 5 记录了该镜像原先的构建、内容扫描和完整容器验收。新机器需先准备有使用权限的 IEEE-CIS 文件、生成所需工件并取得相同 Ollama 模型，再按[中英文演示说明](../docs/data/stage-06-demo-guide.md)重现；不能只靠公开仓库下载受限数据。

## 验收结论与边界

[Stage 06 总纲](../docs/plans/stage-06-mvp.md)中的交易输入、三类证据、人工审批、50 案、README 启动步骤、真实 Compose 和 Git 边界均有本次或路线 4～6 的运行证据，工程状态改为 `Done`。模型分数未校准；政策与案例为模拟材料；本地审批员字段未认证；报告、费用账本及检查点只在单进程内存保存，服务重启后旧审批不可恢复；系统不自动拒付、封禁或通知。50 案是固定功能/可靠性评估，三个真实模型样本都来自同一交易，不能代表生产准确率或泛化能力。

复盘：从交易、模型、图、RAG 到 Agent/审批的链路已经可展示，最有价值的可解释边界是“引用来源核验不等于政策适用”和“0 个图邻居不等于低风险”。Stage 07 优先处理真实失败分类、跨进程持久检查点和认证边界；PostgreSQL/pgvector 的接入需保留现有工具契约，Neo4j 仍是可选。个人 90 秒口述与独立重跑按[演示指南](../docs/data/stage-06-demo-guide.md)完成，不由工程测试代签。
