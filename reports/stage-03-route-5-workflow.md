# Stage 03 路线 5：确定性工作流、报告与审计验证

> 验证日期：2026-09-23  
> 状态：路线 5 已完成；Stage 03 仍需路线 6 总验收

## 工作流与停止规则

`src/graphrisk/investigation/workflow.py` 按固定顺序执行：

```text
get_transaction
  → get_transaction_features
  → get_risk_score
  → get_related_entities
  → find_risky_neighbors
```

输入无效或目标交易不存在时，流程在第一步停止，因为后续结果都没有有效调查对象。第一步发生数据依赖故障时同样停止并将报告标为 `failed`。交易事实成功后，特征、模型、关系工具即使有一个失败，其余工具仍继续执行；报告保留已经取得的证据并标为 `partial`。没有历史关系是 `ok` 的空结果，不是失败。Stage 03 的 `find_risky_neighbors` 返回预期的 `unavailable`，被单独放入 `unavailable_capabilities`，不使其他四个工具都成功的报告变成 `partial`。

工作流额外兜住工具边界外的未预期异常，将查询异常转换为 `query_failed`、模型异常转换为 `prediction_failed`，不把路径和堆栈写入报告或审计。它不自动重试、不调用 LLM，也不执行封禁等处置。

## 报告结构与确定性

`InvestigationReport` 包含交易概览、严格历史特征、表格风险分数、历史共享实体、图能力状态、结构化问题、限制及 `evidence_bindings`。每个数据区段都保留产生它的工具结果；binding 再明确记录区段、来源工具和交易/模型版本 ID。报告没有 investigation ID、执行时间或耗时。在数据、模型和输入相同的条件下，两次运行的报告 JSON 完全相同，固定期望样例为 `tests/fixtures/stage03_investigation_report.json`。

固定限制包括：模型分数不是校准概率；共享建模实体不证明真实身份或欺诈；图风险邻居要等 Stage 04；工作流只读。IEEE-CIS 报告还声明时间锚点是人工设定，只能解释相对顺序。报告不生成自由文本欺诈结论，也不包含 `is_fraud`、`identity_key` 或测试集指标。

报告状态：

| 状态 | 含义 |
|---|---|
| `completed` | 交易、特征、风险和关系均成功；已知图能力缺口单独记录 |
| `partial` | 交易存在，但至少一个后续工具失败；保留其他成功证据 |
| `not_found` | 已成功查询数据源，但目标交易不存在，流程停止 |
| `invalid_input` | 输入 schema 不合法，查询前停止 |
| `failed` | 无法取得基础交易事实，例如 canonical 产物不可用 |

## 审计边界

`InvestigationAudit` 与报告分开返回。每个实际调用记录 `investigation_id`、连续序号、工具名、带时区的开始/结束时间、单调时钟计算的耗时、状态和错误码。审计不记录交易 ID、实体 ID、模型输入、原始宽表、密钥、异常文本或报告内容。重复运行产生不同 trace ID 和时序信息，但不改变确定性报告。

## 命令行演示

入口：

```powershell
uv run --offline python scripts\run_investigation.py 2987100 `
  --data-config configs\ieee_cis.yaml `
  --training-config configs\train_baseline_ieee_cis.yaml `
  --relation-limit 3 `
  --report-output reports\generated\stage-03-ieee-investigation.json `
  --audit-output reports\generated\stage-03-ieee-audit.json
```

生成目录已被 Git 忽略。实际运行返回 `status=completed`：交易金额 100、产品 H；历史窗口计数为 0；`xgboost-v1` 分数 `0.4616164565086365`、等级 `medium`、未校准；历史共享实体总数 9，展示最近 3 条并标记截断；图风险邻居明确不可用。这些数值分别是数据事实、历史统计、模型输出和关系线索，不能组合成欺诈定论。

## 验证与学习笔记

`tests/integration/test_investigation_workflow.py` 覆盖健康运行、重复报告相等、审计分离与顺序、交易不存在、空白 ID、未知字段、关系上限无效、模型不可用时继续、未预期工具异常收敛。固定 fixture 同时检查结构化报告没有标签和内部 identity 字段，审计不含交易/实体值。

验证命令：`uv run --offline pytest tests/integration/test_investigation_workflow.py -q` 为 **7 passed**；全量 `uv run --offline pytest` 为 **105 passed**；`uv run --offline python -m compileall -q src api scripts tests`、`uv build --offline` 与 `git diff --check` 均通过。真实 CLI 命令成功生成分离的报告和审计 JSON。

有限状态工作流把“下一步做什么”写在代码里，因此不会由 LLM 临时决定是否跳过关键证据。交易不存在使所有后续工具失去对象，所以应停止；模型不可用不妨碍读取交易与关系，因此应形成带缺口的部分报告；无历史关系是经过成功查询得到的事实，应保留为成功空列表。幂等性针对调查内容，不要求 trace ID、时间戳或耗时相同。证据 binding 说明每个区段来自哪个工具和记录，使后续 Agent 只能在已有结构化事实之上组织语言。
