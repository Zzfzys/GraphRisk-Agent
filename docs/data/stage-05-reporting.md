# Stage 05 路线 5：派生报告与引用核验契约

> 2026-09-25。路线 5 只生成待人工复核的调查建议；路线 6 的同进程[审批中断](stage-05-approval.md)已另行实现。

## 输入与生成边界

`Stage05ReportWorkflow` 接受路线 4 的 `Stage05GraphRun`，不重新查询交易或修改 Stage 04 报告、独立审计。`ReportDraft` 是受限模型输出：请求交易 ID、数据源、必须原样回传的交易/模型/图证据 ID、恰好一个本次政策 `chunk_id`、不确定性枚举和建议动作枚举。交易金额、未校准风险分数、图状态、邻居 ID 和最终不确定性由代码从 Stage 04 类型化结果填入 `DerivedReport`，不让模型重写。模型返回的不确定性枚举只作非权威提示；即使它与调查状态不一致，也不得改变最终 `verified` / `partial_evidence` 分支。交易、来源、证据 ID、政策选择和建议动作仍须严格校验。

本地生成适配器先核对 Ollama `/api/tags` 的模型名称和完整 digest，再使用配置的 `/api/generate`、JSON schema、温度 0 和输出上限。fake provider 用于离线回归；`api` 提供商尚未接入。本地模型输出不直接作为最终报告。

## 代码核验

1. 交易与数据源必须和请求、原调查相同；模型回传证据 ID 必须恰好等于本次要求的 ID 集合。
2. 候选政策只来自本次检索；核对 `chunk_id`、正文 SHA-256、`policy_as_of` 半开生效区间、`doc_id`、版本、章节和来源。最终只复制政策片段**完整原文**，不接受模型改写的规则句。
3. 当前 Stage 04 没有独立可信设备批准记录、24 小时不同账户数或 10 分钟同卡尝试次数，所以排除可信设备例外及相应阈值触发条款。图线索条款只有图工具成功时可选；图能力缺口仍可引用图解释限制。
4. 无政策命中或所有片段的前提都无法核实时，不调用模型，报告写“无法依据现有政策判断”。模型超时、不完整 JSON、schema 失败、伪造 ID 或状态不符均不产生派生报告；错误结果只记录安全错误码。
5. `partial` 保持 `partial_evidence`，无命中保持 `insufficient_evidence`。`source_quote_verified` 只表示出处与逐字内容核验通过，**不表示政策适用性已被证明**。建议始终待人工复核，不执行封禁、拒付或通知。

派生审计单独记录 Stage 04 `investigation_id`、prompt 版本和哈希、模型 ID/digest、检索索引版本、耗时、Token、估算费用及派生报告哈希。本地模型费用为 0；未调用或统计不可用时为 `null`，不伪造为 0。运行 JSON 同时保存原 `graph_run` 和 `report_run`，且存入 Git 忽略的生成目录。

## 复现

```powershell
uv run --offline pytest tests/unit/test_stage05_reporting.py -q
uv run --offline python scripts/run_stage05_report.py 3488959 --policy-as-of 2026-07-01 --relation-limit 3 --provider fake
uv run --offline python scripts/run_stage05_report.py 3488959 --policy-as-of 2026-07-01 --relation-limit 3 --provider local_http
```

真实模型需要本机 Ollama 服务、已安装且与 `configs/stage05.yaml` 完整摘要一致的 `qwen2.5:0.5b`。政策原文虽逐字可查，具体交易是否满足某条规则的业务前提仍须人工复核；路线 7 再实现全程超时、重试和费用执行门禁。
