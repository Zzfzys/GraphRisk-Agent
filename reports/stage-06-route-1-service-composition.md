# Stage 06 路线 1：服务组合与运行状态验收

**日期：2026-09-26。工程门禁：通过；Stage 06 保持 In Progress。** 请求/状态/错误约定见[服务契约](../docs/data/stage-06-service-contract.md)，学习练习见[Stage 06 学习路线 L1](../docs/plans/stage-06-learning-roadmap.md)。

## 实现与核对

- 将 Stage 05 的 `build_components` 从脚本移到 `src/graphrisk/agent/components.py`，原有路线 4、7、8 脚本复用同一函数。FastAPI 进程在启用 `GRAPHRISK_STAGE06_CONFIG` 后，通过 lifespan 调用 `Stage06Service.from_config`；未启用时保留 Stage 02 API 的启动语义。
- 服务启动一次加载配置、图和政策索引，创建共享费用账本与真实 Ollama provider；每次调查在单进程中保存报告、版本、审计和审批检查点。记录默认容量 32、TTL 24 小时。`run_id` 带实例标识，`session_id` 与报告 SHA-256 绑定；过期、满额和重启失效均为明确错误。服务关闭/记录过期会删除对应 checkpoint。
- `verified` 映射 `source_verified`；`partial_evidence` 和 `insufficient_evidence` 分别保持部分/不足状态。即使得到 `source_verified`，它也不等于政策已适用或交易已证实欺诈。版本或请求绑定错误在保存前停止。审批备注与对外 API 留路线 2、3。

## 实测命令与结果

```powershell
uv run --offline pytest tests/unit/test_stage06_service.py tests/api/test_risk_api.py -q
uv run --offline python -c "from graphrisk.serving.investigation_service import Stage06Service; s=Stage06Service.from_config('configs/stage05.yaml'); print(s.dataset_source, s.versions.graph_comparison_run, s.versions.policy_index_version, s.runner.cost is s.cost_ledger); s.close()"
uv run --offline python scripts/validate_stage05_end_to_end.py --provider fake
```

定向回归通过：新服务与旧 API 合计 13 项测试。真实本机组装读到 `ieee_cis`、`stage05-route8`、政策索引版本 `9eade9b4c7f38df917267731ebe1d19a0aa8c270cbe1b8234a64d64b1605b3e7`，并确认运行器使用同一个费用账本。真实 `3488959` / `2026-07-01` / 关系上限 3 的进程内调查返回 `verified`、`source_verified`、`pending`；报告哈希与审批载荷一致，按运行 ID 重读命中同一记录。旧 Stage 05 路线 8 脚本在改用共享组合函数后用 fake provider 复验，仍返回 `verified / rejected / insufficient_evidence / generation_error`。输出只打印状态与布尔核对，不打印交易标签或报告全文。

全量 `uv run --offline pytest -q`：**268 passed，1 条 Starlette TestClient 的 anyio 别名弃用警告**，不影响本阶段行为。`uv build --offline --out-dir reports/generated/stage06-route1-build` 成功产出 wheel 和 sdist，二者均包含新组合模块；文件清单检查未发现 `.uv-cache`、`.venv`、原始数据、模型权重或生成报告。路线 2 才实现公开 HTTP 调查入口、就绪检查与安全投影；路线 3 才实现审批提交和备注。当前没有 UI 或跨进程审批持久化证据。
