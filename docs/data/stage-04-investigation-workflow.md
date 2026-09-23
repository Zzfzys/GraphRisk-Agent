# Stage 04 路线 6：确定性调查工作流契约

`InvestigationWorkflow` 继续按固定顺序调用 `get_transaction`、`get_transaction_features`、`get_risk_score`、`get_related_entities`、`find_risky_neighbors`。首个工具找不到交易时立即停止；之后任一工具失败不会覆盖已成功的证据。`scripts/run_investigation.py` 对 IEEE-CIS 默认注入 `configs/graph_ieee_cis.yaml` 的 `GraphRiskService` 和 `same-id-v4` 对比产物；服务在运行调查前只加载一次。可用 `--graph-config` 和 `--comparison-run` 指定其它版本化产物。

## 报告中的分数与证据

`risk_score` 是 Stage 02 服务模型 `xgboost-v1` 的结果。`risky_neighbors.data` 属于路线 4 的同 ID 受控比较：`target_graph_risk_score` 来自冻结 GraphSAGE 的重新推理，`controlled_xgboost_score` 来自相同训练目标 ID 的受控模型，`fusion_score = alpha × controlled_xgboost_score + (1 − alpha) × target_graph_risk_score`，其中 alpha 只根据验证集 PR-AUC 选择。图服务校验模型和预测产物哈希、目标 ID 顺序、版本、分数范围及融合公式；仅对验证/测试中有 held-out 分数的目标返回这些字段。Stage 02 分数不参加融合，不能把两个 XGBoost 分数当作同一个模型的预测。

`related_entities` 是直接共享实体的历史查询；`risky_neighbors` 是严格历史 ego graph 中、另有 held-out 图分数的交易子集。两节各有自己的 `evidence_bindings`。风险邻居保留交易 ID、图分数、距目标的秒数及共享实体路径；空列表表示成功查询后没有合格邻居，仍可提供目标分数和模型版本。共享匿名实体不证明真实身份，模型分数未校准，也不证明邻居或目标实际欺诈。报告不读取或输出测试标签。

## 状态与失败恢复

| 场景 | 图工具 | 调查状态 | 保留内容 |
|---|---|---|---|
| 合格目标，有或没有合格邻居 | `ok` | `completed`，若其它工具失败则 `partial` | 图分数、路径、模型证据及其它成功节 |
| 没有注入图服务 | `unavailable/capability_unavailable` | `partial` | 交易、历史特征、Stage 02 分数和直接关系 |
| 目标没有 held-out 图分数 | `unavailable/graph_prediction_unavailable` | `partial` | 同上；不生成 in-sample 图分数 |
| 图产物损坏或来源不匹配 | `dependency_error` | `partial` | 同上；错误码和失败工具写入 issues |
| 交易不存在 | `not_found/transaction_not_found` | `not_found` | 仅首个工具结果 |

报告是确定性业务内容，不包含调查 ID、耗时或时间戳。独立审计按调用顺序保存调查 ID、工具名、时间、耗时、状态、错误码和成功结果的模型版本；不保存标签、完整实体表、交易行或私有文件路径。相同输入和产物应得到相同报告字节，但审计 ID 和时间应随运行变化。

## 自查

先运行 `uv run --offline pytest tests/integration/test_investigation_workflow.py tests/integration/test_relation_tools.py -q`。再运行 README 中的真实调查命令，读取 `reports/generated/stage04-report.json` 的 `status`、`risk_score`、`related_entities`、`risky_neighbors`、`evidence_bindings` 和 `limitations`；只从 `stage04-audit.json` 查看审计字段。路线 6 的[验证记录](../../reports/stage-04-route-6-workflow.md)给出固定案例和哈希。
