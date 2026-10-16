# Stage 05 路线 4 验收：有界 LangGraph 骨架

> 执行日期：2026-09-25；路线状态：**Done**。Stage 05 整体仍为 `In Progress`；真实报告生成、语义引用核验和人工审批仍按路线 5、6 实施。

## 已交付

- [工作流契约](../docs/data/stage-05-graph-workflow.md)定义 6 个无回边节点、显式条件边、结构化终态、步数/调用上限和 `not_requested` 审批状态。Pydantic 计划只允许 `retrieve_policy`，且交易 ID、数据源、政策日期和 `top_k` 必须与已验证请求一致。
- `Stage05GraphWorkflow` 直接调用原有 `InvestigationWorkflow.run`；没有复制 SQL、特征、模型或图查询。Stage 04 原报告与独立审计留在可序列化输出中。交易不存在即终止；图证据失败仍可进入检索，但终态保持 `partial_evidence`。
- `FakeDraftProvider` 只产生本次片段 ID 的占位草稿；`verify_stub` 拒绝虚构/重复引用及有命中却无引用，明确保留 `semantic_validation_pending=true`。它没有生成可用政策结论，也没有批准或执行处置。

## 验证证据

14 项路线 4 聚焦测试覆盖正常固定节点顺序、重复运行一致、交易不存在、图能力部分失败、无政策命中、未知工具、跨数据源/日期/交易参数、`top_k` 越界、步骤上限、伪造或缺失引用和检索错误。测试使用 Stage 04 固定报告 fixture 与本地政策索引，无网络和 LLM 费用。

对现有 IEEE-CIS held-out 交易 `3488959` 执行：

```powershell
uv run --offline python scripts/run_stage05_graph.py 3488959 --policy-as-of 2026-07-01 --relation-limit 3 --config configs/stage05.yaml --output reports/generated/stage05/route4-3488959.json
```

本机结果为 `partial_evidence`、6 个节点、1 次政策检索、1 次 fake 草稿调用；Stage 04 报告为 `partial`，有 5 个独立审计事件，`find_risky_neighbors` 的错误码为 `graph_artifact_unavailable`，政策检索为 `ok` 并返回 3 个有效片段。输出保存在 Git 忽略目录；图产物当前不可用的原因需在 Stage 04 配置/产物层排查，本路线正确地保留了部分失败，没有把政策片段当作完整图证据。

全量 `uv run --offline pytest -q` **223 passed**；`uv lock --check --offline`、`uv build --offline` 与 `git diff --check` 通过。用户已有的图训练配置和 smoke 测试未提交修改保持原状。默认词法检索可离线运行；本路线没有对真实生成模型质量或审批安全性作出验收。

下一步路线 5 将在这套状态和引用集合上接入真实模型草稿、报告 schema、逐条证据绑定和语义复核；路线 6 再增加人工审批中断。
