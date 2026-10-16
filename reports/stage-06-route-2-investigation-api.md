# Stage 06 路线 2：调查 API 验收

**日期：2026-09-26。工程门禁：通过；Stage 06 仍为 In Progress。** 对外契约见[服务契约](../docs/data/stage-06-service-contract.md)。本路线提供调查创建、重读、进程健康与依赖就绪；人工审批提交和备注属于路线 3。

## 接口与证据边界

| 接口 | 行为 |
|---|---|
| `GET /api/v1/health` | 仅报告 API 进程是否存活及 Stage 06 服务是否启用，不把数据或 Ollama 故障当进程故障 |
| `GET /api/v1/ready` | 核对交易/特征、风险模型、图、政策索引和当前 digest 的 Ollama；缺项返回 503 和逐项布尔状态，不暴露文件路径 |
| `POST /api/v1/investigations` | 严格验证交易 ID、数据源、显式政策日期和 1～50 的关系上限；默认上限 3。正常、部分和无政策调查返回 201；业务故障返回结构化 `error_code` 与可重读的 `run_id` |
| `GET /api/v1/investigations/{run_id}` | 从同进程记录读取，成功为 200；绝不重新调用模型。到期或旧实例 ID 返回 410 |

成功响应从内部 `ReliableRun` **显式投影**交易金额/产品、历史计数、Stage 02 未校准模型分数、GraphSAGE 与融合分数、图状态/历史邻居、政策版本和原文出处、工具审计摘要及待审批载荷。共享实体只显示类型，不外传设备/卡片等原始实体 ID。引文在有检索结果时再次核对 chunk、版本、原文、来源和生效日期；不返回测试标签、原始交易行、提示词或整个内部对象。`source_verified` 只说明引文来源已核验，不是欺诈结论。`simulated_policy=true`、`scores_calibrated=false` 和 `automatic_action_executed=false` 随响应提供。

HTTP 映射：`not_found→404`、无效请求/错数据源 `→422`、容量/预算 `→429`、过期/旧进程 `→410`、检索/生成/上游不可用 `→503`、总超时 `→504`、内部证据绑定或未知错误 `→500`。`partial_evidence` 与 `insufficient_evidence` 都是调查已完成但证据有限的 201/200 响应，不提升成完整欺诈判断。OpenAPI 列出这些分支；日志仅记录运行 ID、状态、耗时或安全错误码。

## 可复现检查

```powershell
uv run --offline pytest tests/api/test_investigation_api.py tests/api/test_risk_api.py tests/unit/test_stage06_service.py -q
uv run --offline pytest -q
uv lock --check --offline

# 另开 PowerShell，仓库根目录；单 worker 且只监听本机。
$env:GRAPHRISK_STAGE06_CONFIG = 'configs/stage05.yaml'
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1

# 在第二个 PowerShell 窗口运行。
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
$body = @{transaction_id='3488959';dataset_source='ieee_cis';policy_as_of='2026-07-01';relation_limit=3} | ConvertTo-Json -Compress
$run = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/investigations -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/investigations/$($run.run_id)"
```

本次在 `127.0.0.1:8000` 实际运行：健康 `ok`，就绪 `ready`（data、risk_model、graph、policy_index、ollama 均为 `true`）；held-out 交易 `3488959` 的 POST 为 **201**、`verified` / `source_verified`、审批 `pending`、报告哈希与审批载荷相同。政策引文 1 条，`source_type=simulated`，chunk 为 `policy-97f6eb79d2a992d3cc2fd187`；图状态 `ok`，生成模型 `qwen2.5:0.5b`。GET 为 **200** 且 `run_id` 相同；响应文本未见 `is_fraud` 或 `label` 键。演示服务已停止。

Fake 注入测试覆盖正常、部分图、无政策、无交易、错数据源、非法日期/上限、检索和生成故障、预算、超时、过期、未启用服务、就绪失败及未知异常去敏；旧 Stage 02 风险接口回归仍通过。路线 3 才提供审批提交接口，因此本路线的 `pending` 不能在 HTTP 上批准或拒绝。

验证结果：定向 **25 passed**；全量 `uv run --offline pytest -q` **280 passed**，另有一条 Starlette TestClient 的 anyio 别名弃用警告。`uv lock --check --offline` 与 `git diff --check` 通过。`uv build --offline --out-dir reports/generated/stage06-route2-build` 成功；wheel/sdist 均包含新增 API 和投影模块，文件清单未见 `.uv-cache`、`.venv`、生成报告或模型权重。
