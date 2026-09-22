# Stage 03 路线 2：交易与历史特征工具验证

> 验证日期：2026-09-22  
> 状态：路线 2 已完成；Stage 03 的模型、关系、工作流路线仍未完成

## 实现内容与调用方式

`src/graphrisk/investigation/repository.py` 用显式 `ProjectConfig` 绑定数据源与 canonical/feature Parquet 路径。仓库用内存 DuckDB 连接执行只读 `SELECT`，固定列投影、参数绑定 `transaction_id`、最多读取两行来判断唯一性。工具函数 `get_transaction` 和 `get_transaction_features` 位于 `src/graphrisk/investigation/transaction_tools.py`；入参、结果和错误均采用路线 1 的 Pydantic 契约。

最小调用示例：

```python
from graphrisk.config import load_config
from graphrisk.investigation.repository import InvestigationRepository
from graphrisk.investigation.transaction_tools import get_transaction, get_transaction_features

repository = InvestigationRepository.from_config(load_config("configs/ieee_cis.yaml"))
request = {"transaction_id": "2987000", "dataset_source": "ieee_cis"}
transaction = get_transaction(request, repository)
features = get_transaction_features(request, repository)
print(transaction.model_dump_json())
print(features.model_dump_json())
```

只读取交易允许字段 `transaction_id`、金额、产品、四类建模实体；IEEE-CIS 额外读取原始 `TransactionDT` 相对秒数，合成数据返回 `null`。特征只读取三个严格历史计数及 24 小时金额均值、标准差、偏差。两种输出均没有 `is_fraud`、`identity_key`、人工锚点日历时间或无关匿名宽表字段。成功结果分别引用 `canonical_transaction` 和 `historical_features`。当前 `EvidenceRef.record_id` 指向交易 ID；更细粒度产物版本追踪留待路线 3～5。

## 错误边界

| 情形 | `status` / `error.code` |
|---|---|
| 输入 ID 无效 | `invalid_input` / `invalid_transaction_id` |
| 请求源与仓库绑定源不同 | `dependency_error` / `data_source_mismatch` |
| canonical 中无该交易 | `not_found` / `transaction_not_found` |
| canonical 或 feature 文件缺失 | `dependency_error` / `transaction_artifact_unavailable` 或 `feature_artifact_unavailable` |
| canonical 有交易而 feature 没有该行 | `dependency_error` / `feature_record_missing` |
| 任一文件中该 ID 有多行 | `dependency_error` / `duplicate_transaction` |
| Parquet 损坏、列缺失或数据不符合输出 schema | `dependency_error` / `query_failed` |

`get_transaction_features` 先确认 canonical 交易存在，再查询 feature；因此真正不存在的交易不会因为特征缺行而被误判为产物故障。错误结果不含数据、证据、磁盘路径或底层堆栈。显式配置绑定是当前同源依据；feature Parquet 本身没有来源列，完整的模型/特征同源机器门禁仍由路线 3 完成。

## 验证证据

- `uv run --offline pytest tests/integration/test_transaction_tools.py -q`：**16 passed**。小 fixture 含 `t0`、同时间的 `t1`、一分钟后的 `t2`；同时间两笔的 1 小时历史计数均为 0，24 小时金额均值/标准差/偏差均为 `null`；`t2` 的历史计数为 2，均值 20、总体标准差 10、偏差 30。测试还覆盖缺失交易、SQL 字符串输入、数据源不符、缺文件、缺 feature 行、重复 ID、损坏 Parquet 和字段白名单。
- 使用 `configs/ieee_cis.yaml` 及本地真实 Parquet 调用两个工具，交易 `2987000` 均返回 `ok`。交易金额 `68.5`、产品 `W`、原始相对偏移 `86400` 秒；1h/24h/7d 历史计数均为 `0`，金额均值、标准差和偏差均为 `null`。两个结果分别带有对应来源类型的证据引用，且无标签字段。
- `uv run --offline pytest`：**70 passed**；`uv run --offline python -m compileall -q src api scripts tests` 与 `git diff --check` 均通过。

## 学习笔记

参数绑定让 `transaction_id` 始终是查询值，即使输入含 SQL 片段也不会改变查询逻辑。固定列投影把 `is_fraud` 等离线字段挡在查询边界，而不是先读完整宽表再删除。读取最多两行已经足够区分“无记录”“唯一记录”和“重复记录”，避免静默取第一行。`t0` 与 `t1` 发生在同一时刻，窗口上界排除当前时间，因此二者互不构成历史；到 `t2` 时两笔才算历史。历史均值为 `null` 表示没有可计算的更早样本，不能改写成 0 或解释为真实用户从未交易；这里的 `identity_key` 仍是启发式分组。
