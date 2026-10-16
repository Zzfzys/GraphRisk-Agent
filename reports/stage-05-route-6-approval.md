# Stage 05 路线 6 验收：人工审批中断与恢复

> 执行日期：2026-09-25；路线状态：**Done**。Stage 05 整体仍为 `In Progress`，运行级重试、全程超时、评估集及生产身份认证尚未交付。

## 已实现

[审批契约](../docs/data/stage-05-approval.md)记录了 `InMemorySaver` + LangGraph `interrupt` 的同进程状态机。待审摘要冻结交易 ID、报告哈希、建议动作、证据 ID、政策引用、调查 ID 和到期时间。恢复时核对会话、交易、哈希及当前报告；批准/拒绝仅更新建议状态并追加独立审批审计，不调用任何外部处置工具。`await_approval` 在中断前无副作用，避免恢复重跑时重复写入。

修改报告或超过有效期使旧请求 `expired`；错会话、错哈希、错交易及非法决定不消耗检查点。同一决定重复恢复幂等，相反决定被拒绝。当前 `reviewer_id` 由调用方传入，尚无身份认证；这只验证审批状态语义，工作台身份与跨进程持久化属于后续工程。

## 验证

`uv run --offline pytest tests/unit/test_stage05_approval.py -q`：**9 passed**，覆盖真实中断载荷、批准、拒绝、未批准、错会话/哈希/交易、非法决定、重复提交、到期、报告变更、非法报告以及共享检查点的同进程恢复。全量 `uv run --offline pytest -q`：**248 passed**。`uv build --offline`、`uv lock --check --offline`、`git diff --check` 通过。

对路线 5 真实 Ollama 派生报告做同进程演示，使用独立演示会话分别输入 `reject` 和 `approve`：两次均先返回 `pending`，再返回 `rejected` / `approved`，各有连续的 `created` 与终态审计事件，`external_action_count=0`。输出在 `reports/generated/stage05/route6-*-demo.json`，不提交仓库。这里的 `demo-reviewer` 是模拟标识，不能视为真实员工授权或真实风控处置。

下一步路线 7 将验证暂时性故障、有限重试、全程超时、费用停止门禁和更多 Agent 案例。
