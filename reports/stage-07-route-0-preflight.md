# Stage 07 路线 0：前置门禁与选型实测记录

> 日期：2026-09-27（Asia/Hong_Kong）  
> 工程门禁：**通过**；仅代表前置环境、Stage 06 基线及最小真库兼容探针。Stage 07 路线 1～8 和个人学习 L0 尚未验收。  
> 原始运行输出：`reports/generated/stage06/agent-eval-v1-20260927T154038246750Z.json`、`reports/generated/stage07/`（均被 `.gitignore` 排除）。

## 前置矩阵

| 项目 | 复现命令 / 探针 | 实际结果 |
|---|---|---|
| Python 与锁文件 | `uv --version`; `uv run --offline python -c "import sys; print(sys.version)"`; `uv lock --check --offline`; `uv sync --locked --offline --group demo` | Python **3.11.16**，uv **0.12.18**；锁文件校验通过，147 个包解析、143 个包检查通过。 |
| 全量回归 | `uv run --offline pytest -q` | **305 passed，1 warning，103.61s**。唯一警告为 Starlette 对已弃用 `anyio.abc.BlockingPortal` 别名的使用；本次无测试失败。 |
| Docker 与磁盘 | `docker version`; `docker compose version`; `Get-PSDrive C,D,E` | Docker Desktop 4.92.0，Client/Server **29.8.0**，Compose **v5.5.1**；检查结束时 C/D/E 分别空余 **13.02/29.55/56.35 GiB**。之前的 WSL `0x800705aa` 曾阻止启动；本次 Linux Engine 已可用，不能据此推断故障根因已永久消失。 |
| Stage 06 本地服务 | `uv run --offline python scripts/run_stage06_local.py`; GET `/api/v1/ready`、`/_stcore/health` | 启动器打印 `Stage 06 ready`；API 状态 `ready`，`data/risk_model/graph/policy_index/ollama` 五项均为 `true`，工作台 HTTP 200、正文 `ok`。探针后关闭本轮启动的子进程，8000/8501 均已释放。自动化清理使启动器以“服务已退出”代码 1 结束，不影响已记录的就绪结果。 |
| Stage 06 Compose | `docker compose up -d --no-build`; `docker compose ps --format json`; 同上两项 HTTP 探针；`docker compose down` | 复用镜像 `graphrisk-stage06:local`，ID `sha256:db57b1cdbc7786fe916c3ae70ce88b425adce934e70462d43480b918edeff312`；API、workbench 均 `healthy`，同样五项就绪、UI 200。随后关闭服务并删除本轮 Compose 网络。 |
| Ollama 固定模型 | GET `http://127.0.0.1:11434/api/tags`; POST `/api/embed` 短文本 | 生成模型 `qwen2.5:0.5b` digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`；向量模型 `qwen3-embedding:0.6b` digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`。实测输出 **1024 维**，与现有 dense 索引一致。 |

## 冻结 Stage 06 基线

执行 `uv run --offline python scripts/evaluate_stage06_agent.py --real`，本轮报告为 `reports/generated/stage06/agent-eval-v1-20260927T154038246750Z.json`。旧 17 案 SHA-256：`d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1`；新 33 案 SHA-256：`f91200b170e0665628312bc24ecfd01b928d6a74d861ce8e9bb9eac31caaf684`。**fake 50/50**，无依据结论 0、审批绕过 0，P50/P95 为 41.49/567.08 ms；这些时间只反映本机本轮执行。

真实本地样本 **3 条**，均来自固定交易而非独立样本：`real_current_policy` 为 `verified`、`real_pre_policy` 为 `insufficient_evidence`、`real_relation_limit_one` 为 `verified`；引用检查均通过，无依据结论及审批绕过均为 0。真实样本 P50/P95 为 2937/10319.7 ms。模型 digest 与前述 Ollama 固定值一致。这组小样本只用于回归，不可当作准确率估计。

原创模拟政策语料 SHA-256：`5944138255497bb595d46513a7100045363d1f35d99fd2607954567e64145513`。默认 TF-IDF 索引版本：`9eade9b4c7f38df917267731ebe1d19a0aa8c270cbe1b8234a64d64b1605b3e7`；候选本地 dense 索引版本：`e5daa423b9370888f82f92ee9250e73fecc0a401500464b63d991d66373530bd`，16 段、1024 维、模型 digest 如上。

