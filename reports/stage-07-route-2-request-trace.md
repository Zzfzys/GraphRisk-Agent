# Stage 07 路线 2：请求 ID、工具轨迹与安全审计验收

> 2026-09-28 工程门禁通过；个人 L2 自测待学习者完成。Stage 07 整体仍为 In Progress。

## 实现结果

- [请求追踪实现](../src/graphrisk/serving/request_trace.py)为每次 HTTP 请求生成服务端 ID，响应头返回 `X-Request-ID`；来访同名头不决定身份。创建请求 ID 写入调查运行、工具与依赖尝试视图，审批创建/决定/到期审计事件绑定相应请求。读取与审批另有独立请求 ID，沿同一 `run_id` 在结构化日志中关联。
- API 日志只写固定 11 个元数据字段和有限字符的版本/状态/工具名；记录五个调查工具、Agent 节点、检索/生成尝试与审批分支。原始 Uvicorn URL access log 已关闭，避免未受限路径/查询参数进入进程日志。日志不含请求体、报告正文、政策原文、Prompt、复核员备注或凭据；未捕获异常只返回带请求 ID 的安全 500。
- [数据与安全契约](../docs/data/stage-07-request-trace.md)写清 `request_id` 与 `run_id`、`session_id`、审批身份的不同，以及内存/日志尚未持久的边界。Stage 06 的业务状态、错误码与工作台请求方式保留。

## 验证证据

| 检查 | 结果 |
|---|---|
| fake 调查创建→GET→人工拒绝 | 三个不同请求 ID；同一 `run_id`；工具/检索/生成事件归属创建请求；决定审计绑定决定请求；GET 后冻结调查视图不变 |
| 并发 4 次创建 | 4 个不同请求 ID 与 `run_id`；每组工具日志仅归属自己的二元组，无串号 |
| 无政策、输入错误、旧实例错误、未捕获异常 | 均有响应头和结构化状态/错误；无政策不伪造生成尝试；未捕获异常不回显详情 |
| 审批到期 | `expired` 审计事件带实际观察到到期的 GET 请求 ID |
| 日志隐私 | 故意输入伪造 `X-Request-ID`、敏感备注、额外请求字段及异常消息；应用 trace 日志和安全错误响应均未包含这些值 |
| 旧 API/工作台回归 | 全量测试中的 Stage 06 API、50 案与 Streamlit 测试继续通过 |

真实本地探针命令 `uv run --offline python scripts/probe_stage07_trace.py`：使用既定 IEEE-CIS held-out `3488959`、当前 TF-IDF 政策索引和本地 `qwen2.5:0.5b`；调查 `verified`、人工拒绝 `rejected`、外部动作 0。输出在忽略目录 `reports/generated/stage07/route2-real-trace-20260928T025813261045Z.json`；记录运行 ID `run-5e55c11a24ce-fb5db58954904e5c9a027b84e16554fa`，创建/读取/审批请求 ID 互异，工具事件 **7**（5 个调查工具、1 次政策检索、1 次生成），结构化日志为 `tool=5`、`agent_node=6`、`dependency_attempt=2`、`http=3`、`approval=1`。输出不含原始交易行、标签或政策正文。

Stage 07 冻结 v2 追踪案例 `trace_links_create_read_approval` 用真实 FastAPI 调用和结构化日志观察评分，**1/1 通过**。连同前一条重启探针执行 `uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage06-restart --probe-stage07-trace`，得到 `reports/generated/stage07/agent-eval-v2-initial-20260928T030020Z.json`：新冻结集 15 案中 **2 已观察、1 通过、1 失败、13 `not_run`**。失败仍是旧内存模式重启后的 `410 run_instance_mismatch`；路线 2 没有改写它或把未运行案例计为通过。

聚焦回归 `uv run --offline pytest -q tests/api/test_stage07_request_trace.py tests/integration/test_stage07_agent_evaluation.py tests/api/test_investigation_api.py` 为 **29 passed**；全量 `uv run --offline pytest -q` 为 **316 passed，1 条第三方 Starlette/anyio deprecation warning**。`uv build --offline` 成功生成 sdist 与 wheel，包清单未包含 `.uv-cache` 或生成报告。未引入第三方框架或修改 `uv.lock`。目前请求与审批关联只保存在当前进程和本地日志；路线 3～4 才制定并验收重启后的持久关联，路线 6/8 才汇总全部新案与真库评估。

## 学习任务 L2（个人待完成）

先画“创建 `request_id` → 同 `run_id` 的工具/Agent → GET 新 `request_id` → 审批新 `request_id`”四栏图，再打开真实探针 JSON 对照。解释为什么 `request_id` 能追踪一次 HTTP 调用，却不能证明 `reviewer_id` 身份；尝试在本地测试请求放一个无害 canary，并确认只允许固定元数据进日志。个人亲自复现并能解释后，再在学习记录中勾选 L2。
