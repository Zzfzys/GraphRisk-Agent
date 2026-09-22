# Stage 02：XGBoost、统一评估与预测服务

> 状态：Done
> 计划时间：Week 3  
> 前置阶段：Stage 01
> 实际开始：2026-09-21
> 实际结束：2026-09-21

## 阶段目标

建立可信的表格模型基线、统一评估接口和第一版风险预测 API。

## 学习任务

- 类别不平衡；
- PR-AUC、ROC-AUC、Recall、Precision、F1 与 Top-K 指标；
- XGBoost 基本调参；
- MLflow 基础追踪；
- FastAPI 与 Pydantic。

## 实现任务

- [x] 建立 Dummy、Logistic Regression 和 XGBoost 基线；
- [x] 建立统一 `train/predict/evaluate` 接口；
- [x] 记录配置、特征、指标、运行时间和模型产物；
- [x] 输出统一模型对比表；
- [x] 实现 `/health`、`/model/info` 和 `/risk/predict`；
- [x] 编写模型、指标与 API 测试。

## 交付物

- 可重复训练的 XGBoost 模型；
- 基线评估报告；
- MLflow 本地实验记录；
- FastAPI 预测服务。

## 验收标准

- [x] 所有模型使用相同时间切分；
- [x] 主指标为 PR-AUC；
- [x] 评估报告包含 Top-K 指标和推理延迟；
- [x] API 返回风险分数、等级和模型版本；
- [x] API 输入错误具有结构化错误响应。

## 验证证据

- `uv sync --locked --offline` 通过；
- `uv lock --check --offline` 通过；
- `uv run --offline pytest`：25 项测试全部通过；
- `python -m compileall src api scripts tests` 通过；
- `uv build --offline` 成功生成 sdist 与 wheel，产物包含 API 和模型源码；
- 三个模型完成真实训练，MLflow 中三个运行均为 `FINISHED`；
- Logistic Regression 以验证集 PR-AUC `0.1457` 胜出，测试集 PR-AUC 为 `0.1892`；
- XGBoost 测试集 PR-AUC 为 `0.2024`，但未用测试结果反向改变选型；
- Uvicorn 真实进程验证 `/health`、`/model/info`、成功预测、404 与 422；
- 完整证据见 [`reports/stage-02-validation.md`](../../reports/stage-02-validation.md)。

## 风险与降级方案

- 训练成本过高：先使用固定样本完成接口和基线；
- MLflow 阻塞：先输出结构化本地报告，随后接回 MLflow；
- 指标较低：记录原因并保留基线，不通过随机切分美化结果。

## 决策记录

| 日期 | 决定 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-21 | PR-AUC 为主指标 | 欺诈标签高度不平衡 | Accuracy 不作为核心判断 |
| 2026-09-21 | 仅使用验证集 PR-AUC 选择 champion | 保持测试集独立，避免测试泄漏 | Logistic Regression 被选中，即使 XGBoost 测试 PR-AUC 更高 |
| 2026-09-21 | MLflow 使用完整发行版和 SQLite 后端 | Windows 下精简版配合本地文件存储无法稳定查询刚创建的运行 | 增加依赖体积，换取可复查的本地实验记录 |
| 2026-09-21 | 风险等级暂用 0.30/0.70 阈值 | Stage 02 需要稳定 API 契约，当前无业务成本数据 | 后续必须把等级与概率校准分开说明 |

## 计划变更

- 原计划强调 XGBoost 交付；实际补充 Dummy 和 Logistic Regression，用统一接口建立更可信的对照。
- 训练和服务均使用 Stage 01 合成数据；指标只能作为工程验证，不能用于声称真实风控效果。

## 阶段复盘

本阶段完成了从 point-in-time 特征到可服务模型的闭环。训练入口可重复运行，MLflow 保存实验元数据，FastAPI 可按 `transaction_id` 返回结构化风险结果。验证集选型规则正确抵抗了“测试集表现更好就换模型”的诱惑。

进入 Stage 03 时直接复用 `RiskPredictor` 作为确定性风险工具。需要继续保留的边界包括：启发式实体身份、未经校准的分数、启发式风险等级阈值，以及 IEEE-CIS 人工时间锚点。

## IEEE-CIS 补充验证

真实数据 adapter 完成后，三个基线在独立 IEEE-CIS 产物目录重新训练。三个 MLflow 运行均完成；XGBoost 以验证集 PR-AUC `0.1736` 成为 champion，测试集 PR-AUC 为 `0.1522`。真实 champion 已通过 FastAPI 加载和预测验证。单请求约 `802ms`，说明接入成功但单行预处理仍需优化。详细证据见 [`reports/ieee-cis-adapter-validation.md`](../../reports/ieee-cis-adapter-validation.md)。

## 2026-09-22 复核

31 项全量测试、两套 champion 产物与 MLflow 记录、真实 IEEE-CIS HTTP 成功及错误响应均通过。复核没有重新训练模型；详细记录见 [`reports/stage-00-02-recheck-2026-09-22.md`](../../reports/stage-00-02-recheck-2026-09-22.md)。
