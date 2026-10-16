# Stage 07 路线 3：持久化、恢复与兼容合同 v1

> 2026-09-28 冻结的实现合同。路线 3 定义语义与验收输入；PostgreSQL 业务表、迁移、持久检查点和 HTTP 幂等头须在路线 4 实现并用真库验证。Stage 07 仍为 In Progress。

## 模式、标识与兼容

启动时显式选择 `memory` 或 `postgres`，进程内不可自动切换。默认维持 Stage 06 的 `memory`：`InMemorySaver`、进程内运行/费用，重启后旧 `run_id` 返回 `410 run_instance_mismatch`。`postgres` 模式的连接、schema 或检查点不可用时，启动不就绪或请求返回 `503 persistence_unavailable`；不得切回内存并报告可恢复。`/health` 只说明进程存活；持久模式的 `/ready` 必须检查数据库和检查点。

保留 `run-{instance_id}-{uuid}`、`session-{run_id}` 形状。`instance_id` 只是创建者信息；持久模式只按完整 `run_id` 查库，不比较当前实例 ID。每个 HTTP 调用生成新 `request_id`；创建请求 ID 和首次决定请求 ID 随必要审计持久化，重读/重试不能覆写原事件。`reviewer_id` 仍未认证，外部动作始终为 0。

现有请求体、报告/审批字段及首次创建 201、GET 200 的语义不变。持久模式给 `POST /api/v1/investigations` 增加可选 `Idempotency-Key` 请求头；不传时每次 POST 仍创建新运行。键允许 8～128 个 ASCII 字母、数字、点、下划线、短横线；数据库和日志只保存/输出 SHA-256 摘要，不记录原头值。请求指纹由严格校验后的 `transaction_id`、`dataset_source`、`policy_as_of`、`relation_limit` 固定字段规范序列化后哈希。相同键与相同请求首次发布返回 201，已发布重放返回 200 和原 `run_id`、哈希、版本、审批窗口，不再调用模型或记费用；键相同但请求不同为 `409 idempotency_conflict`；创建中、结果不明或待对账为 `503 persistence_unavailable`，不能启动第二次生成。键至少覆盖运行 TTL 加审计保留期；到期清除后不承诺旧键去重。无键请求无跨进程 exactly-once 保证。内存模式收到该请求头时应返回 `422 invalid_request`，不得静默忽略并让调用方误以为有持久幂等保证。

API 错误仍用 `ApiError` 和 `X-Request-ID`。既有 `approval_binding_mismatch`、`report_hash_mismatch`、`approval_conflict` 为 409，`approval_expired` 和 `run_expired` 为 410；新增 `idempotency_conflict` 为 409、`persistence_unavailable` 为 503。连接故障、未知 schema、缺失或损坏的检查点不得放行决定；无法安全区分的存储故障对外统一 503，不回显 SQL/DSN，内部审计记录细分原因。可确认的请求错哈希仍用 409。已知过期运行 410、未知 ID 404；墓碑清除后可能变为 404。

## 最小持久状态与校验

公开业务 `status`（如 `verified`）与内部存储阶段分开。内部阶段 `preparing → published → expired`，异常可转 `recovery_required`；只有 `published` 可 GET/审批。`published` 中审批可为 `pending/approved/rejected/expired`；`decision_applying` 是不可被另一决定抢占的内部阶段，不能向 GET 展示可操作的 `pending`。

存储 schema 初版为 1。路线 4 的最小表和约束：

| 逻辑表 | 必要字段与约束 |
|---|---|
| `investigation_runs` | `run_id` 主键、schema/mode、规范请求及指纹、创建请求 ID、UTC 创建/运行截止时间、业务/内部状态、唯一 `session_id`、报告哈希/schema 版本、`RunVersions`、白名单服务审计、恢复审批所需的 `ReportRun` 最小字段及检查点引用 |
| `approval_events` | `(run_id, sequence)` 唯一；审批 ID、报告哈希、UTC 时间、事件、首次请求 ID、必要决定字段；每运行至多一个胜出决定 |
| `create_idempotency` | 键摘要唯一、请求指纹、运行 ID、阶段、首次请求 ID、有效期 |
| `cost_attempts` / `cost_budget` | `(run_id, attempt_no)` 唯一，定点金额、`reserved/settled/uncertain`、预算行版本；原子更新总预算 |
| `schema_migrations` | 版本主键、迁移校验和、时间 |
| LangGraph 检查点表 | 固定版本 PostgresSaver 管理；不以报告 JSON 冒充暂停会话 |

数据库仅保存恢复既有 API 展示和审批所必需的**派生**报告/引文、受限审计、决定和费用。不保存原始交易行/CSV、训练标签、模型权重、完整 Prompt、密钥或提供商原始响应。报告哈希仍按 `DerivedReport.model_dump_json().encode("utf-8")` 计算，不能改为 DB JSONB 的字节哈希。GET/审批从已存 `ReportRun` 验证报告、报告审计、审批载荷与检查点的会话/交易/审批 ID/哈希/截止时间；不调用新模型，不用当前政策版本重建旧报告。历史运行的模型、图、索引版本按创建时快照读取。未知 schema 或校验失败时内部标记待修复，对外 fail-closed。

