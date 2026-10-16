# Stage 07 路线 4：PostgreSQL 运行、检查点与审批操作说明

> 2026-09-28。路线 4 使用显式 `GRAPHRISK_STAGE07_STORAGE=postgres`；Stage 06 默认仍为 `memory`。本文件说明本地演示与真库测试，数据与凭据只放忽略文件或进程环境。配套[路线 3 合同](stage-07-persistence-contract.md)、[故障矩阵](stage-07-persistence-fault-matrix.md)和[路线 4 实测报告](../../reports/stage-07-route-4-postgres-recovery.md)。

## 准备与启动

Python 3.11、项目 `uv.lock`、本地 IEEE-CIS/模型/政策工件和固定 Ollama 模型仍是前置条件。PostgreSQL 17/pgvector 容器需要独立的持久卷；路线 4 的测试容器是临时的，不能把它当生产数据卷。数据库账号和密码从本地忽略的 `.env` 或安全环境来源注入；**不要**把实际连接串写入 Git、命令日志或报告。下述命令假定当前 PowerShell 已设置环境变量：

- `GRAPHRISK_STAGE07_MIGRATION_DSN`：有建表权限的迁移账号；
- `GRAPHRISK_STAGE07_APP_DSN`：需要新建/授权的专用应用账号；
- `GRAPHRISK_STAGE07_DSN`：API/清理实际使用的受限应用账号连接串；
- `GRAPHRISK_STAGE06_CONFIG=configs/stage05.yaml` 与 `GRAPHRISK_STAGE07_STORAGE=postgres`。

迁移与授权的顺序（本地单账号演示可只设置 `GRAPHRISK_STAGE07_DSN`，迁移脚本会使用它；推荐分离账号）：

```powershell
uv sync --locked --offline --group demo
uv run --offline python scripts/migrate_stage07.py
uv run --offline python scripts/grant_stage07_app.py
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`grant_stage07_app.py` 需要迁移和应用 DSN；它给应用角色授予 public schema USAGE、业务/检查点表所需 DML，以及迁移表只读权限，不授予 schema CREATE 或 superuser。迁移脚本使用 `GRAPHRISK_STAGE07_MIGRATION_DSN`，未提供时回退到 `GRAPHRISK_STAGE07_DSN`。API 启动**不自动迁移**；缺 schema/检查点或 DB 断连时拒绝就绪，不切换到内存。可用 `GET /api/v1/ready` 查看 `postgres` 组件，`GET /api/v1/health` 只证明进程活着。

创建调查沿用原 JSON 请求。可选 `Idempotency-Key` 只在 PostgreSQL 模式可用：首次发布 201，完全相同键和请求的已发布运行重放 200、原 `run_id`/报告/审批窗口；同键不同请求 409。运行未发布或收费结果未知时返回 503 待对账，不自动重复调用模型。无键请求仍是每次创建新运行。内存模式收到此头返回 422。每个 HTTP 请求仍有独立 `X-Request-ID`；首次决定请求 ID 保留在审批审计，`reviewer_id` 未认证。

本轮供 L4 自学的隔离容器 `graphrisk-stage07-route4-test` 仍在运行，仅监听 `127.0.0.1:15432`。本机可在 PowerShell 中从忽略文件读取受限账号连接串，而不打印密码：

```powershell
$line = Get-Content -LiteralPath .env.stage07-app-localtest
$env:GRAPHRISK_STAGE07_DSN = $line.Substring($line.IndexOf('=') + 1)
uv run --offline python scripts/probe_stage07_postgres.py
```

此容器创建时使用 `--rm`；执行 `docker stop graphrisk-stage07-route4-test` 会删除本轮临时数据库，适合完成 L4 检查后释放资源。生成证据 JSON 仍保留在被忽略的 `reports/generated/stage07/`。

## 存储和安全边界

迁移版本 1 建立 `investigation_runs`、`create_idempotency`、`approval_events`、`cost_budget`、`cost_attempts`、`schema_migrations`，并调用固定版 PostgresSaver 建检查点表。运行 JSON 仅含经白名单保留的派生报告、必要报告审计及公开视图；原始 `graph_run`、IEEE-CIS 行/CSV、训练标签、模型权重、完整 Prompt 和密钥不写数据库。报告哈希沿用 `DerivedReport.model_dump_json()`；重启只加载已存版本/报告和真实 LangGraph 暂停点，不重新推理。内部 `preparing` 不可公开读取；检查点通过绑定核验后才发布 `pending`。独立决定意图和 PostgreSQL 行锁保障跨连接的唯一决定，崩溃后相同决定可继续，冲突不能覆盖赢家。所有审批外部动作数为 0。

默认运行 TTL 24 小时、审批窗口 30 分钟；重启不会延长。过期清理可定期手动运行：

```powershell
uv run --offline python scripts/cleanup_stage07.py
```

清理先将到期运行置为不可审批，再删检查点和派生报告；保留最小审计、幂等键摘要、费用与墓碑七天供对账，之后分批删除。可重复运行。请不要用 `docker compose down -v` 或删除数据库卷来模拟 API 重启；数据库卷丢失属于数据丢失。

## 复现真库测试

在**隔离测试数据库**设置 `GRAPHRISK_STAGE07_DSN` 后：

```powershell
uv run --offline python scripts/migrate_stage07.py
uv run --offline pytest -q tests/integration/test_stage07_postgres.py
uv run --offline python scripts/probe_stage07_postgres.py
uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage07-postgres-restart --probe-stage07-trace
uv run --offline pytest -q
```

路线 6 将 PostgreSQL 模式的待审资格收紧为仅 `verified`：图服务故障导致的 `partial_evidence` 仍保存/公开部分报告，但 `approval=null`、无审批检查点，也不能进入批准/拒绝接口。Stage 06 默认内存模式为保持冻结旧 50 案仍可能对部分报告给出待审；这是一项有意保留的双模式兼容差异。路线 8 已复核旧 50 案与 Stage 06 默认路径：迁移到显式 PostgreSQL 模式时应说明部分证据不能进入人工审批；若要统一两种模式的语义，需要另行设计版本化 API 合同，不在 Stage 07 暗改旧冻结案例。见[总验收](../../reports/stage-07-route-8-acceptance.md)。

集成测试覆盖重启待审、同决定重试、双连接冲突、到期、DB 读断连、发布前写中断、创建幂等、哈希/版本错绑、检查点缺失、两个决定崩溃窗口、费用预留/结算与不确定费用、迁移重入和 TTL 清理。真实探针使用固定 IEEE-CIS held-out `3488959` 和本地 Ollama，关闭首个 API 进程后由独立操作系统进程创建新实例，以同 `run_id` 读取、拒绝、重试并查询独立审计/费用行；输出只放 `reports/generated/stage07/`。测试库积累活跃运行后可能触发默认 32 条容量限制；探针为隔离测试显式使用更高容量，产品默认不变。独立 Compose 的容器重建、故障与卷恢复已在[路线 7](../../reports/stage-07-route-7-compose-faults.md)和[总验收](../../reports/stage-07-route-8-acceptance.md)验证；多 worker 整体安全性、正式身份认证及生产备份恢复未验收。

