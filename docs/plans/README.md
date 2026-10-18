# 阶段计划索引与维护规范

本目录保存 GraphRisk-Agent 的阶段执行计划。总体目标、范围和跨阶段约束以仓库根目录的 [PROJECT_PLAN.md](../../PROJECT_PLAN.md) 为准。

跨聊天继续 Stage 09 或 Stage 10 前，先读 [当前交接与后续 GNN 检索约定](stage-09-cross-chat-handoff.md)；核对实际源码工作区、最新证据及原路线 6～8 的状态，避免把 5B 子路线完成误读为整个 Stage 09 完成。

## 阶段索引

| 阶段 | 状态 | 时间 | 文档 |
|---|---|---|---|
| Stage 00：环境与数据门禁 | Done | Week 0 | [stage-00-preflight.md](stage-00-preflight.md) |
| Stage 01：工程骨架与数据管线 | Done | Week 1～2 | [stage-01-foundation-data.md](stage-01-foundation-data.md) |
| Stage 02：机器学习服务 | Done | Week 3 | [stage-02-ml-service.md](stage-02-ml-service.md) |
| Stage 03：调查工具 | Done | Week 4 | [stage-03-investigation-tools.md](stage-03-investigation-tools.md) |
| Stage 04：图风险模型 | Done（工程验收；个人学习自测待完成） | Week 5 | [总纲](stage-04-graph-risk.md) · [实现路线](stage-04-implementation-roadmap.md) · [学习路线](stage-04-learning-roadmap.md) · [总验收](../../reports/stage-04-validation.md) |
| Stage 05：RAG 与 Agent | Done（工程路线 0～8；个人学习自测独立） | Week 6～7 | [stage-05-rag-agent.md](stage-05-rag-agent.md) · [路线 0](../../reports/stage-05-route-0-preflight.md) · [路线 1](../../reports/stage-05-route-1-policy-corpus.md) · [路线 2](../../reports/stage-05-route-2-policy-retrieval.md) · [路线 3](../../reports/stage-05-route-3-retrieval-evaluation.md) · [路线 4](../../reports/stage-05-route-4-graph-workflow.md) · [路线 5](../../reports/stage-05-route-5-reporting.md) · [路线 6](../../reports/stage-05-route-6-approval.md) · [路线 7](../../reports/stage-05-route-7-reliability-evaluation.md) · [路线 8](../../reports/stage-05-route-8-acceptance.md) |
| Stage 06：八周 MVP | Done（工程路线 0～7；个人学习自测独立） | Week 8 | [总纲](stage-06-mvp.md) · [实现路线](stage-06-implementation-roadmap.md) · [学习路线](stage-06-learning-roadmap.md) · [50 案评估](../../reports/stage-06-route-6-agent-evaluation.md) · [总验收](../../reports/stage-06-route-7-acceptance.md) · [中文演示指南](../data/stage-06-demo-guide.md) |
| Stage 07：Agent 强化与持久化平台 | Done（工程路线 0～8；个人学习自测独立） | Week 9～10 | [总纲](stage-07-hardening-platform.md) · [实现路线](stage-07-implementation-roadmap.md) · [学习路线](stage-07-learning-roadmap.md) · [Agent v2](../../reports/stage-07-route-6-agent-evaluation.md) · [Compose/故障](../../reports/stage-07-route-7-compose-faults.md) · [总验收](../../reports/stage-07-route-8-acceptance.md) · [中文演示](../data/stage-07-demo-guide.md) |
| Stage 08：本地模型与 GraphRAG | Done（工程；2026-10-07 Docker 恢复复测通过，个人自测独立） | Week 11～12 | [总纲](stage-08-local-model-graphrag.md) · [实现路线](stage-08-implementation-roadmap.md) · [学习路线](stage-08-learning-roadmap.md) · [路线 0 门禁](../../reports/stage-08-route-0-preflight.md) · [路线 1 合同](../../reports/stage-08-route-1-provider-contract.md) · [路线 2 实测](../../reports/stage-08-route-2-local-model.md) · [路线 3 对照](../../reports/stage-08-route-3-model-comparison.md) · [路线 4 语料](../../reports/stage-08-route-4-corpus-schema.md) · [路线 5 检索](../../reports/stage-08-route-5-retrieval.md) · [路线 6 问答](../../reports/stage-08-route-6-grounded-answer.md) · [路线 7 评估](../../reports/stage-08-route-7-graphrag-evaluation.md) · [路线 8 总验收](../../reports/stage-08-route-8-acceptance.md) · [L8 个人练习](../data/stage-08-demo-and-interview.md) |
| Stage 09：真实 Agent 基线、GraphSAGE 诊断与持续质量 | In Progress（原路线0～8及5A/5B工程完成；基线质量Pass/图增量Fail不晋级；本人学习/人工实用性Pending） | Week13起；5B追加约2.5～3周 | [总纲](stage-09-quality-operations.md) · [实现路线](stage-09-implementation-roadmap.md) · [学习路线](stage-09-learning-roadmap.md) · [5B实施](stage-09-route-5b-model-enhancement.md) · [5B学习](stage-09-route-5b-learning.md) · [5B强基线](../../reports/stage-09-route-5b-2-baselines.md) · [5B扩量调参](../../reports/stage-09-route-5b-3-graph-expansion.md) · [5B消融/增强](../../reports/stage-09-route-5b-4-conditional-graph.md) · [5B三折确认](../../reports/stage-09-route-5b-5-confirmation.md) · [5B覆盖验收](../../reports/stage-09-route-5b-6-coverage.md) · [5B总验收](../../reports/stage-09-route-5b-7-acceptance.md) · [5A报告](../../reports/stage-09-route-5a-agent-quality.md) |
| Stage 10：性能、最终交付与求职 | In Progress（路线0～6工程Done，新真实质量Fail（6/10），7～8 Planned；本人学习Pending） | 2026-10-17启动，约44小时 | [总纲](stage-10-delivery-career.md) · [实现](stage-10-implementation-roadmap.md) · [学习](stage-10-learning-roadmap.md) · [路线0](../../reports/stage-10-route-0-preflight.md) |

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


2026-10-07 路线4工程完成：12次真实CUDA GNN（5消融/基础、6组合复用首组、2确认），旧checkpoint完整10k验证复现差异0；胜出candidate-4三种子平均AP 0.179247±0.009721，业务参照0.213027，晋级=False。验证先冻结决策，历史已观察测试一次复核，不以测试重选。25新测试、根557/18（18缺DSN），12权重各512验证根重载通过，失败构建与GPU探针记录/耗时保留。服务未切换、LLM/付费0；L4本人学习与路线1人类复核待完成，下一路线5决策接入与真实同集重放。 见[路线4报告](../../reports/stage-09-route-4-graphsage-diagnosis.md)及[L4讲义](../data/stage-09-graphsage-diagnosis.md)。

Stage10路线1已工程验收：[报告](../../reports/stage-10-route-1-measurement-contract.md)、[核对与学习](../data/stage-10-measurement.md)；10+2输入、14实际追加请求守卫及计时运行器冻结，CPU898含47新增；12模拟/0真实，路线2实际质量/性能尚未开始，本人学习Pending。

## 2026-10-17 路线2实测交接

[正式报告](../../reports/stage-10-route-2-real-measurement.md)：10笔首次真实受限判断6/10，门槛9/10未达，质量Fail；2次重复通过不补分。四笔错误输出均被拦截，unsafe0。实测工程已完成，Stage10整体仍In Progress，路线3～8和本人学习/人工实用性待完成。唯一台账142/144、剩2，无新训练/付费。当前新质量不能用历史5A40/40代替。