## 提交顺序、补偿与费用

**创建**：① DB 事务占用键/运行 ID，写 `preparing`，以预算行锁预留模型单次调用上限；提交前不调用 provider。② 有界运行；每次尝试以 `(run_id, attempt_no)` 仅结算一次。③ 校验报告与版本，写可恢复报告快照；合格报告写 LangGraph `pending` 检查点并回读验证确实停在 `await_approval`，载荷和报告哈希一致。④ 最后在 DB 事务中复核检查点、TTL/哈希，原子写运行 `published`、审批创建事件、幂等完成状态，之后才返回 201。无审批的调查也要持久化审计才发布，不能伪造检查点。检查点先于公开运行提交，且位于同一 PostgreSQL 持久域。发布后若检查点被异常删除，GET/审批现场复核并 fail-closed。

②～④ 失败时运行不可公开 GET/审批；孤立检查点由对账清理，已经发生的费用预留/实际额保留。重启后对账器只在报告和检查点都完整且校验通过时补发布；生成是否发生不明时转 `recovery_required`，同键重试 503，**不自动再次调用**可能收费的 provider。DB 写入中断不可产生公开孤立 `pending`。

**审批决定**：先核对 URL/会话/交易/报告哈希和已存报告；在一笔 DB 事务中以行锁/条件更新抢占 `pending` 且数据库 UTC 当前时间小于审批截止，写唯一决定意图。指纹包括规范化后的决定、`reviewer_id`、备注及所有绑定字段。只有赢家可推进 LangGraph；完全相同重试读取/继续原意图，不同决定、复核员或备注为 409。推进前后读检查点：若仍暂停只 resume 一次，若已终结且与意图相符则不再次 resume。确认检查点已持久化终态后，在 DB 事务中提交审批终态和顺序审计。响应丢失后的同请求重试返回原 200/原审计，不附加第二事件。意图已抢占但检查点/终态未完成时可由同意图恢复；修复期间公开读/其他决定 fail-closed，不能呈现可操作 `pending`。没有任何外部处置。

**时间**：审批截止首次创建时持久保存，默认 `min(检查点创建时间+30 分钟, run_expires_at)`；重启不重置。数据库 UTC 时间达到截止且没有赢家意图时，原子转 `expired`，只产生一次到期事件，POST 410；截止前已抢占的决定可按原意图完成，不能再接受竞争决定。运行默认 24 小时，达到 `run_expires_at` 后不能 GET/审批。本地单测可注入时钟；真库并发/到期判断使用数据库时间。

**费用**：唯一约束和预算行锁保证本地同一次尝试只预留/结算一次，创建键重放不再调用 provider。第三方 API 在“provider 已收费、响应未到”时无法保证外部账单 exactly-once；保留上限为 `uncertain`，停止自动重跑并人工核对。合同只承诺本地记账幂等与预算保守性。当前本地 Ollama 费用可为 0，也走相同状态机。路线 4 需要让现有 runner 的费用接口使用持久账本适配，不能在服务层和 `CostLedger` 各预留一次。

## 迁移、清理与部署

迁移由独立步骤在事务/迁移锁中运行，记录校验和；空库至 v1 和重复执行 v1 必须成功且不重置数据。服务只接受已支持的 schema 和 PostgresSaver 表版本，未知新版本拒绝就绪，不隐式执行破坏性迁移。升级前备份数据库卷、执行向前兼容迁移、再部署；应用回滚只退到能读当前 schema 的版本，否则恢复备份，不靠 `DROP` 常规回滚。路线 4 真库测试覆盖空库、重复迁移、已存运行升级、不兼容版本拒绝。

默认运行/派生报告/审批/检查点保留至运行 TTL（24 小时）；到期转墓碑并删报告/检查点。最小审计、键摘要、费用事实与过期墓碑再保留 7 天供对账，然后清理。清理分批且幂等，不删除未到期 `pending` 的检查点；DB 不可用时不在内存伪清理。配置改变保留期时，键有效期不能短于运行和对账窗口。路线 4 先单 API worker 验收恢复，另用两个 DB 连接测并发；这不等于宣称多 worker 整体生产安全。数据库卷丢失是数据丢失，不属于 API 重启恢复。

路线 4 需新增显式存储配置、持久服务端口、可选 `Idempotency-Key`、首次 201/重放 200 分支、新错误映射和 DB/检查点就绪检查。内存模式保留；持久 `close()` 不删除未到期状态。代码与真库证据到位前，本合同不得描述为已实现能力。详见[故障矩阵](stage-07-persistence-fault-matrix.md)。

