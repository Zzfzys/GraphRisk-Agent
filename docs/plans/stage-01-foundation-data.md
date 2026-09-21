# Stage 01：工程骨架与时间正确的数据管线

> 状态：Done
> 计划时间：Week 1～2  
> 前置阶段：Stage 00
> 实际开始：2026-09-21
> 实际结束：2026-09-21

## 阶段目标

建立可维护的 Python 工程，并实现从原始交易数据到训练、验证和测试特征集的可复现管线。

## 学习任务

- Python `src` layout、依赖与配置管理；
- pytest、日志和异常处理；
- DuckDB、Parquet 与基础 SQL；
- 时间切分、窗口统计和 point-in-time correctness。

## 实现任务

- [x] 建立源码、测试、配置、脚本、数据和报告目录；
- [x] 建立配置加载与结构化日志；
- [x] 定义交易、身份和图实体的数据契约；
- [x] 实现原始数据校验与清洗；
- [x] 按事件时间完成 70%/15%/15% 切分；
- [x] 实现只使用过去信息的交易窗口特征；
- [x] 输出 Parquet 特征集和数据质量报告；
- [x] 为时间边界与泄漏场景编写测试。

## 交付物

- 可安装 Python package；
- 数据字典；
- 数据导入与特征构建命令；
- train/validation/test Parquet；
- 数据质量与时间泄漏测试报告。

## 验收标准

- [x] 新环境可按照 README 运行测试；
- [x] 同一输入与配置能够生成一致的数据切分；
- [x] 历史窗口特征不包含当前或未来交易；
- [x] 数据规模、缺失率、标签比例和时间范围有报告；
- [x] 原始数据未被提交 Git。

## 验证证据

- `uv sync --locked --offline`：依赖锁与本地环境一致；
- `uv run --offline pytest`：13 passed；
- `uv run --offline python -m compileall -q src scripts tests`：通过；
- `uv run --offline python scripts/build_features.py --config configs/dev.yaml`：通过；
- 合成交易：5,000 行，97 条欺诈标签；
- 时间范围：2025-01-01 00:04:22 ～ 2025-01-18 21:42:56 UTC；
- 时间切分：3,500 / 750 / 750；
- 管线连续运行两次，原始 CSV、全量特征和三个切分文件的 SHA-256 均一致；
- 详细证据见 `reports/stage-01-validation.md`；
- 生成的数据质量报告位于 `reports/generated/feature_report.md`，该文件按设计不提交 Git。

## 风险与降级方案

- 16GB 内存不足：使用 DuckDB/Polars 风格的分批处理，避免 pandas 多份完整复制；
- 数据字段过于匿名：只对可解释实体建模，其余字段保留为模型特征；
- 真实数据延迟：使用同一数据契约下的合成数据替代。

## 决策记录

| 日期 | 决定 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-21 | 交易作为主要调查对象 | 与 IEEE-CIS 标签和数据粒度一致 | 账户与团伙调查后置 |
| 2026-09-21 | 使用 DuckDB 处理 CSV、Parquet 和历史窗口 | 降低内存复制并保留 SQL 可读性 | Stage 01 不引入 pandas |
| 2026-09-21 | 建立独立 canonical transaction schema | IEEE-CIS 原始文件尚未取得且字段匿名 | 真实数据到位后实现显式 adapter |
| 2026-09-21 | 相同时间戳的交易视为同时发生 | 数据源没有可靠的同时间事件顺序 | 同时间交易不能互相提供历史信息 |
| 2026-09-21 | 先计算严格历史特征，再进行时间切分 | 验证和测试推理可以使用其发生前的真实历史 | 窗口必须排除当前和未来事件 |

## 计划变更

- IEEE-CIS 原始文件尚未取得，本阶段以确定性合成数据完成 canonical pipeline；
- 原计划中的 `TransactionDT` 在 canonical schema 中表示为 UTC `transaction_time`；
- IEEE-CIS adapter 明确后置到认证数据可用之后；
- 本阶段没有引入 PostgreSQL，文件层管线由 DuckDB 和 Parquet 完成。

## 阶段复盘

Stage 01 已完成可安装 Python package、配置和结构化日志、数据契约、合成后备数据、校验与标准化、历史窗口特征、确定性时间切分、质量报告和泄漏测试。

进入 Stage 02 的输入：

- `data/processed/train_features.parquet`；
- `data/processed/validation_features.parquet`；
- `data/processed/test_features.parquet`；
- 可重复运行的 `scripts/build_features.py`；
- 统一 canonical schema 和已记录的真实数据 adapter 边界。

遗留事项：IEEE-CIS 下载与 adapter 仍需用户 Kaggle 账户，不能把当前合成数据指标包装为真实风控效果。
