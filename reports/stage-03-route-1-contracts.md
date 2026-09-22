# Stage 03 路线 1：调查工具契约与错误分类

> 验证日期：2026-09-22  
> 状态：路线 1 完成；这里只固定 schema，不表示查询、模型或图工具已经可调用

## 契约范围

五个工具使用相同的请求定位字段：`transaction_id`（去首尾空白后 1～128 字符）与显式 `dataset_source`（`ieee_cis` / `synthetic`）。关系工具另有 `limit`，默认 20、范围 1～50。未知字段直接拒绝。验证入口 `validate_tool_request` 在查询前把无效参数转成稳定的 `invalid_input` 结果，且不回显原始非法 ID。

结果外壳统一包含 `schema_version`、`tool`、`status`、`transaction_id`、`dataset_source`、`data`、`error`、`evidence`。`ok` 必须有与外壳交易 ID 一致的类型化 `data`；失败只能有结构化 `error`，不得携带数据或证据；证据引用的数据源必须与本次请求一致。五种 `tool` 名由各自结果类型固定，不能借用别的工具结果。

| 工具 | 输入示例（均附加 `transaction_id=2987000`, `dataset_source=ieee_cis`） | 成功时 `data` 示例或形状 | 主要证据来源 |
|---|---|---|---|
| `get_transaction` | 无其他字段 | `transaction_amount=68.5`, `product_code=W`, `source_transaction_dt_seconds=86400`, `card_id`, `device_id`, `email_domain`, `address_id` | `canonical_transaction` |
| `get_transaction_features` | 无其他字段 | `tx_count_last_1h/24h/7d=0`, `amount_mean_last_24h=null`, `amount_std_last_24h=null`, `amount_deviation_last_24h=null` | `historical_features` |
| `get_risk_score` | 无其他字段 | `risk_score=0.3`, `risk_level=medium`, `model_name=xgboost`, `model_version=xgboost-v1`, `probability_calibrated=false` | `model_artifact` |
| `get_related_entities` | `limit=20` | `matches=[{transaction_id, seconds_before_target, shared_entities}]`, `total_matches`, `truncated`；无关联时 `matches=[]`, `total_matches=0`, `truncated=false` | `entity_link` |
| `find_risky_neighbors` | `limit=20` | Stage 04 预留的 `neighbors=[{transaction_id, graph_risk_score, shared_entities}]`, `graph_model_version` | Stage 03 不生成图证据 |

上表仅展示 schema 示例，**不是实际查询结果**。尤其 `get_risk_score` 的数值示例不是交易 `2987000` 的真实预测，`find_risky_neighbors` 的成功类型只是为 Stage 04 验证兼容性；Stage 03 实现占位工具时必须返回 `unavailable`。真实交易的已核对数值见[路线 0 记录](stage-03-route-0-semantics.md)。

输入和输出示例（`get_transaction`）：

```json
{"transaction_id":"2987000","dataset_source":"ieee_cis"}
```

```json
{
  "schema_version": "1.0",
  "tool": "get_transaction",
  "status": "ok",
  "transaction_id": "2987000",
  "dataset_source": "ieee_cis",
  "data": {
    "transaction_id": "2987000",
    "transaction_amount": 68.5,
    "product_code": "W",
    "source_transaction_dt_seconds": 86400,
    "card_id": "card-a",
    "device_id": "device-a",
    "email_domain": "example.com",
    "address_id": "address-a"
  },
  "error": null,
  "evidence": [{"source_kind":"canonical_transaction","record_id":"2987000","dataset_source":"ieee_cis"}]
}
```

以上完整结果仍是**契约 fixture**，实体值并非真实 IEEE-CIS 交易字段。`source_transaction_dt_seconds` 是相对偏移，不能写成真实日历时间；`identity_key` 和 `is_fraud` 不在允许的输出模型中。模型分数显式带 `probability_calibrated=false`，不能称为已校准欺诈概率。

## 状态与错误码

| `status` | 适用情形 | 代表性 `error.code` |
|---|---|---|
| `ok` | 获取到交易、特征或分数；关系查询可为空列表 | `error=null` |
| `invalid_input` | ID 空白/过长、未知数据源、limit 越界、未知字段 | `invalid_transaction_id`, `invalid_dataset_source`, `invalid_limit`, `invalid_request` |
| `not_found` | 在已可用且已选定的数据源里没有这笔交易 | `transaction_not_found` |
| `unavailable` | 能力尚未实现，如 Stage 03 图风险邻居 | `capability_unavailable` |
| `dependency_error` | 数据/模型产物缺失、已有交易缺少特征行、来源错配、重复交易、查询或推理失败、依赖超时 | `transaction_artifact_unavailable`, `feature_artifact_unavailable`, `feature_record_missing`, `model_artifact_unavailable`, `data_source_mismatch`, `duplicate_transaction`, `query_failed`, `prediction_failed`, `dependency_timeout` |

`error.message` 固定为不含堆栈、路径和原始输入的公开文本。只有 `dependency_timeout` 标记 `retryable=true`；其余错误默认不可自动重试，调用方可按 `status` 与 `code` 决定停止或降级。`not_found` 的前提是成功查过可用数据源；文件缺失时应报告 `dependency_error`，不能误报交易不存在。

失败结果示例（仍是契约 fixture）：

```json
{"schema_version":"1.0","tool":"get_transaction","status":"not_found","transaction_id":"missing","dataset_source":"ieee_cis","data":null,"error":{"code":"transaction_not_found","message":"The transaction was not found in the selected dataset.","retryable":false},"evidence":[]}
```

## 验证与学习笔记

`tests/unit/test_investigation_contracts.py` 覆盖五类请求规范化、五种成功数据结构的序列化、空/过长 ID、limit 和来源错误、未知字段、无交易、能力不可用、产物缺失、超时、标签泄漏、跨源证据与失败结果不带伪证据。`uv run --offline pytest tests/unit/test_investigation_contracts.py -q`：**23 passed**；`uv run --offline pytest`：**54 passed**；`uv run --offline python -m compileall -q src api scripts tests` 和 `git diff --check` 均通过。

输入校验必须先于查询，否则空 ID、超长 ID 或未知字段可能进入数据库层，造成含糊的查询失败；这里将其稳定归类为 `invalid_input`。一笔不存在的交易是查询后的业务结果，模型文件缺失是环境依赖失败，二者不能混成一个“查不到”。Pydantic 的类型与字段约束使上游 Agent 能检查结果，而不是从自由文本里猜。`model_dump` 再 `model_validate` 的往返测试说明结构可以安全传给后续工作流，但不代表外部依赖已运行。路线上还需验证真实查询、模型同源和严格历史关系；这些属于路线 2～4。
