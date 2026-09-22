# Stage 03 路线 3：风险模型工具验证

> 验证日期：2026-09-22  
> 状态：路线 3 已完成；Stage 03 关系证据、工作流和总验收仍未完成

## 实现内容

`src/graphrisk/investigation/risk_tool.py` 增加 `RiskModelTool` 和 `get_risk_score`。使用 Stage 02 已有的 `RiskPredictor`，没有复制模型预处理、阈值计算或重新训练。工具启动时校验四个绑定条件：调查数据配置的 `data.source`、训练配置的 `dataset_source`、两份配置解析后的同一个 serving feature 路径、champion `metadata.json` 的 `dataset_source`。任何不一致先返回 `data_source_mismatch`，不调用预测。metadata 格式和阈值有基本校验；模型仅加载一次并复用。

调用示例：

```python
from graphrisk.config import load_config
from graphrisk.training.config import load_training_config
from graphrisk.investigation.risk_tool import RiskModelTool, get_risk_score

tool = RiskModelTool.from_configs(
    load_config("configs/ieee_cis.yaml"),
    load_training_config("configs/train_baseline_ieee_cis.yaml"),
)
result = get_risk_score(
    {"transaction_id": "2987000", "dataset_source": "ieee_cis"}, tool
)
print(result.model_dump_json())
```

输入先由路线 1 契约校验；预测前调用路线 2 的特征工具，确认 canonical 交易和唯一特征行均存在。成功结果包含 `risk_score`、`risk_level`、`model_name`、`model_version`、`probability_calibrated=false`，以及指向模型版本的 `model_artifact` 证据引用。失败结果不携带文件路径、异常堆栈、标签或任何伪造证据。

| 情形 | 结构化结果 |
|---|---|
| 空白/无效 ID | `invalid_input / invalid_transaction_id` |
| 请求、训练配置、serving 路径或模型 metadata 来源不一致 | `dependency_error / data_source_mismatch` |
| canonical 中没有交易 | `not_found / transaction_not_found` |
| 交易存在而 feature 行缺失 | `dependency_error / feature_record_missing` |
| feature 文件缺失 | `dependency_error / feature_artifact_unavailable` |
| champion 模型或 metadata 缺失/损坏 | `dependency_error / model_artifact_unavailable` |
| 模型推理异常或结果不符合契约 | `dependency_error / prediction_failed` |

来源门禁基于明确的配置、解析后的路径和模型 metadata，**不从相同列名推断来源**。现有 feature Parquet 没有来源列、champion metadata 没有特征文件哈希，因此该检查还不能证明文件内容从未被同路径替换；当前真实产物的哈希与来源已在[路线 0 记录](stage-03-route-0-semantics.md)人工核对。未来若重训或移动产物，需要重新核对来源；更强的内容绑定可在产物清单加入哈希。这个限制不应被描述为已实现文件级完整性验证。

## 验证证据

- `uv run --offline pytest tests/integration/test_risk_tool.py -q`：**15 passed**。测试含本地小模型与现有 `/risk/predict` API 响应逐字段对比；跨配置/跨路径/metadata 来源不一致；模型文件、metadata 或 feature 文件缺失；损坏模型；无交易；特征行缺失；输入错误；推理异常与结果竞态。构造的是独立 fixture，不依赖本地受限数据。
- 本地 IEEE-CIS 交易 `2987000`：启动门禁通过，`get_risk_score` 返回 `ok`、`xgboost-v1`、`risk_score=0.2996322810649872`、`risk_level=low`、`probability_calibrated=false`，与同一 champion 的 `RiskPredictor.predict("2987000")` 分数、等级和版本完全一致。
- `uv run --offline pytest`：**85 passed**；`uv run --offline python -m compileall -q src api scripts tests` 与 `git diff --check` 均通过。

## 学习笔记

模型文件是训练得到的 artifact，metadata 记录模型版本、数据源和演示阈值；调查工具必须在推理前检查它们与所选特征产物一致。相同 schema 的合成和 IEEE-CIS 特征不代表同源，所以配置路径和 metadata 不符时先拒绝。`0.299632` 是模型产生的未校准分数，`low` 只表示它低于当前 `0.30` 演示阈值，不能说“只有 29.96% 概率欺诈”或认定交易安全。champion 已按验证集 PR-AUC 选出；本工具只加载它，不根据测试集指标重新选模。缺模型属于依赖故障，缺交易属于业务查询结果，二者需要不同状态供后续 Agent 正确降级。