GraphSAGE 比较运行 `artifacts/stage-04/ieee-cis/comparisons/stage05-route8/metadata.json` 的 SHA-256 为 `7aa37572e2e56a260660fdbe4509362ecf3e465a11422c8620b8273967dae799`；图清单 SHA-256 为 `8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80`，模型 checkpoint SHA-256 为 `b1e4696dfca80c617ddeadf2fb2ae310b39a6e445fe9127865ea56901d26e69f`。相同时间测试集 PR-AUC：GraphSAGE **0.0860**、受控 XGBoost **0.1296**、简单融合 **0.1181**；融合并未在该测试集超过 XGBoost。`uv run --offline python scripts/query_graph_neighbors.py 3488959 --comparison-run stage05-route8 --limit 3` 对固定 held-out 交易返回 `status=ok`、`score_scope=held_out`、**邻居 0 个**；不得把图分数描述成找到了欺诈团伙。

## 当前状态归属与失败窗口

`Stage06Service` 的 `OrderedDict` 运行记录、`CostLedger` 与 `InMemorySaver` 都属于单进程内存；`Stage05ApprovalWorkflow` 的暂停检查点也写入该 saver。`PolicyRetriever` 从本地版本化索引文件读政策，尚无数据库。`run_id` 含实例 ID，新进程会拒绝旧实例的运行 ID。这是诚实的 Stage 06 边界，并非可恢复持久化。

| 崩溃点 | 可能留下的状态 | 路线 3 必须定义的合同 |
|---|---|---|
| 生成已计费、运行记录未写入 | 费用与调查结果可能不一致 | 费用预留/结算与幂等对账 |
| 检查点已写、API 记录未发布 | 可能有孤立检查点 | `pending` 发布顺序、补偿与清理 |
| 决定已写、HTTP 响应丢失 | 客户端可能重试 | 同一决定幂等，冲突决定拒绝 |

旧 50/50 只证明冻结路径在现有条件下满足评分器，不能证明进程崩溃、真库断连或并发重试的长尾路径。个人 L0 练习仍由学习者亲自画状态图并解释三种不一致；工程探针不代签学习完成。

## 最小依赖与容器选型

选择 **PostgreSQL 17 + pgvector 0.8.6** 的官方 `pgvector/pgvector:0.8.6-pg17-bookworm`，本机拉取后 digest 固定为 `sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f`。临时、无网络容器实测 PostgreSQL **17.11**、`CREATE EXTENSION vector` 得到 **0.8.6**、`'[1,0]'::vector(2) <=> '[0,1]'::vector(2)` 得到 **1**。容器已删除。16 段政策先用精确余弦查询，不加 ANN 索引。[pgvector 官方镜像/SQL 文档](https://github.com/pgvector/pgvector/blob/master/README.md)。

候选 Python 依赖为 `langgraph-checkpoint-postgres==3.1.2`、`psycopg[binary]==3.3.6`、`pgvector==0.5.0`；`psycopg-pool==3.3.3` 由前者解析得到。`uv pip compile` 在 Python 3.11、既有 LangGraph **1.2.12** / checkpoint **4.2.0** 约束下成功解析，离线 `uv run --with ...` 导入通过。随后短暂开启仅绑定 `127.0.0.1:15432` 的临时数据库：`PostgresSaver.setup()`、写入/读回检查点成功，psycopg + pgvector 参数适配的余弦查询得到 1；容器已删除。检查点适配官方要求首次使用 `.setup()`，手动连接需 `autocommit=True` 和 `dict_row`。[LangGraph 适配说明](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/README.md) · [依赖范围](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/pyproject.toml)。

**决定：** 后续路线 4 用现有 LangGraph + `PostgresSaver` 保存真正的暂停检查点，另用直接 `psycopg`/PostgreSQL 表处理运行记录、审批和费用，不新引入 ORM。路线 5 用同一 PostgreSQL 的 `vector(1024)` 与 `pgvector` Python 适配，先按半开政策日期筛选，再做精确余弦排序。数据库模式必须显式开启；故障时 fail-closed，不暗中回退内存。旧 TF-IDF 默认演示保留，向量质量对照通过后才考虑切换。

本轮**未因候选依赖修改** `pyproject.toml` 或 `uv.lock`；解析与真库往返在被忽略的隔离探针中完成。路线 4/5 实装前必须将所需依赖加入项目、更新并检查 `uv.lock`，在 Windows/Linux 目标环境重跑测试。此时没有运行记录/审批/费用持久化，也没有 pgvector 政策 Retriever 或跨进程恢复；临时数据库探针不能替代后续门禁。

## 下一步与复现

路线 1 先冻结不少于 12 个不同风险点的新增挑战案例；路线 3 冻结事务、TTL、迁移和恢复合同后，路线 4/5 才修改真实服务。若 Docker/WSL 资源错误重现，保留本报告中的失败背景，重新执行 `docker version`、磁盘检查和真库探针；不得拿本次单次通过代替后续容器恢复测试。所有生成评估与探针文件均位于 `.gitignore` 排除的 `reports/generated/`，没有把原始 IEEE-CIS、模型权重或数据库凭据写进公开报告。
