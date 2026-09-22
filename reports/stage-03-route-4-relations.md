# Stage 03 路线 4：本地关系证据与图工具占位验证

> 验证日期：2026-09-23  
> 状态：路线 4 已完成；确定性工作流与全阶段验收仍待路线 5～6

## 实现内容

`get_related_entities` 使用本地 canonical Parquet 和内存 DuckDB。先按交易 ID 唯一定位目标，并从目标读取四类建模实体和 `transaction_time`；随后只查询 `transaction_time` **严格小于**目标的交易。查询条件里的交易 ID、时间和实体值都使用参数绑定，列名只来自内部固定清单。目标为 `unknown-*` 的占位实体不参与匹配，避免把缺失值连成虚假关系。

同一历史交易即使共享多类实体，也只生成一条 `RelatedTransaction`，其 `shared_entities` 列出所有命中的类型和 ID。结果按交易时间降序、交易 ID 升序稳定排序，相当于最近历史优先；`limit` 默认 20、范围 1～50。另返回未截断的 `total_matches` 与 `truncated`，不会把上限内的几笔当作全部关联。`seconds_before_target` 为正整数；遇到不足一秒的真实差值时向上取整，避免把严格更早的记录写成 0 秒。

`find_risky_neighbors` 只验证输入与数据源，然后返回 `unavailable / capability_unavailable`，不输出邻居、GNN 分数或证据。Stage 04 有图模型后才能实现它。共享 card、device、email 或 address 只是**关系证据**，不证明同一真实用户，也不证明相邻交易是欺诈。

调用示例：

```python
from graphrisk.config import load_config
from graphrisk.investigation.repository import InvestigationRepository
from graphrisk.investigation.relation_tools import get_related_entities, find_risky_neighbors

repository = InvestigationRepository.from_config(load_config("configs/ieee_cis.yaml"))
request = {"transaction_id": "2987100", "dataset_source": "ieee_cis", "limit": 3}
relations = get_related_entities(request, repository)
graph_neighbors = find_risky_neighbors(request, repository)
print(relations.model_dump_json())
print(graph_neighbors.model_dump_json())
```

## 错误语义

| 情形 | 结果 |
|---|---|
| 目标交易不存在 | `not_found / transaction_not_found` |
| 没有合格历史关系 | `ok`，`matches=[]`、`total_matches=0`、无证据 |
| ID/上限无效 | `invalid_input / invalid_transaction_id` 或 `invalid_limit` |
| 请求数据源与 repository 不同 | `dependency_error / data_source_mismatch` |
| canonical 文件缺失 | `dependency_error / transaction_artifact_unavailable` |
| Parquet 损坏或时间/实体字段不符合契约 | `dependency_error / query_failed` |
| 目标或符合条件的历史交易 ID 重复 | `dependency_error / duplicate_transaction` |
| 图风险邻居能力未实现 | `unavailable / capability_unavailable` |

所有错误结果都没有数据或证据，也不回传本地文件路径和底层异常。查询只投影交易 ID、时间和四类建模实体，`is_fraud` 与 `identity_key` 不参与过滤或输出。IEEE-CIS 的时间来自人工锚点，仅用于相对顺序和间隔，绝不当作真实日历时间展示。

## 验证证据

- `uv run --offline pytest tests/integration/test_relation_tools.py -q`：**13 passed**。fixture 覆盖 card、device、email、address 四类共享实体、多类同时命中去重、相同时刻与未来隔离、`unknown-*` 排除、稳定排序、limit/总数/截断、无关系空成功、重复 ID、缺失/损坏文件、数据源与输入错误、图占位不可用。
- 真实 IEEE-CIS 交易 `2987000`：`ok`，严格历史关联数为 0，空列表无证据。交易 `2987100`：`ok`，总数 9；`limit=3` 时返回 `2987095`（相差 49 秒，共享 address）、`2987080`（313 秒，共享 email domain）、`2987069`（444 秒，共享 address），`truncated=true`。这些实体是建模线索，不应推断为真实身份或欺诈事实。
- `uv run --offline pytest`：**98 passed**；`uv run --offline python -m compileall -q src api scripts tests` 和 `git diff --check` 均通过。

## 学习笔记

把交易当作节点、共享 card/device/email/address 当作一跳连接时，同一对交易可以同时有多种连接依据，但最终仍是同一笔历史交易。时间过滤用严格小于，因此发生在目标交易同一时刻或之后的记录不能被称为“已经发生的历史”。`unknown-*` 是源数据缺失时的占位符，不是可共享的业务实体；若以它连边会制造大量假关系。`total_matches=9` 与实际返回 3 条分别表示完整匹配数量和展示数量。风险邻居必须有图模型分数才能称为“高风险”，目前的共享实体关系不能替代该结论。
