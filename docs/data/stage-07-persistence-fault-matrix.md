# Stage 07 路线 3：持久化故障与测试矩阵 v1

> 与[持久化合同](stage-07-persistence-contract.md)一同冻结。以下是路线 4 的**预期**，不是已通过的 PostgreSQL 测试。冻结 `data/evaluation/stage07_agent_frozen_v2.yaml` 保持原样。

| ID / 固定挑战 | 注入位置与前置事实 | 预期 HTTP / 可见状态 | 必查的数据库、审计和费用事实 |
|---|---|---|---|
| P01 `pending_restart_recovers` | 发布合格 `pending` 后停止 API，新进程按原 `run_id` GET | 200；同一 ID、报告哈希/版本/审批截止时间，仍 `pending` | `published` 与有效 `await_approval` 检查点配对；原费用/创建请求 ID 不变 |
| P02 `decision_retry_after_lost_response` | 拒绝终态已提交，丢第一份 HTTP 响应，另一进程重试完全相同请求 | 200，仍 `rejected` | 决定唯一、拒绝事件仅一条、首次决定请求 ID 不变、外部动作 0 |
| P03 `conflicting_decisions_race` | 两连接同时 approve/reject，或更改复核员/规范化备注 | 赢家 200；输家 409 `approval_conflict` | 至多一个决定意图/终态，审计无重号，检查点与赢家一致 |
| P04 `approval_expiry_survives_restart` | `pending` 超过持久截止时间，重启后 POST | 410 `approval_expired`；未过运行 TTL 的 GET 可见 `expired` | 到期事件一条、无决定事件；审批窗未重置 |
| P05 `database_read_outage_fails_closed` | 断开已有 `pending` 的 DB 后 GET，再恢复 | 故障时 503 `persistence_unavailable`；恢复后 200 原运行 | 无内存回退/决定事件；哈希与检查点一致 |
| P06 `database_write_interrupt_no_orphan_pending` | 报告生成后、公开发布前断开 DB/注入写故障 | POST 503 `persistence_unavailable`，不可 GET 到可审批 `pending` | 只可留 `preparing/recovery_required` 或待清孤立检查点 |
| P07 `creation_retry_cost_once` | 带 `Idempotency-Key: stage07-cost-v2` 创建已提交，丢响应后重试 | 首次 201、重放 200，原 ID/哈希/审批窗口 | provider 调用不增加；预算预留/结算唯一，累计费用不变 |
| P08 报告哈希错绑 | 提交哈希改一位，或篡改已存报告/检查点 | 请求或已存报告哈希错绑 409 `approval_binding_mismatch`/`report_hash_mismatch`；无法验证的存储故障 503 | 决定/审计/费用不变；不重算报告 |
| P09 意图提交后崩溃 | 决定意图已提交，检查点尚未推进；重启同决定 | 修复后 200 原决定；修复中 503 | 同一意图恢复，无第二事件；竞争决定 409 |
| P10 检查点推进后崩溃 | LangGraph 终态已持久，审批终态事务尚未提交 | 修复后 GET/重试一致；修复中 503 | 根据检查点补齐原决定，不第二次 resume/记审计 |
| P11 检查点丢失/错版本 | 删除/改坏已发布运行检查点或 schema 版本 | GET/审批 503 `persistence_unavailable`，不呈现可操作 `pending` | 内部记录 `checkpoint_missing`/`schema_incompatible`，报告/费用不变 |
| P12 幂等键请求冲突 | 相同键，更改交易/日期/关系上限 | 409 `idempotency_conflict` | 原运行、provider 次数和费用不变 |
| P13 无键与内存兼容 | 连续相同 POST；内存模式重启读旧 run | POST 各 201 新运行；内存旧 run 410 `run_instance_mismatch` | 无键无跨进程去重承诺；持久模式不按 instance 拒绝 |
| P14 迁移与清理 | 空库/重复迁移/未知新版本；24h TTL 与 7d 墓碑 | 已知过期 410、未知 404；不兼容/DB 故障 503 | 重复迁移不改数据；清理不动未过期 checkpoint；无原始 CSV/标签/密钥 |
| P15 费用结果不明 | provider 调用后网络/进程中断，收费与否不明 | 同键重试 503 待对账，不重新生成 | 原预留仍占预算、状态 `uncertain`，不虚报实际额 |

路线 4 真库测试至少将 P01～P07 对应到冻结 v2 探针，P08～P15 用真实 PostgreSQL 分别触发。断连测试恢复后必须再读原记录；mock 或静态 SQL 不能充当重启、事务和并发证据。冻结 v2 中以上七个案例的输入与评分规则不因实现便利改写。


