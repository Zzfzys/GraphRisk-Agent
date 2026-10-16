# Stage 04 路线 5：图模型服务与风险邻居契约

`GraphRiskService` 在进程启动时一次性校验并加载图 manifest、来源/输出哈希、特征 schema、GraphSAGE checkpoint，以及路线 4 的验证/测试预测表。后续 `predict(transaction_id)` 和 `find_neighbors(transaction_id, limit=...)` 复用内存中的索引、模型和分数，不在每次工具调用重新读取文件。当前服务只覆盖路线 4 的 **10,000 个验证目标和 10,000 个测试目标**；训练目标和未选为目标的交易没有合格的 held-out 分数。

图风险预测入口对已覆盖的目标重建严格历史 ego graph、运行冻结的 GraphSAGE，并与经过哈希校验的离线分数核对。路线 6 还校验同 ID 受控 XGBoost 模型哈希、版本、融合权重的验证集选择来源和融合公式；目标预测同时返回受控 XGBoost 与融合分数，Stage 02 全量模型不参加该融合。分数是未校准的排序分数，不是欺诈概率。风险邻居工具只从同一 ego graph 选取严格早于目标的交易，以共享的有效 card/device/email_domain/address 实体建立路径。它只使用经校验的验证/测试 GraphSAGE 分数；训练交易的 in-sample 分数、无分数候选、同时间或未来交易、`unknown-*` 占位关系均不得成为风险证据。多实体命中合并为一笔交易，但 `shared_entities` 保留全部共享路径。

返回结果按 `graph_risk_score DESC`、`seconds_before_target ASC`、`transaction_id ASC` 排序，再应用 `limit`（1～50）。`data.graph_model_version`、`score_scope=held_out` 和 `probability_calibrated=false` 明确分数来源；每个邻居有时间差和共享实体。`evidence` 含图模型、受控 XGBoost 版本引用及每个邻居的实体关系引用，不含 `is_fraud`、`identity_key` 或宽表。没有合格邻居返回空列表和 `ok`，但仍返回目标的模型分数。

结构化失败：输入非法为 `invalid_input`；目标不存在为 `transaction_not_found`；目标在图分数覆盖范围外为 `graph_prediction_unavailable`；数据源/仓库绑定不一致为 `data_source_mismatch`；模型版本不一致为 `graph_model_version_mismatch`；模型、manifest、预测表缺失或哈希损坏为 `graph_artifact_unavailable`。未注入图服务时保留 Stage 03 的 `capability_unavailable`；路线 6 的 IEEE-CIS CLI 已默认注入图服务，工作流将该缺口呈现为 `partial`。

独立查看真实 IEEE-CIS 结果：

```powershell
uv run --offline python scripts\query_graph_neighbors.py 3489059 --config configs\graph_ieee_cis.yaml --comparison-run stage05-route8 --limit 5
```

`3489059` 是固定的测试目标示例；相同产物与请求得到相同业务内容。首次命令会校验文件并构建内存索引，因此冷启动比后续同进程调用慢。路线 5 的[验收记录](../../reports/stage-04-route-5-graph-service.md)说明成功、空结果与失败案例。
