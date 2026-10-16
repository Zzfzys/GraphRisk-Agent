# Stage 06：服务状态与接口契约

> 2026-09-26 更新。路线 1～3 的服务组合、调查和人工审批 HTTP 接口已实现。配套[演示输入契约](stage-06-demo-contract.md)、[路线 1 验收](../../reports/stage-06-route-1-service-composition.md)、[路线 2 验收](../../reports/stage-06-route-2-investigation-api.md)和[路线 3 验收](../../reports/stage-06-route-3-approval-api.md)。

## 归属和生命周期

`GRAPHRISK_STAGE06_CONFIG=configs/stage05.yaml` 启用 FastAPI lifespan 的 Stage 06 组合；未设置时，既有 Stage 02 `/risk/predict` 服务保持原来的启动路径。单个 API 进程在启动时读取源配置、模型/图、政策索引和 Ollama provider，并创建唯一 `Stage05ReliableRunner`、共享 `CostLedger`、`Stage05ApprovalWorkflow` 和内存记录库。每次调用复用这些依赖，只有调查结果和报告按运行保存；Streamlit 不持有业务事实。进程停止时关闭服务并清理检查点，重启的实例标识不同。

一个运行默认最多保留 24 小时，审批窗口默认 30 分钟（不超过运行 TTL），默认最多 32 个记录；满额时拒绝新的调查，不淘汰尚有效的待审报告。审批窗口到期后运行仍可读取，审批状态变为 `expired`；运行到期时删除记录和对应 LangGraph 检查点。`run_id=run-{instance_id}-{random}` 唯一标识调查，`session_id=session-{run_id}` 仅对可审批报告产生，`report_sha256` 按冻结 `DerivedReport.model_dump_json()` 计算并与 Stage 05 审计及审批载荷核对。旧实例的 `run_id` 报 `run_instance_mismatch`；同实例已到期的 ID 报 `run_expired`。读取运行记录返回深拷贝，不能修改服务内报告。直接重复创建调查会产生新运行；浏览器刷新必须按 `run_id` 读取，不再 POST 创建。

## HTTP 字段约定

`POST /api/v1/investigations` 使用 Stage 05 的严格 schema：`transaction_id`（1～128 字符）、`dataset_source`（实际演示只接受 `ieee_cis`）、显式 `policy_as_of`（`YYYY-MM-DD` 日期）、`relation_limit`（整数 1～50，默认 3）。不从服务器时钟推断政策日期，不接受第二数据源。`GET /api/v1/investigations/{run_id}` 重读同一运行。创建和查询响应只投影必要字段：

| 字段 | 说明 |
|---|---|
| `run_id`, `session_id`, `report_sha256` | 查询和审批绑定；无可审报告时后两项为空 |
| `status`, `evidence_state` | 原始 Stage 05 状态；`source_verified`、`partial`、`insufficient` 或 `unavailable` 只描述证据流程，**不是欺诈判断** |
| `versions` | 图比较运行、政策索引版本、生成模型名称和 digest |
| `created_at`, `expires_at` | UTC 时间；过期后旧审批不可恢复 |
| `report`, `audit`, `approval` | 通过白名单投影交易、模型、图、政策出处、必要审计与待审批状态；绝不直接序列化内部 `ReliableRun` 或原始测试标签 |
| `simulated_policy`, `scores_calibrated`, `automatic_action_executed` | 固定为 `true`、`false`、`false`，提醒演示边界 |

当 `verified` 且来源引文核验通过，可得到 `source_verified` 和 `pending`。`partial_evidence` 仍是 `partial`；若报告合格可人工复核，但不能在 UI 中变成“完整欺诈结论”。`insufficient_evidence` 只有 `insufficient`，没有待审会话。模型/检索/预算/截止时间错误归入 `unavailable`，保留原始状态与机器可读错误码。

`GET /api/v1/investigations/{run_id}/approval` 读取审批状态；`POST` 同一路径提交 `run_id`、`session_id`、`transaction_id`、`report_sha256`、`decision=approve|reject`、`reviewer_id` 和可选 `note`。`note` 去除首尾空白后最多 500 字符，空白变成空值；它只进入独立审批决定与审计，不改报告字节和哈希。审批视图包括 `status`、`decision`、`reviewer_id`、`note`、顺序审计和 `external_action_count=0`。`reviewer_authenticated=false` 明示 `reviewer_id` 是演示字段，并非认证身份。审批必须检查有效期、同一运行/会话/交易/哈希；**完全相同**的决定、复核员及规范化备注重试返回原结果，任一字段变化均为冲突。审批通过只代表本地演示中的人工复核决定，不触发外部处置。

## 错误分类与边界

| 类别 | 服务层错误码 | 预期处理 |
|---|---|---|
| 输入 | Pydantic 校验、`dataset_source_mismatch` | 请求无效；不调用 Stage 05 |
| 运行容量/失效 | `run_capacity_exceeded`, `run_expired`, `run_instance_mismatch`, `run_not_found`, `service_stopped` | 不重用旧审批；前端给出重新调查操作 |
| 证据绑定 | `graph_request_binding_mismatch`, `report_request_binding_mismatch`, `report_hash_mismatch`, `report_status_mismatch` | 内部不一致；不保存记录或启动审批 |
| 版本绑定 | `policy_index_version_mismatch`, `generation_model_mismatch`, `generation_model_revision_mismatch` | 停止，不把其它版本的证据并入本运行 |
| 审批检查点 | `approval_checkpoint_failed` | 不交付可审批状态；清理孤立检查点 |
| 审批绑定与冲突 | `approval_not_available`, `approval_run_mismatch`, `approval_binding_mismatch`, `approval_conflict`, `report_hash_mismatch` | HTTP 409；不改变既有决定或报告 |
| 审批到期 | `approval_expired` | HTTP 410；运行可能仍可查询，审批不可再恢复 |
| Stage 05 业务终态 | `not_found`, `invalid_plan`, `retrieval_error`, `generation_error`, `deadline_exceeded`, `budget_exhausted`, `upstream_error` | 保留受控审计；由路线 2 映射 HTTP 状态与安全错误信息 |

服务的内部 `InvestigationRecord.result` 含原始 Stage 05 对象，**仅供 API 投影使用**；前端绝不直接接收它。路线 2 已按上表提供机器可读错误：成功调查创建为 201、读取为 200；部分或无政策仍是成功调查；无交易 404、过期 410、非法输入 422、容量/预算 429、依赖故障 503、超时 504、内部证据故障 500。`GET /api/v1/health` 与 `/api/v1/ready` 区分进程存活和依赖就绪。此阶段仍只允许单 worker，跨进程持久恢复不在本契约内。
