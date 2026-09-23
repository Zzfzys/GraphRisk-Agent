# Stage 04 路线 6 验证记录：确定性工作流接入与回归

日期：2026-09-23。阶段状态仍为 In Progress；本记录只验收路线 6，路线 7 的阶段总验收与复盘另行执行。

## 实现与学习目标

既有五工具顺序保持不变。IEEE-CIS CLI 将经过启动校验的 `GraphRiskService` 注入工作流；报告把直接共享关系与图风险邻居作为两节独立证据。图节增加目标 GraphSAGE 分数、路线 4 同 ID 受控 XGBoost 分数、验证集选择的 alpha 和融合分数，并绑定两种模型版本及邻居 ID。Stage 02 全量训练的 `xgboost-v1` 仍在原 `risk_score` 节，其分数不参与受控融合。审计记录工具元数据、状态、模型版本和耗时，不保存实体内容。详细字段、失败分支和学习自查见[工作流契约](../docs/data/stage-04-investigation-workflow.md)。

学习时对照 `tests/integration/test_investigation_workflow.py` 画出三条分支：交易不存在时首个工具停止；图产物不可用时其它四节保留且报告为 `partial`；图查询成功但没有合格历史邻居时仍为 `completed`，并保留目标图分数。未覆盖训练目标返回 `graph_prediction_unavailable`，不把训练集内分数当作 held-out 证据。

## 自动化与固定报告

- 固定成功报告：`tests/fixtures/stage04_investigation_report.json`；同一请求运行两次，报告对象和序列化 SHA-256 一致，审计调查 ID 不同。
- 成功、空邻居、未覆盖目标、未注入服务、损坏产物、数据源错配、非法输入、其它工具故障及异常隔离均有工作流回归。路线 5 的严格历史、排序、产物损坏等图服务测试继续运行。
- 聚焦命令 `uv run --offline pytest tests/integration/test_investigation_workflow.py tests/integration/test_relation_tools.py tests/unit/test_graph_service_neighbors.py tests/unit/test_investigation_contracts.py -q`：52 passed。
- 全量命令 `uv run --offline pytest -q`：166 passed；`uv build --offline` 成功生成 sdist 与 wheel；`git diff --check` 通过。

## 真实 IEEE-CIS 调查

固定示例 ID `3489059` 沿用路线 5，不按测试标签筛选。两次执行 README 中的五工具 CLI，输出到忽略的 `reports/generated/stage04-route6-report-{a,b}.json` 和对应审计文件。两次状态均为 `completed`，五个工具均为 `ok`，无 issue 和 capability gap；图节返回 3 个历史邻居。目标 GraphSAGE 分数约 0.408483，受控 XGBoost 约 0.179653，融合分数约 0.294068，alpha 为 0.5。审计中的模型版本为 Stage 02 `xgboost-v1` 和图 `graphsage-v1`，没有实体明细。

两份报告 SHA-256 均为 `61d32afcdccc1500d2dbb6c498365bb76d2b1e7f3916901c5287dd8164663ac9`，字节相同。审计字节不同，调查 ID 和首事件时间不同。生成文件未加入 Git。

这些是未校准的风险排序分数，不能写作欺诈概率或已确认欺诈。该受控融合在路线 4 测试 PR-AUC 上未超过受控 XGBoost，故报告只展示版本化分数和局部关系证据，不声称融合有已验证的性能优势。

## 待路线 7

路线 7 仍需汇总图规模、时间/资源、同 ID 三模型指标、真实成功及失败案例、全量回归、构建与受限文件检查，并填写 Stage 04 总复盘；本路线完成不等于 Stage 04 已完成。
