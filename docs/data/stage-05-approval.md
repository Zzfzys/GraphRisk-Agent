# Stage 05 路线 6：同进程人工审批契约

> 2026-09-25。审批仅改变调查**建议的状态**，不连接封禁、拒付、通知或其它外部处置。跨进程持久恢复和工作台接入仍属后续阶段。

## 待审对象

`Stage05ApprovalWorkflow.start(report_run, session_id)` 只接受路线 5 `verified` 或 `partial_evidence`、带政策原文引用、建议为 `human_review` 的派生报告。它重算报告 SHA-256，并核对派生审计的报告哈希、原调查 ID 和状态。`partial_evidence` 仍允许提交**人工补证/复核建议**，不会被标成证据完整。其他状态、篡改后的报告和没有政策证据的报告不能进入审批。

进入 LangGraph 前冻结 `session_id`、`approval_id`、交易 ID、数据源、报告哈希、原调查 ID、建议动作、交易/模型/图证据 ID、政策 `chunk_id` 及到期时间。`await_approval` 节点首先调用 `interrupt`，在此之前没有不可重复的副作用；恢复时 LangGraph 会从该节点开头重跑。

## 状态与恢复规则

```text
pending --approve--> approved
pending --reject---> rejected
pending --到期/报告变化--> expired
```

`resume(decision, current_report)` 要求同一 `session_id`、交易 ID 和冻结报告哈希，明确的 `approve`/`reject`，并再次核对**当前报告**与冻结版本相同且仍可审批。错误会话、哈希、交易或非法决定返回结构化错误，检查点保持 `pending`。到期或报告变更会写入 `expired` 审计事件，之后不能改为批准。相同决定重复提交返回原结果，不新增审计事件；相反决定返回 `already_finalized`。修改报告后必须以新会话重新发起审批。

审批审计含顺序号、`created`/`approved`/`rejected`/`expired`、UTC 时间、审批 ID、报告哈希、复核员标识及过期原因。输出恒为 `external_action_count=0`；原 Stage 04/05 报告与审计均不被覆盖。`reviewer_id` 目前是调用方提供的演示标识，**没有身份认证**，不能把本地演示当作生产授权。路线 6 使用 `InMemorySaver`，只有共享该检查点对象的同一进程可恢复；进程重启后会话不保留。

Stage 06 在同一审批对象上增加可选备注：先去除首尾空白，最多 500 字符。备注存入独立审批决定和顺序审计；相同决定、复核员和规范化备注才算幂等重试，任何字段改变均是冲突。原冻结报告仍不改变。

## 复现

```powershell
uv run --offline pytest tests/unit/test_stage05_approval.py -q
uv run --offline python scripts/demo_stage05_approval.py --report-json reports/generated/stage05/route5-ollama-3488959.json --session-id my-demo-reject --decision reject --reviewer-id demo-reviewer
```

演示命令在**一次进程**中先暂停再恢复；`--decision` 是操作者显式提供的演示输入。生成文件存入 Git 忽略目录。若要逐步观察，在 Python 同一进程分别调用 `start` 和 `resume`；只运行 `start` 后退出进程无法再恢复。审批前仍需人工判断政策是否确实适用，审批结果不会把未校准模型分数变成欺诈事实。
