# Stage 04 图 schema 与严格历史语义

> 状态：路线 1 的小型正确性契约已实现；路线 2 才负责大规模索引与持久化图产物。
> 数据源：Stage 01 canonical transaction 和 point-in-time features。
> 实现：[`src/graphrisk/graph/schema.py`](../../src/graphrisk/graph/schema.py)。

## 1. 节点与边

图包含五种节点：

| 节点类型 | 稳定键 | 含义 |
|---|---|---|
| `transaction` | `(transaction, transaction_id)` | 一笔交易；根节点是当前要预测的交易 |
| `card` | `(card, card_id)` | card-like 建模实体 |
| `device` | `(device, device_id)` | device-like 建模实体 |
| `email_domain` | `(email_domain, email_domain)` | 邮箱域名建模实体 |
| `address` | `(address, address_id)` | address-like 建模实体 |

逻辑边 `transaction → entity` 只表示交易记录使用了该实体。边不表示真实用户身份、实体所有权或已确认欺诈。PyG `edge_index` 为消息传递显式加入反向边，因此每条逻辑边导出为两条有向边。原始实体 ID 只用于拓扑映射和证据定位，不作为可学习 one-hot 特征。

`unknown-*`（不区分大小写）是未识别/交易唯一占位值；这种值不建立实体节点和边，不能让无关交易因占位值相同而被连接。其它非空实体值按原值精确匹配。Stage 01 IEEE-CIS 的实体映射是启发式建模值，不能称为经核验的客户或设备身份。

## 2. 逐目标时间截止

目标交易 `q` 的时间为 `t(q)`。局部图只纳入满足 `t(h) < t(q)` 的历史交易 `h`。目标自己是根节点，允许使用本次交易的金额、产品和已在事件时可见的 point-in-time 特征；同时间交易即使 ID 更小，也不视为历史。UTC 时间用于比较，IEEE-CIS 的日历锚点是人工设定，只能解释相对时间。

构建顺序：

1. 根节点 `q` 连接自己的四类有效实体；
2. 对每个根实体，找严格早于 `q` 且共享该实体的交易；
3. 候选按事件时间降序、交易 ID 升序排序，取前 `max_history_per_entity` 笔；
4. 四类实体的入选交易取并集，同一交易即使匹配多类实体也只出现一个交易节点；
5. 每笔入选历史交易连接自身所有有效实体，形成可解释的局部路径；这些额外实体不会再扩张出其它历史交易；
6. 图节点顺序固定为根、按上述排序的历史交易、按类型顺序和实体 ID 排序的实体；边按两个节点索引排序；
7. 图内容哈希覆盖目标、节点允许字段及逻辑边，不读取标签、身份启发式键或输入行顺序。

上限限制的是**每个根实体选入的历史交易数**。入选交易可能因为另一条路径也连接某个根实体，因此最终该实体的边数可以高于该上限；整体交易节点仍由四个实体的有界候选并集控制。Stage 04 配置中的 `fanout: [10, 5]` 在路线 2 映射为图采样规则；本路线的参考构建器直接接受 `max_history_per_entity`，测试用 `2` 以便手算。

根交易 `q` 的两层 GraphSAGE 可以沿 `q → entity → historical transaction` 接收两跳历史上下文。历史交易到其它实体的边保留局部图关系，但两层模型的根表示不会再从这些三级实体获得消息。路线 2/3 在确定采样器后需保证训练和推理使用一致的边方向、上限与根索引。

## 3. 输入特征与标签边界

`GraphTransaction.from_feature_row` 从 Stage 01 特征行投影允许字段：

| 类别 | 字段 | 用途 |
|---|---|---|
| 时间/主键 | `transaction_id`、`transaction_time` | 唯一节点与严格时间截止；不进入可学习数值向量 |
| 关系实体 | `card_id`、`device_id`、`email_domain`、`address_id` | 只用于拓扑 |
| 数值 | Stage 02 的 `NUMERIC_FEATURES` 九列 | 交易节点数值特征 |
| 类别 | `product_code` | 训练集词表的 one-hot 编码 |

九列数值为 `transaction_amount`、`tx_count_last_1h`、`tx_count_last_24h`、`tx_count_last_7d`、`amount_mean_last_24h`、`amount_std_last_24h`、`amount_deviation_last_24h`、`transaction_hour`、`transaction_day_of_week`。缺失值保留为 `None`，转换时使用**仅在训练交易上拟合**的列中位数填充，同时增加九个缺失指示；训练集拟合均值和标准差用于数值缩放，零标准差按 1 处理。产品词表也只由训练 ID 决定，未见过的产品编码为全零。最后加入五维节点类型 one-hot。实体节点除类型位外全部为零，因此不同 card/device/email/address ID 不会被模型作为直接特征记忆。

明确禁止进入图特征、边和图内容哈希的字段包括 `is_fraud`、`identity_key`、split 标记、模型预测分数、由未来事件计算的聚合值及原始 IEEE-CIS 宽表中未列入允许清单的字段。`is_fraud` 只在后续训练 loss 和离线评估中单独读取。当前参考类的 `to_pyg` 不设置 `data.y`，路线 3 才在训练批次外部绑定目标标签。

输入必须具备唯一交易 ID、带时区的交易时间、非空实体/产品值和九列数值字段。金额、计数及时间字段不能为空；允许的历史统计可为 `None`。非数值字符串、非有限数值、负金额和重复交易 ID 直接报错。

## 4. 可手算 fixture

fixture 为 [`tests/fixtures/stage04_temporal_graph.json`](../../tests/fixtures/stage04_temporal_graph.json)，共十笔匿名合成交易。以 `T3`（第 10 分钟）为目标、每根实体最多选两笔历史交易：

| 候选 | 分钟 | 与 T3 的匹配 | 结果 |
|---|---:|---|---|
| `T2` | 8 | device、email_domain | 入选 |
| `T8` | 6 | card、device、email_domain | 入选一次 |
| `T1` | 5 | card、email_domain | 入选 |
| `T9`、`T0` | 2、0 | card | 超出 card 的最近两笔上限 |
| `T4` | 10 | card | 与目标同时间，排除 |
| `T5` | 12 | card | 未来交易，排除 |
| `T6`、`T7` | 3、4 | 仅 `unknown-*` 占位值 | 无有效共享实体 |

节点 0～3 为 `T3,T2,T8,T1`。实体节点顺序为 `card:C1,C2`、`device:D1,D2`、`email_domain:e1.example`、`address:A1,A2,A3,A8`。逻辑边可逐笔核对：

```text
T3 → C1, D2, e1.example, A3
T2 → C2, D2, e1.example, A2
T8 → C1, D2, e1.example, A8
T1 → C1, D1, e1.example, A1
```

共 13 个节点、16 条逻辑边；导出的 PyG `edge_index` 有 32 条双向消息边。此 fixture 的内容 SHA-256 为 `ac0bebe1ae3234afedf03898360f7080416ce4842b445f47c4425c57f0e82955`。更改标签、启发式身份键、输入行顺序或增加未来交易后，该哈希不变。

## 5. 路线 2 输入与未完成项

当前构建器扫描内存中的小集合，用来证明语义和生成固定期望值。路线 2 应实现列投影和时序实体倒排索引，处理 50k/100k 受控规模，记录输入文件与 ID 清单哈希，并把训练拟合的 `GraphFeatureEncoder` 连同图 schema、采样参数写入 manifest。路线 3 才训练 GraphSAGE，路线 5 才让风险邻居工具返回真实模型证据。
