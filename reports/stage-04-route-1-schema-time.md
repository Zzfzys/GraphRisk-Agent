# Stage 04 路线 1：图 schema、特征与时间语义验证

> 日期：2026-09-23
> 结果：PASS，路线 1 小型正确性门禁完成；大规模图产物留给路线 2

## 已实现

- 五类节点（Transaction、Card、Device、EmailDomain、Address）和交易—实体直接边的稳定键、顺序与映射；
- 每笔目标交易单独截止时间：只纳入严格更早的共享实体交易；同时间和未来交易排除；
- 每个根实体的历史候选按 `event_time DESC, transaction_id ASC` 截断，多实体命中去重；
- 入选历史交易连接自身有效实体；`unknown-*` 占位实体不建节点或边；
- Stage 02 九列数值特征、缺失指示、训练 ID 拟合的填充/缩放、训练 ID 产品词表、五类节点类型 one-hot；
- 只从输入行投影允许字段；`is_fraud`、`identity_key` 和 split 不进入图、PyG `Data.x`、`edge_index` 或图内容哈希；
- 小图导出 PyG `Data`，显式双向 `edge_index`，根节点索引为 0，当前不绑定 `data.y`。

完整字段、时间规则与逐边解释见 [图 schema 文档](../docs/data/stage-04-graph-schema.md)。实现位于 [`schema.py`](../src/graphrisk/graph/schema.py)，fixture 位于 [`stage04_temporal_graph.json`](../tests/fixtures/stage04_temporal_graph.json)。

## 手算 fixture

十笔匿名合成交易中，目标 `T3` 位于第 10 分钟。每根实体最多选择两笔历史交易，最终选中 `T2`（第 8 分钟）、`T8`（第 6 分钟）、`T1`（第 5 分钟）。`T8` 同时共享 card、device 和 email domain，但只成为一个交易节点。更老的 `T9/T0` 超过 card 的最近两笔上限；`T4` 与目标同时间；`T5` 是未来交易；`T6/T7` 只持有 `unknown-*` 占位值。

固定结果：

| 项目 | 值 |
|---|---|
| 节点 | 13 |
| 交易—实体逻辑边 | 16 |
| PyG 双向消息边 | 32 |
| 历史交易顺序 | `T2,T8,T1` |
| 内容 SHA-256 | `ac0bebe1ae3234afedf03898360f7080416ce4842b445f47c4425c57f0e82955` |

测试直接断言全部 13 个节点顺序和 16 条逻辑边；这一固定期望不是从实现输出动态生成。逆转输入行顺序后，节点映射、边和哈希不变；更改全部标签与 `identity_key`、添加未来交易后，目标图哈希不变。

## 自动验收

```powershell
uv run --offline pytest --basetemp=reports\generated\stage04-route1-focused tests/unit/test_temporal_graph_schema.py -q
uv run --offline pytest --basetemp=reports\generated\stage04-route1-all -q
uv run --offline python -m compileall -q src\graphrisk\graph tests\unit\test_temporal_graph_schema.py
uv build --offline
```

| 检查 | 结果 |
|---|---|
| 图 schema 与时间边界聚焦测试 | 10 passed in 8.60s |
| 全仓库测试 | 137 passed in 17.72s |
| compileall | PASS |
| wheel/sdist 构建 | PASS |
| 真实 IEEE-CIS 单行特征契约抽查 | PASS，九列数值接受；该行有两类有效实体 |

时间测试覆盖未来关系、同时间交易、占位实体、每实体上限、同时间历史候选的 ID 破同分、输入乱序、多实体命中去重、训练目标看不到较晚 validation/test 交易。未来行增加或标签变化后，PyG 节点特征与边索引保持一致。特征测试覆盖训练 ID 拟合、validation 极端金额不改变 encoder、未见产品编码、实体 ID 不进入向量、标签不进入 PyG。

## 学习复盘

以 `T3` 为目标时，`T2/T8/T1` 是更早且有真实共享实体的交易；`T4` 虽然共享 card，但与目标同时间，因此不能称为历史；`T5` 即使共享 card，也不能影响 `T3` 的节点、边或图哈希。图边表达的是“共享建模实体”，不是共同真实身份，更不是欺诈结论。GraphSAGE 的输入可以用金额、当前产品和严格历史统计，但 `is_fraud` 只能在训练 loss/离线评估中单独使用。

本路线提供的是**正确性参考构建器**，会扫描内存中的小型集合。路线 2 还需实现列投影、时序倒排索引、受控规模构建、输入/ID 哈希与持久化 manifest。路线 3 才能据此训练和评估 GraphSAGE；目前 `find_risky_neighbors` 仍保持 Stage 03 的 `unavailable` 状态。
