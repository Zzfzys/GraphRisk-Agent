# Stage 05 路线 7：可靠性与 Agent 评估契约

运行入口为 `scripts/run_stage05_reliable.py`。默认输出文件名带 UTC 微秒时间且以独占创建方式写入，避免下一次运行覆盖失败证据。它复用路线 4 的六节点调查图与路线 5 的派生报告，只在政策检索和生成提供者边界计数实际尝试。`graph_steps` 和图内 `policy_queries/model_calls` 是节点执行次数，不能代替真实 HTTP/检索尝试次数。`policy_attempts`、`generation_attempts`、逐次状态和耗时记录在 `ReliabilityAudit`。

暂时性检索错误限于 `embedding_unavailable`、`dependency_timeout` 或连接/请求超时；生成端只对连接或请求超时重试。每类最多 `max_retries + 1` 次，且分别受 `max_policy_queries`、`max_model_calls` 限制。输入不合法、数据源错绑、检索索引损坏、无效计划、schema/证据校验错误不重试。总截止时间在依赖调用前后检查；Ollama HTTP 超时取请求上限和剩余总时间的较小值。本地同步计算无法强制抢占，因此 `max_total_seconds` 是软截止时间，运行中调用返回后才可能标记超时。

费用门禁用 `CostLedger` 在每次生成请求**发出前**预留上界；成功后以供应端返回的估算费用结算。超时后实际是否计费未知，保留预留额；若下一次预留会超过 `api_stop_cny` 或 `api_budget_cny`，立即停止。该账本是单进程内存态，不是跨进程计费系统；有费用的外部 API 必须给每次请求的保守费用上界，并在部署时接持久账本。本地 Ollama 和 fake 运行按 0 元记录，不等于模型有完整成本测量。

固定案例在 `data/evaluation/stage05_agent_cases_v1.yaml`，运行 `uv run --offline python scripts/evaluate_stage05_agent.py` 生成 `reports/generated/stage05-agent-eval-v1.json`。测试夹具使用模拟政策和 Stage 04 合成调查报告，绝不作为 IEEE-CIS 或真实企业规则效果。每个案例检查预期分支、真实尝试次数、审批状态、无外部处置、引用来自本次检索且原文/版本相同、无无证据政策结论；记录固定工具选择与参数绑定、延迟、Token、估算费用和重试轨迹。注入案例把恶意动作要求放进不可信检索文本，并模拟模型尝试输出 `auto_block`；状态机拒绝，不能据此声称已覆盖所有提示注入。旧版案例把已失效 v1 片段在新版日期注入本次结果，报告校验拒绝。真实模型抽样另行记录，不能与确定性 17 案例合并成质量统计。
