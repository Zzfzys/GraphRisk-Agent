# Stage 06 路线 3：人工审批接口与备注验收

> 2026-09-26；工程门禁：通过。个人 L3 学习自测由学习者自行完成。Stage 06 总体仍为 In Progress。

## 交付

- `GET /api/v1/investigations/{run_id}/approval` 返回当前 `pending / approved / rejected / expired`、冻结报告哈希、到期时间与顺序审计；无可审报告返回 `approval_not_available`（409）。
- `POST` 同一路径要求 `run_id`、`session_id`、`transaction_id`、`report_sha256`、`decision=approve|reject`、`reviewer_id` 和可选 `note`。备注去除首尾空白后最多 500 字符，进入独立审批决定与审计，不写回冻结调查报告。
- 服务用同进程锁串行化决定，核对运行、会话、交易、哈希和当前报告。完全相同的决定、复核员和规范化备注重试返回原状态；不同内容返回 `approval_conflict`（409）。错误绑定、报告变化、到期、记录到期和旧服务实例分别有机器可读错误。
- 审批窗口默认 30 分钟，运行记录默认 24 小时；审批过期后仍可查询调查报告，但不可批准。响应明示 `reviewer_authenticated=false` 和 `external_action_count=0`。当前并无正式身份认证或任何外部处置。

## 自动验证

```powershell
uv run --offline pytest tests/api/test_investigation_api.py tests/unit/test_stage05_approval.py -q
uv run --offline pytest -q
```

聚焦 27 项通过；全量 **287 passed, 1 warning**。警告来自 Starlette TestClient 对 anyio 旧别名的弃用提示。API 测试覆盖批准、拒绝、备注、空白与超长备注、准确重试、冲突决定/复核员/备注、路径与会话/交易/哈希错绑、无可审政策报告、审批到期、运行到期、重启失效和报告被篡改。Stage 05 审批回归保持通过；测试断言冻结报告与原 SHA-256 不变、审计不重复、外部动作恒为 0。

## 真实本机 HTTP 验证

用仓库真实 `configs/stage05.yaml` 启动单 worker：

```powershell
$env:GRAPHRISK_STAGE06_CONFIG = 'configs/stage05.yaml'
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8765
```

请求 IEEE-CIS held-out 交易 `3488959`、`policy_as_of=2026-07-01`、`relation_limit=3`：`/ready` 为 200 `ready`，调查创建为 201 `verified/pending`。对返回的同一 `run_id`、`session_id`、交易 ID 和 `report_sha256` 提交 `reject`、`reviewer_id=demo-reviewer`、带空白的简短备注；响应为 200 `rejected`，备注规范化为 `Needs manual source check.`。再次 GET 审批得到相同结果，审计仅有 `created` 和 `rejected` 两条；GET 调查报告与创建时相同，哈希未改变，外部动作计数为 0。随后停止本地服务。

## 学习复盘 L3

手绘 `pending → approved/rejected/expired`，再用自己的话解释：为什么 `session_id` 只标识审批检查点、`report_sha256` 绑定冻结内容，以及为什么备注必须放在独立审计。可手动尝试把同一审批的 `report_sha256` 改为 64 个 `f`，预期为 409 `approval_binding_mismatch`；提交后的相反决定预期为 409 `approval_conflict`。完成此自测前不把个人学习项标为完成。
