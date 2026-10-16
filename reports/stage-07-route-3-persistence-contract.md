# Stage 07 路线 3：持久化、恢复与兼容合同验收

> 2026-09-28 工程**设计合同门禁**通过；个人 L3 自测待学习者完成。PostgreSQL 业务持久化、迁移、跨进程恢复和真库故障测试属于路线 4，尚未完成。Stage 07 整体仍为 In Progress。

## 交付与决定

[持久化/兼容合同](../docs/data/stage-07-persistence-contract.md)固定了显式内存/PostgreSQL 模式、旧 `run_id` 形状、跨进程查库、请求/报告/审批版本绑定、24 小时运行与 30 分钟审批 TTL、可选创建幂等键、本地费用预留和结算、迁移/回滚/清理。公开业务状态与内部存储阶段分离；合格 `pending` 只有在 LangGraph 检查点写入并校验后才可发布。审批以数据库唯一决定意图抢占，检查点推进与最终决定审计可在崩溃后按同一意图对账。数据库、schema 或检查点故障一律 fail-closed，不静默退回内存。

[故障矩阵](../docs/data/stage-07-persistence-fault-matrix.md)列出 P01～P15 的注入点、HTTP/可见结果和数据库/审计/费用不变量。其中 P01～P07 与冻结 v2 的七个持久化挑战逐一对应，覆盖重启、重复和冲突决定、过期、断连、发布中断、创建重试不重复计费。额外覆盖错哈希、两处决定崩溃窗口、缺检查点、键冲突、无键/内存兼容、迁移/清理和不确定费用。合同明确本地账本幂等不等于第三方 API 账单 exactly-once；不确定调用先占预算并待对账。未认证的 `reviewer_id` 不升级为身份认证，外部动作仍为 0。

与现有代码核对：`Stage06Service` 当前仍使用 `OrderedDict`、`InMemorySaver` 和 `CostLedger`，跨进程 GET 会返回 `run_instance_mismatch`；`Stage05ApprovalWorkflow` 已有相同决定幂等语义，但其锁只保护单进程。因此合同要求路线 4 换成真库事实来源，并保留内存模式的诚实失效语义。HTTP `Idempotency-Key`、`persistence_unavailable` 和 `idempotency_conflict` 目前仅是冻结目标，API 尚未实现。

## 验证与限制

- 冻结 v2 文件 SHA-256 仍为 `60fcad25b9dda57ecd243a226b7d41ce2bda88d28dd89ce88fd460784091f1d1`；本路线未改案例或评分。
- YAML/矩阵静态核对：七个持久化挑战 ID 均在冻结集和矩阵中，P01～P15 均存在。
- `uv run --offline pytest -q`：**316 passed，1 条第三方 Starlette/anyio 弃用警告**。这是旧路径回归，不证明路线 4 的 DB 行为。
- 文档差异检查通过；本路线未修改运行代码、锁文件或数据库 schema，故未声称真库恢复、并发安全或费用实测。

路线 4 的第一批可执行切片应依次完成：显式模式/迁移和持久 ledger → 创建 `preparing`/检查点/发布 → 跨进程 GET → 决定意图与恢复 → P01～P15 真库故障注入。路线 4 开始前按路线 0 的选型把固定兼容依赖加入项目锁文件；真实 API 保持单 worker，另外用双连接验证决定竞态。

## 学习任务 L3（个人待完成）

按[学习路线](../docs/plans/stage-07-learning-roadmap.md)亲自画四条时序：报告生成后检查点失败、检查点成功而发布失败、决定成功但响应丢失、两人同时决定。每条标出事务/幂等键、可见状态、费用、错误码与外部动作 0；再画审批 30 分钟和运行 24 小时两条时间轴，并解释本地账本与第三方收费的边界。工程合同通过不代替个人讲解与实操。
