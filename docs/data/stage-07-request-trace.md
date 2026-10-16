# Stage 07 请求 ID、工具轨迹与安全审计契约

> 2026-09-28 路线 2 已按本地演示边界实现。请求追踪用于排障与审计关联，不是用户身份认证，也不是跨进程持久追踪存储。

## ID 与生命周期

- 每次 HTTP 请求由服务端生成 `req-` 加 32 位小写十六进制 ID；来访的 `X-Request-ID` 不被采信。响应头总带 `X-Request-ID`，包括校验错误、服务错误与未捕获异常的安全 500。
- `run_id` 标识业务调查，`request_id` 标识一次 HTTP 调用。创建、GET、审批各用不同请求 ID；`session_id` 是审批检查点标识，不是请求或复核员身份。
- `InvestigationRecord.created_request_id` 固定创建请求 ID；调查视图 `audit.creation_request_id` 和每条工具/检索/生成尝试的 `audit.tool_events[*].request_id` 对应这次创建。普通重读不会改动冻结报告或创建审计。
- 审批 `audit[*].request_id` 绑定创建、首次决定或实际观察到到期的请求。完全相同的决定重试仍指向首次决定的审计事件；本次重试自身的请求 ID 在响应头与结构化 HTTP 日志中。读取本身不新增审批事件。

## 结构化日志

应用只写固定字段的 `graphrisk_trace` JSON：`at`、`request_id`、`run_id`、`phase`、`tool`、`status`、`error_code`、`elapsed_ms`、`model_version`、`index_version`、`http_status`。`phase` 区分 `http`、`tool`、`agent_node`、`dependency_attempt`、`approval`。日志在调查创建时记录现有五工具审计、Agent 节点顺序、政策检索/模型生成尝试；GET 与审批记录各自的 HTTP/审批事件。模型版本优先使用固定 digest，索引版本使用当前政策索引哈希。所有可变标识先经过有限字符与长度检查。

日志不读取或序列化请求体、响应体、原始交易行、标签、卡/设备/身份键、政策原文、完整 Prompt、连接密钥、复核员 ID 或备注。应用禁用 Uvicorn 原始 URL access log，以免查询参数落盘；上游代理日志由部署环境另行配置。日志目前随进程/本地日志文件存在，尚无 Stage 09 的指标平台或分布式 trace 后端，也不因请求 ID 声称 `reviewer_id` 已认证。

## 复现与兼容

```powershell
uv run --offline pytest -q tests/api/test_stage07_request_trace.py tests/integration/test_stage07_agent_evaluation.py
uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage06-restart --probe-stage07-trace
uv run --offline python scripts/probe_stage07_trace.py
uv run --offline pytest -q
```

真实本地探针用现有 IEEE-CIS held-out `3488959` 和已锁定的本地 `qwen2.5:0.5b`；生成结果只写入忽略目录 `reports/generated/stage07/`，仅保留 ID、状态、版本、工具名/计数和报告哈希，不保存交易原行或政策全文。Stage 06 的请求体、公开状态/错误码、审批绑定和工作台页面语义保持；调查/审批响应只增加可选审计字段。新 500 `internal_error` 只用于未捕获异常，不回显异常详情。

**后续边界。** 路线 4 将 `created_request_id`、审批事件请求 ID 与必要运行审计一起纳入持久化合同；本路线的内存记录和日志不能证明重启恢复。跨进程请求关联与日志保留策略需在持久化/部署验收中再核对。
