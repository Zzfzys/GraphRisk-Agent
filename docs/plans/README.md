# 阶段计划索引与维护规范

本目录保存 GraphRisk-Agent 的阶段执行计划。总体目标、范围和跨阶段约束以仓库根目录的 [PROJECT_PLAN.md](../../PROJECT_PLAN.md) 为准。

## 阶段索引

| 阶段 | 状态 | 时间 | 文档 |
|---|---|---|---|
| Stage 00：环境与数据门禁 | Done | Week 0 | [stage-00-preflight.md](stage-00-preflight.md) |
| Stage 01：工程骨架与数据管线 | Done | Week 1～2 | [stage-01-foundation-data.md](stage-01-foundation-data.md) |
| Stage 02：机器学习服务 | Done | Week 3 | [stage-02-ml-service.md](stage-02-ml-service.md) |
| Stage 03：调查工具 | Done | Week 4 | [stage-03-investigation-tools.md](stage-03-investigation-tools.md) |
| Stage 04：图风险模型 | Done（工程验收；个人学习自测待完成） | Week 5 | [总纲](stage-04-graph-risk.md) · [实现路线](stage-04-implementation-roadmap.md) · [学习路线](stage-04-learning-roadmap.md) · [总验收](../../reports/stage-04-validation.md) |
| Stage 05：RAG 与 Agent | In Progress（路线 0～1 Done） | Week 6～7 | [stage-05-rag-agent.md](stage-05-rag-agent.md) · [路线 0](../../reports/stage-05-route-0-preflight.md) · [路线 1](../../reports/stage-05-route-1-policy-corpus.md) |
| Stage 06：八周 MVP | Planned | Week 8 | [stage-06-mvp.md](stage-06-mvp.md) |
| Stage 07：评估强化与基础设施 | Planned | Week 9～10 | [stage-07-hardening-platform.md](stage-07-hardening-platform.md) |
| Stage 08：本地模型与 GraphRAG | Planned | Week 11～12 | [stage-08-local-model-graphrag.md](stage-08-local-model-graphrag.md) |
| Stage 09：质量与运维 | Planned | Week 13～14 | [stage-09-quality-operations.md](stage-09-quality-operations.md) |
| Stage 10：最终交付与求职 | Planned | Week 15～16 | [stage-10-delivery-career.md](stage-10-delivery-career.md) |

## 状态定义

- `Planned`：范围已经确定，尚未开始；
- `In Progress`：正在执行；
- `Blocked`：存在明确阻塞条件；
- `Done`：验收条件全部满足且有证据；
- `Deferred`：经记录后移至后续阶段；
- `Dropped`：经过决策后从项目范围删除。

## 执行与更新流程

### 阶段开始前

1. 检查所有前置条件；
2. 将阶段状态改为 `In Progress`；
3. 填写实际开始日期；
4. 确认本阶段范围和验收标准；
5. 把新增假设写入“决策记录”。

### 阶段执行中

1. 完成一项任务后更新任务清单；
2. 代码与行为发生改变时，同步更新相关说明；
3. 计划发生偏差时，记录原因、影响和处理决定；
4. 阻塞超过一个工作日时，将状态改为 `Blocked` 并记录解除条件；
5. 不以“代码已写”为完成依据，必须记录验证证据。

### 阶段结束时

1. 逐项检查验收标准；
2. 记录测试命令、指标、报告和演示路径；
3. 填写实际结果和计划偏差；
4. 列出遗留风险与后续阶段输入；
5. 所有门禁通过后才将状态改为 `Done`。

## 计划变更规则

- 只影响当前阶段的调整，更新当前阶段文件；
- 影响技术栈、总目标、成功标准或多个阶段的调整，同时更新 `PROJECT_PLAN.md`；
- 延期时先按照总纲中的删减顺序处理增强项；
- 不允许通过降低测试标准或更换测试集掩盖延期或模型效果；
- 每项重要决定至少记录日期、背景、决定、原因和影响。

## 阶段文档固定栏目

每个阶段文件都应持续维护以下栏目：

- 状态与日期；
- 阶段目标；
- 前置条件；
- 范围与非目标；
- 学习任务；
- 实现任务；
- 交付物；
- 验收标准；
- 验证证据；
- 风险与降级方案；
- 决策记录；
- 计划变更；
- 阶段复盘。
