# Stage 07 路线 5：pgvector 政策检索验收

> 2026-09-28 工程门禁通过；个人 L5 练习待学习者完成。Stage 07 整体仍为 In Progress，路线 6～8 未验收。

## 实现与选择

新增[pgvector Retriever](../src/graphrisk/agent/pgvector_retrieval.py)、[迁移](../scripts/migrate_policy_pgvector.py)、[导入](../scripts/import_policy_pgvector.py)和显式[候选配置](../configs/stage07_pgvector.yaml)。使用现有 `psycopg[binary]==3.3.6` 传递参数化 pgvector 字面量，无需另加 Python 包；数据库仍是路线 0 固定的 PostgreSQL 17/pgvector 0.8.6。Stage 05 本地 dense 的 16 段、`index_version`、模型 `qwen3-embedding:0.6b` digest `ac6da0df…621d`、维度 1024 均保持。原 `configs/stage05.yaml` TF-IDF 默认不变。

数据库表以 `(index_version, chunk_id)` 固定向量及出处。重复导入 16 段幂等；模型/维度、来源 JSON/哈希或向量漂移会使导入/查询失败。应用角色仅有政策向量表 SELECT。每次查询在同一只读快照中核对 16 行，再由 SQL **先过滤半开日期区间，后按精确余弦距离与 `chunk_id` 排序**；沿用本地 dense 的阈值、文本重合和 `no_active_policy`/`no_match` 合同。数据库不可用返回结构化 `index_unavailable`，不回退本地结果或编造引用。16 段规模没有建立 ANN 索引；逐请求全表完整性核验的扩展性边界见[操作/数据合同](../docs/data/stage-07-pgvector-retrieval.md)。

## 真库与质量证据

| 检查 | 实际结果 |
|---|---|
| 真库/权限 | 复用仅绑定 `127.0.0.1:15432` 的隔离 `pgvector/pgvector:0.8.6-pg17-bookworm` 容器；迁移和导入执行成功。受限应用账号可读政策表且不能 INSERT；用该账号的真实模型查询返回 3 条 `pgvector` 命中。 |
| 故障合同 | 5 项真库集成测试：可重复导入、V1/V2 切换日与稳定同分排序、无答案/输入错误、错模型 digest/维度、数据库断连、元数据/向量篡改与缺段均未输出错误引用。 |
| 开发集 15 案 | 本地 dense / pgvector 均 Recall@3 **1.0**、无答案误报 **0/4**、旧版误命中 **0**、错误 **0**；P50 **117.928 / 209.587 ms**，P95 **242.674 / 282.464 ms**。 |
| 冻结集 15 案 | 两后端均 Recall@3 **1.0**、无答案误报 **0/4**、旧版误命中 **0**、错误 **0**；P50 **116.136 / 207.662 ms**，P95 **295.748 / 293.073 ms**。 |
| 同输入对照 | 固定 suite SHA-256 `ae0e508957ab4c3bed6cfa20069ecb56d7ce8180f8e1735f2efa8354c782ae83`；30 案状态差异 **0**、完整候选 ID/顺序差异 **0**，共 68 条返回命中的引用完整率 **68/68 = 100%**（逐请求固定索引与数据库行核对；无答案案例不进入该分母）。原始逐例输出位于忽略的 `reports/generated/stage05/` 与 `reports/generated/stage07/`。 |
| 真实调查 | 受限账号 + 本地 Ollama 的 IEEE-CIS held-out `3488959` 调查：`verified/pending`，政策检索 `ok/pgvector`、3 条命中、固定索引版本 `e5daa423b9370888f82f92ee9250e73fecc0a401500464b63d991d66373530bd`；运行 ID `run-eb58325bec52-c8f117f9de944a8e8ab4246f70f41f97`。服务就绪项 data/risk_model/graph/policy_index/ollama/postgres 全为 true。 |
| 回归 | 隔离管理员 DSN 下 `uv run --offline pytest -q` **334 passed，1 条第三方 Starlette/anyio 弃用警告**。初次误用受限应用 DSN 跑旧路线 4 迁移 fixture，得到 322 passed/12 setup errors；按测试权限合同改用迁移账号后全量通过。 |

`uv sync --locked --offline --group demo`、`uv lock --check --offline`、`uv build --offline` 均通过；轮子和源码包扫描共 354 项，排除允许的 `data/raw/.gitkeep` 后受限文件 0。`git diff --check` 无空白错误。

一次聚焦测试的失败诊断曾显示隔离测试库的 DSN；已把夹具改为不显示连接信息，轮换该临时库密码，并用新凭据重跑 5/5 真库测试。连接文件仍在 Git 忽略范围内。

同集结果没有给出将向量后端升级为默认的收益，且 pgvector P50 在本地环境约多 90 ms；保留显式可切换候选与原 TF-IDF 默认。此评估只涉及 16 段原创模拟政策和 30 个固定问题，不代表真实企业政策覆盖或大规模性能。路线 7 将另验收 pgvector 的 Compose 拓扑和容器重建；路线 6 才完成新的 Agent v2 全集评估。

## L5 复盘入口

学习者可按[学习路线 L5](../docs/plans/stage-07-learning-roadmap.md)在 `2026-06-30` 与 `2026-07-01` 查询“共享设备 人工复核”，写出 V1/V2 的半开日期边界；再运行无答案与错维度例子，并从 SQL 解释“先过滤后 Top-K”。最后对照两份逐例 JSON，解释余弦分数和风险概率/政策适用概率的区别。工程验收不能代签个人学习。
