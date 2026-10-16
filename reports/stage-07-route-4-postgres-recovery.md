# Stage 07 路线 4：PostgreSQL 运行记录、检查点与审批恢复验收

> 2026-09-28 工程门禁通过；个人 L4 自测待学习者亲自完成。Stage 07 整体仍为 In Progress，pgvector 政策检索和容器重建验收分别属于路线 5、7。

## 实现结果

[PostgreSQL 运行实现](../src/graphrisk/serving/postgres_service.py)通过显式 `GRAPHRISK_STAGE07_STORAGE=postgres` 启用；默认 Stage 06 内存模式保留。独立的[存储/迁移层](../src/graphrisk/serving/postgres_store.py)与固定 `langgraph-checkpoint-postgres==3.1.2`、`psycopg[binary]==3.3.6` 已写入 `pyproject.toml`/`uv.lock`。独立[迁移脚本](../scripts/migrate_stage07.py)从空库与重复执行均通过，应用启动不自动迁移。可选[受限角色授权](../scripts/grant_stage07_app.py)和[到期清理](../scripts/cleanup_stage07.py)已提供；[操作说明](../docs/data/stage-07-postgres-service.md)列出实际顺序和环境变量。

运行先保存 `preparing` 和派生报告/审计，再由 PostgresSaver 写入并核验 `await_approval` 暂停点，最后发布可见 `pending`。持久 GET 按完整 `run_id` 查库，核对报告哈希、保存时的模型/政策版本、审批载荷及检查点，不因新进程的 instance ID 不同失效。决定先写唯一意图，再推进检查点与终态审计；两个崩溃窗口可由**同一决定**继续，冲突请求不能覆盖赢家。跨连接行锁限制并发决定；单 worker 仍是当前运行边界。

`Idempotency-Key` 在持久模式下提供创建重放：首次 201、同键同请求已发布重放 200，换请求为 409；准备中或结果未知为 503。运行器的本地费用接口换成 PostgreSQL 预留/结算表，唯一尝试键避免本地重复记账；外部提供商响应不明时预留维持 `uncertain`，不承诺第三方账单 exactly-once。数据库故障返回 `persistence_unavailable`，不退回内存。内存模式收到幂等头返回 422。原 `run_id`、报告/审批 HTTP 结构、默认 24 小时运行和 30 分钟审批窗口、未认证 `reviewer_id` 与外部动作 0 保留。

## 真库与回归证据

| 检查 | 实际结果 |
|---|---|
| 隔离 PostgreSQL 容器 | 本机 Docker Engine 29.8.0；复用路线 0 固定镜像 `pgvector/pgvector:0.8.6-pg17-bookworm`，仅绑定 `127.0.0.1:15432`。本轮测试容器和密码文件仅本地使用；未写入 Git。 |
| 迁移与权限 | 空库 v1 及再次迁移通过；未知新 schema 使服务不就绪。专用应用角色非 superuser，不能 UPDATE 迁移表，能 INSERT 运行表；以该角色完成真实探针。 |
| 真库集成 | 12 项路线 4 测试覆盖跨实例 pending→拒绝、相同重试、冲突/双连接竞争、过期、读断连、发布前中断、创建幂等、报告/检查点错绑、两个决定崩溃窗口、保存版本读取、费用保守预留、TTL/清理。另有旧 Stage 06/API 及冻结评估测试。 |
| 真实 held-out 演练 | 用受限角色在 IEEE-CIS `3488959`、固定本地 Ollama `qwen2.5:0.5b` 上创建 `verified/pending`，首个 API 进程退出后另一操作系统进程按原 ID 读取，创建键重放 200，人工拒绝后相同决定重试 200、冲突 409。证据 `reports/generated/stage07/route4-real-postgres-20260928T044657Z.json`：创建进程 PID 19336、恢复进程 PID 12336；运行 `run-0bc81ea9b0a0-75bd1d894b7f497e9cd445edc6842178`、报告哈希 `d19037a6ecd8cae203c06b09c638c637c465999a91f28f581b049507dae43ee5`、一条 `settled` 费用尝试、两条审批事件、外部动作 0。 |
| 冻结 v2 | `--probe-stage07-postgres-restart --probe-stage07-trace` 的报告 `reports/generated/stage07/agent-eval-v2-initial-20260928T043907Z.json`：15 案中 **2 已观察、2 通过、0 失败、13 `not_run`**。路线 4 新探针让原重启失败案通过；未运行案例不算成功。 |
| 全量/构建 | `uv run --offline pytest -q` **329 passed，1 条第三方 Starlette/anyio 弃用警告**；`uv sync --locked --offline --group demo`、`uv lock --check --offline`、`uv build --offline` 通过。生成包检查未见虚拟环境、uv 缓存、原始数据或生成报告。 |
| 数据边界 | 两个本地 DSN 文件被 `.gitignore` 排除；真库 `record_json` 的受限字段扫描匹配数 0。仅持久派生报告/必要审计，不存原始 `graph_run` 或训练标签。 |

曾在重复使用的测试库触发默认 `max_runs=32`，API 正确返回 429。真实探针为隔离测试库显式提高容量后通过；产品默认容量未改。这说明测试库的运行/清理周期会影响后续演练，不能把 429 当数据库恢复失败。

## 边界与学习任务 L4

路线 4 的真实 held-out 演练已经在两个独立操作系统进程之间恢复同一 PostgreSQL 检查点；路线 7 才验收容器重建、Compose 拓扑、数据库断连/恢复和镜像边界。当前测试以单 API worker 为前提，两个独立数据库连接只验证决定互斥，不宣称多 worker 完整就绪。默认 TF-IDF 政策检索保留，pgvector Retriever 尚未接入；Stage 07 仍不可标 Done。本轮仅回环开放的临时数据库容器保留供 L4 练习；其 `--rm` 生命周期及释放命令见操作说明。

学习者按[学习路线 L4](../docs/plans/stage-07-learning-roadmap.md)在测试库亲自检查同一 `run_id` 的运行、checkpoint、审批与费用行，关闭 API 再读取并拒绝；随后改备注/哈希、断开 DB、恢复后重读。能解释“数据库行、检查点和费用分别如何对账”以及为何数据库卷丢失不能视为普通进程重启，再勾选个人 L4；工程测试不代签。

