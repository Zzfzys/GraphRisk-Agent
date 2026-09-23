# Stage 04 可复现图产物契约

> 状态：路线 2 已实现并在 IEEE-CIS 50k/10k/10k 目标集合上验证。
> 实现：[`build_artifacts.py`](../../src/graphrisk/graph/build_artifacts.py)、[`temporal_index.py`](../../src/graphrisk/graph/temporal_index.py)。
> 前置规则：[图 schema 与时间语义](stage-04-graph-schema.md)。

## 1. 输入与选取

运行 [`scripts/build_graph_artifacts.py`](../../scripts/build_graph_artifacts.py) 时读取 `configs/graph_ieee_cis.yaml`，先验证 Stage 01 canonical 交易、point-in-time 特征、三个时间分割文件及 Stage 02 champion 产物存在。canonical 与全量特征的八列交易基础字段必须一致；三个分割必须按时间排列，唯一交易 ID、分割标记和图允许字段必须与全量特征完全对应。损坏或交叉来源输入直接失败。

每个分割按 `(transaction_time ASC, transaction_id ASC)` 取配置中的前缀目标 ID。当前受控配置选 50,000 个 train、10,000 个 validation、10,000 个 test 目标。图索引包含从数据开始到最后一个已选目标时间之间的**全部**可见交易，而不只包含所选目标；验证/测试目标因此可以看到其之前发生、但并未选为模型目标的交易。这些交易的标签不会进入图输入。截止时间之后的交易不会写入投影或索引。

Stage 01 合成源把 UTC 时间存为无时区 Parquet 时间列，IEEE-CIS 源使用带时区列。图产物在 DuckDB 中显式设置 UTC 并统一转换为 `TIMESTAMPTZ`，再进入严格小于根交易时间的索引规则。同时间交易仍不能互为历史邻居。

## 2. 采样与索引

`TemporalEntityIndex` 按 `(event_time, transaction_id)` 稳定排序，为四类有效实体建立交易倒排表。查询时用二分查找定位目标时间之前的范围；同一历史时间的交易按 ID 升序破同分。它与路线 1 的扫描式参考构建器对十笔 fixture 的**每个目标**、多个上限值逐一比较节点、边和内容哈希。

当前配置 `fanout: [10, 5]` 的含义是：第一个值至少覆盖根交易最多四类实体，首版不丢弃根实体；第二个值表示**每个根实体最多选 5 笔严格更早的交易**。历史交易本身的有效实体边保留，但不沿这些额外实体继续找交易。多实体命中的历史交易去重。局部图节点与边顺序沿用路线 1；PyG 消息边由逻辑边双向展开。`unknown-*` 不建立实体节点或边。

这是一种确定性的两跳局部图采样规则，后续训练、评估和在线查询必须复用相同 manifest 和索引。全局边表只保存事件发生时的交易—实体事实；**不能**把全局表直接当成任意早期目标的消息传递图。每次 `ego_graph` 查询都重新按根交易时间过滤。

## 3. 输出及完整性

所有文件写入被 Git 忽略的 `artifacts/stage-04/<source>/`。当前输出：

| 文件 | 内容 |
|---|---|
| `projected-transactions.parquet` | 截止时间内允许的交易 ID、时间、实体、产品和九列数值特征；无标签、`identity_key`、split |
| `entity-edges.parquet` | 带交易事件时间的交易—实体事实表；排除占位实体 |
| `node-mapping.parquet` | 全局稳定节点类型、原始键、索引和交易时间；用于追溯/审计，不作为早期目标的静态输入图 |
| `train/validation/test-ids.json` | 各分割按时间、ID 排序的受控目标清单 |
| `encoder.json` | 仅在选中训练目标上拟合的填充、缩放、产品词表和特征名 |
| `ego-sample.json` | 两个可重建 PyG `Data`/`Batch` 的小样例，无标签 |
| `graph-manifest.json` | 数据源、版本、时间边界、fanout、计数、统计、软件版本及输入/输出 SHA-256 |

manifest 的 `content_sha256` 是除该字段自身外其余字段的规范 JSON 哈希；不记录运行时间或生成时间，因此同输入和配置的重复构建应逐字节一致。`load_graph_artifacts()` 会核对 manifest、所有源文件和所有输出文件的哈希，并拒绝错误数据源、损坏产物或过期源文件。运行日志只输出聚合计数、内容哈希、耗时和 manifest 路径，不输出实体原始值。

## 4. 运行与核查

```powershell
uv run --offline python scripts\build_graph_artifacts.py --config configs\graph_ieee_cis.yaml
uv run --offline pytest tests\unit\test_temporal_entity_index.py tests\integration\test_graph_artifacts.py -q
```

先确保 Stage 01/02 上游产物齐备。构建成功后，检查 `graph-manifest.json` 中 `indexed_transaction_count`、三个 `selected_target_counts`、`target_time_bounds`、`ego_statistics` 和 `content_sha256`。再次运行同一命令，内容哈希及 manifest 字节应不变。真实数据的当前验收数据见[路线 2 验证记录](../../reports/stage-04-route-2-artifacts.md)。

路线 2 输出的是可复用、可核验的图输入。路线 3 已在这些目标 ID 上训练 GraphSAGE，详见[训练契约](stage-04-graphsage-training.md)；逐交易分数表和风险邻居模型证据仍待路线 4/5。不能把当前全局边表当作无时间截止的训练图。
