# Stage 02：XGBoost、统一评估与预测服务

> 状态：Planned  
> 计划时间：Week 3  
> 前置阶段：Stage 01

## 阶段目标

建立可信的表格模型基线、统一评估接口和第一版风险预测 API。

## 学习任务

- 类别不平衡；
- PR-AUC、ROC-AUC、Recall、Precision、F1 与 Top-K 指标；
- XGBoost 基本调参；
- MLflow 基础追踪；
- FastAPI 与 Pydantic。

## 实现任务

- [ ] 建立 Dummy、Logistic Regression 和 XGBoost 基线；
- [ ] 建立统一 `train/predict/evaluate` 接口；
- [ ] 记录配置、特征、指标、运行时间和模型产物；
- [ ] 输出统一模型对比表；
- [ ] 实现 `/health`、`/model/info` 和 `/risk/predict`；
- [ ] 编写模型、指标与 API 测试。

## 交付物

- 可重复训练的 XGBoost 模型；
- 基线评估报告；
- MLflow 本地实验记录；
- FastAPI 预测服务。

## 验收标准

- [ ] 所有模型使用相同时间切分；
- [ ] 主指标为 PR-AUC；
- [ ] 评估报告包含 Top-K 指标和推理延迟；
- [ ] API 返回风险分数、等级和模型版本；
- [ ] API 输入错误具有结构化错误响应。

## 验证证据

待执行后填写。

## 风险与降级方案

- 训练成本过高：先使用固定样本完成接口和基线；
- MLflow 阻塞：先输出结构化本地报告，随后接回 MLflow；
- 指标较低：记录原因并保留基线，不通过随机切分美化结果。

## 决策记录

| 日期 | 决定 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-21 | PR-AUC 为主指标 | 欺诈标签高度不平衡 | Accuracy 不作为核心判断 |

## 计划变更

暂无。

## 阶段复盘

阶段完成后填写。
