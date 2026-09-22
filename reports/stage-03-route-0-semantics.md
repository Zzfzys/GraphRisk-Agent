# Stage 03 路线 0：调查语义与数据源核对

> 核对日期：2026-09-22  
> 状态：路线 0 已通过；Stage 03 其他路线尚未实现  
> 默认演示数据：IEEE-CIS Fraud Detection 的已标注训练文件

## 1. 数据与模型绑定

| 角色 | 本地路径或配置 | 核对结果 |
|---|---|---|
| 原始交易 | `data/raw/ieee-cis/train_transaction.csv` | 与本地 provenance 清单 SHA-256 一致 |
| 原始身份 | `data/raw/ieee-cis/train_identity.csv` | 与本地 provenance 清单 SHA-256 一致 |
| 转换配置 | `configs/ieee_cis.yaml` | `source=ieee_cis`，人工 UTC 锚点 `2000-01-01T00:00:00Z` |
| canonical 交易 | `data/interim/ieee-cis/transactions.parquet` | SHA-256 `0106569663D4E1DD5BCA6E241DEBF520291AE3CB3E4E1B9ACD112055C08B2720` |
| 严格历史特征 | `data/interim/ieee-cis/features_all.parquet` | SHA-256 `9659D9EBD34829D974CB2E3E013E420BC8CC3FA875D37CF5A605D884D0538441` |
| 模型训练配置 | `configs/train_baseline_ieee_cis.yaml` | `dataset_source=ieee_cis`，serving features 指向上述 IEEE-CIS 文件 |
| 模型产物 | `artifacts/stage-02/ieee-cis/champion/` | `xgboost-v1`，metadata 中 `dataset_source=ieee_cis`、`selected_by=validation_pr_auc` |

模型文件为本地生成且被 Git 忽略；本次检查的 SHA-256 是 `BE9DA46426535522138B6CBCCCE1A74719F54A3F44B43A9E15EA458AB6DFDD02`。重训后哈希可变，应重新核对 metadata 与产物来源，不能把本次哈希当作永久版本号。特征 Parquet **没有** `dataset_source` 列；本路线通过明确的配置路径、阶段验证哈希和模型 metadata 人工核对同源。自动拒绝错配属于路线 3，当前 `RiskPredictor` 尚无此门禁。

合成数据的特征和 champion 位于独立目录，不用于本次真实数据演示。原始 IEEE-CIS CSV、生成 Parquet 与模型文件均继续保持 Git 忽略状态。

## 2. 字段使用契约

| 字段或字段组 | 类别 | 调查中允许的用法 |
|---|---|---|
| `transaction_id` | 原始事实 | 交易定位与证据引用；它不是用户 ID |
| `transaction_amount`, `product_code` | 原始事实 | 展示数值和产品代码；不臆测货币种类或业务含义 |
| `source_transaction_dt_seconds` | 原始相对时间 | 表示数据集起点后的秒数，可计算先后与间隔；不解释为真实日期 |
| `card_id`, `device_id`, `email_domain`, `address_id` | 映射的实体线索 | 可展示为“共享某建模实体”的关系依据；不能断言同一真实用户或设备拥有者 |
| `tx_count_last_1h/24h/7d`, `amount_mean_last_24h`, `amount_std_last_24h`, `amount_deviation_last_24h` | 已生成的历史统计 | 可展示数值、窗口与计算来源；缺失值保留 `null`，不能擅自改成 0 |
| `risk_score`, `risk_level`, `model_name`, `model_version`, `dataset_source` | 模型输出/元数据 | 标明模型版本和演示阈值；分数未校准，不宣称为真实欺诈概率 |
| `transaction_time` | 内部排序字段 | 仅用于严格历史比较；由人工锚点生成，不作为真实日历时间展示 |
| `identity_key` | 内部分组字段 | 可用于已定义的历史特征计算；不作为已验证客户身份展示 |
| `transaction_hour`, `transaction_day_of_week` | 模型内部特征 | 受人工时间锚点影响，不能作为真实当地小时或星期的解释证据 |
| `is_fraud`, `dataset_split`, 测试指标 | 离线标签/评估元数据 | 禁止进入交易调查工具输出、证据、报告或审计事件；不能当作调查时已知事实 |

`unknown-*` 是 adapter 为缺失实体建立的交易唯一占位 ID。它可以说明“来源缺失”，不能用于跨交易的共享实体关系。关系证据只允许目标交易发生前的记录；与目标交易相同 `TransactionDT` 的交易视为同时发生，也不能作为已发生历史。

## 3. 一笔真实交易的最小核对

交易 `2987000` 在 canonical Parquet 和特征 Parquet 中各找到 **1 行**，两者的交易 ID、相对时间、金额、产品、四类实体和 `identity_key` 一致。原始 `TransactionDT` 偏移为 **86,400 秒**；1 小时、24 小时、7 天历史计数均为 **0**，24 小时历史金额均值为 **`null`**。这表示在现有启发式 `identity_key` 和窗口定义下没有更早的匹配交易，不能进一步推断该真实用户过去没有交易。

同一交易通过 IEEE-CIS champion 得到 `xgboost-v1`、`dataset_source=ieee_cis`、风险分数 `0.2996322810649872`、演示等级 `low`。该等级只反映当前 `0.30/0.70` 阈值；分数未经校准，也不是欺诈事实。模型 metadata 记录的训练代码提交为 `5a32656`。

## 4. 学习笔记：解释这次输出

`TransactionDT=86400` 表示相对起点后经过 86,400 秒，人工锚点对应的日历日期不是交易真实日期。三个历史计数为零，是因为现有身份分组在三个窗口内没有更早的匹配交易；同时间交易也不会进入窗口。历史均值为 `null` 表示没有可计算的过去金额样本，与金额平均值为零不同。`identity_key` 只是 card、address 和 email 的哈希组合，不能证明存在一个真实客户账户。XGBoost 的 `0.299632` 是未校准的模型分数，`low` 是演示阈值给出的等级，都不能独立证明交易安全或欺诈。

## 5. 门禁结论与下一步

- [x] 真实交易、特征和模型来源已人工核对为 IEEE-CIS；
- [x] 路径、模型版本、人工时间锚点和关键产物哈希已记录；
- [x] 已明确事实、历史统计、模型输出与禁用字段；
- [x] 至少一笔真实交易的 canonical/feature 行和模型结果已核对；
- [x] 已记录 `identity_key`、同时事件、缺失值和未校准分数的解释边界。

路线 1 应把字段允许清单和错误状态落实为 Pydantic 契约；路线 2/4 要用测试证明标签和未来关系不泄漏；路线 3 要实现机器可检查的模型/特征同源门禁。Stage 03 整体仍未完成。
