# Stage 04 路线 5：图服务与风险邻居验收

> 日期：2026-09-23。状态：PASS；路线 5 完成，Stage 04 整体仍在进行。
> 图 manifest SHA-256：`8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80`；GraphSAGE 版本：`graphsage-v1`；分数来源：路线 4 `same-id-v4` 验证/测试产物。

`GraphRiskService` 启动时验证并缓存图 manifest、来源文件、GraphSAGE checkpoint 及 held-out 分数表。直接 `predict("3489059")` 在严格历史 ego graph 上复算得到未校准分数 `0.408483`，与版本化离线预测一致。服务只在进程启动加载一次；首次独立 CLI 查询约 22 秒包含全图哈希检查与索引构建，不能把它当作单次热查询延迟。

真实 IEEE-CIS 固定查询：

```powershell
uv run --offline python scripts\query_graph_neighbors.py 3489059 --config configs\graph_ieee_cis.yaml --comparison-run same-id-v4 --limit 5
```

返回 `ok`、5 个历史邻居，按图分数从高到低排序；首邻居交易 ID `3489035`，分数 `0.543260`，早于目标 2080 秒，共享 card 与 address。证据包含 `graphsage-v1` 模型引用与每个邻居的实体关系引用；`score_scope=held_out`、`probability_calibrated=false`，不包含标签或 `identity_key`。相同数据源与模型版本下重复查询业务内容稳定。

同进程测试另外两个固定目标：`3488959` 返回 `ok` 与空列表；训练目标 `2987000` 返回 `unavailable/graph_prediction_unavailable`，因为训练 in-sample 分数不允许作为独立邻居证据。伪造目标返回 `not_found/transaction_not_found`。小型时序测试覆盖高分排序、时间差与 ID 破同分、limit、未来/同时间/`unknown-*`/无分数候选排除、多实体命中去重并保留共享路径。集成测试覆盖缺模型、版本错配、来源错配和损坏预测表，均为标准结构化状态且不泄漏内部路径。

全仓库 `uv run --offline pytest -q`：**161 passed**。图服务契约与适用范围见[服务说明](../docs/data/stage-04-graph-service.md)。路线 6 再将真实服务注入 Stage 03 调查工作流；本路线独立工具已完成，但现有工作流仍沿用未注入服务时的兼容占位状态。
