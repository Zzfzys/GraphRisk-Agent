# Stage 03：确定性调查工具与无 LLM 工作流

> 状态：In Progress
> 计划时间：Week 4  
> 前置阶段：Stage 02
> 实际开始：2026-09-22
> 实际结束：—

详细的七条实施路线、每条路线的学习任务与验收方法见 [Stage 03 实施路线](stage-03-implementation-roadmap.md)。路线 0～5 已完成；路线 6 尚未实施总验收。

## 阶段目标

在接入 LLM 前，建立可以独立测试的调查工具和确定性调查流程。

## 学习任务

- Tool contract 与结构化输出；
- 幂等性、超时、重试和错误分类；
- 工作流状态与审计日志；
- Agent 工具的安全边界。

## 实现任务

- [x] 路线 0：核对默认 IEEE-CIS 数据源、模型版本、字段边界和真实交易样例；
- [x] 路线 1：统一工具契约与错误分类；
- [x] `get_transaction`；
- [x] `get_transaction_features`；
- [x] `get_risk_score`；
- [x] `get_related_entities`；
- [x] `find_risky_neighbors` 的本地占位实现；
- [x] 明确每个工具的输入、输出和错误模型；
- [x] 建立不依赖 LLM 的顺序调查流程；
- [x] 为成功、空结果、无效参数和服务失败编写测试。

## 交付物

- 工具接口与 schema；
- 工具测试；
- 无 LLM 调查报告；
- 审计事件格式。

## 验收标准

- [ ] 所有工具可单独调用；
- [ ] 输出均可由 Pydantic schema 验证；
- [ ] 工具错误不会变成无结构字符串；
- [ ] 无 LLM 工作流能够完成一次基础交易调查；
- [ ] 工具调用过程可追踪。

## 验证证据

- 路线 0：原始数据、canonical Parquet、历史特征、XGBoost champion 的路径、来源、哈希和版本已核对；
- 抽查交易 `2987000`：canonical 与特征行各 1 条、核心字段一致，模型返回 `xgboost-v1` 与 `dataset_source=ieee_cis`；
- 字段允许清单、禁止清单和时间/身份解释边界见 [`reports/stage-03-route-0-semantics.md`](../../reports/stage-03-route-0-semantics.md)；
- 路线 1：五个工具的 Pydantic 请求/结果 schema、状态与错误码、输入拒绝和结果不变量已实现；契约测试及五个示例见 [`reports/stage-03-route-1-contracts.md`](../../reports/stage-03-route-1-contracts.md)；
- 路线 2：两个只读工具通过小 fixture 测试和 IEEE-CIS 交易 `2987000` 的真实查询；查询、错误分支和学习笔记见 [`reports/stage-03-route-2-transaction-tools.md`](../../reports/stage-03-route-2-transaction-tools.md)；
- 路线 3：风险工具复用 Stage 02 `RiskPredictor`，完成配置路径/metadata 同源门禁、API 对比、真实 IEEE-CIS 分数核对与失败分类；证据见 [`reports/stage-03-route-3-risk-tool.md`](../../reports/stage-03-route-3-risk-tool.md)；
- 路线 4：本地共享实体关系工具与图风险占位实现已通过小 fixture 测试和 IEEE-CIS 实际抽查；证据见 [`reports/stage-03-route-4-relations.md`](../../reports/stage-03-route-4-relations.md)；
- 路线 5：固定顺序工作流、确定性报告、独立审计和 CLI 已实现；重复执行、停止/降级分支及真实 IEEE-CIS 演示见 [`reports/stage-03-route-5-workflow.md`](../../reports/stage-03-route-5-workflow.md)；
- 路线 6 尚无总验收证据；不得因此将整个 Stage 03 标为 `Done`。

## 风险与降级方案

- 数据库尚未接入：使用 repository interface 和本地实现；
- 图工具尚未完成：返回明确的 unavailable 状态，不伪造证据；
- 工具数量膨胀：优先保留调查主链所需的最小集合。

## 决策记录

| 日期 | 决定 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-21 | 先 Tool，再 Agent | 保证业务逻辑可测试且不依赖 LLM | Week 4 不接入自由式 Agent |
| 2026-09-22 | 默认演示使用 IEEE-CIS，模型与特征必须同源 | 真实数据和 XGBoost champion 已可用 | 合成数据仅作确定性测试 fixture |
| 2026-09-22 | Stage 03 只提供严格历史的共享实体查询；风险邻居留给 Stage 04 | 当前没有图风险证据 | `find_risky_neighbors` 只返回 `unavailable` |
| 2026-09-22 | 调查工具不暴露 `is_fraud` | 防止离线标签及未来关系泄漏 | 工具与测试必须实施字段和时间门禁 |
| 2026-09-22 | 特征文件没有数据源列，先以配置路径、验证哈希和模型 metadata 人工核对 | 相同 schema 不能证明同源 | 路线 3 实现机器可检查的同源门禁 |
| 2026-09-22 | 统一结构化结果与固定错误码；无效请求不回显原始 ID | 便于后续工作流区分业务空结果与依赖故障 | 路线 2～5 的工具实现复用同一契约 |
| 2026-09-22 | 查询绑定显式数据源与 Parquet 路径，并区分交易不存在与特征行缺失 | 缺失特征行说明管线产物不完整，不应被当作无交易 | 路线 3 继续实现模型与特征同源门禁 |
| 2026-09-22 | 模型工具启动时交叉核对调查/训练来源、serving 特征路径与 champion metadata | 相同特征 schema 不能证明同源，错配必须先于推理被拒绝 | 不修改 Stage 02 预测行为；文件内容哈希仍需独立产物清单 |
| 2026-09-23 | 共享实体关系仅使用严格历史、有效实体和有限结果；图风险邻居固定返回 `unavailable` | 共享关系不是 GNN 风险结论，缺失占位值会形成假连接 | Stage 04 接管真正的图模型邻居查询 |
| 2026-09-23 | 报告内容与运行审计分离；后续工具失败时保留已有证据 | 内容幂等性不应被 trace ID 和运行时间破坏，单个依赖失败也不应抹掉事实 | Stage 05 可在确定性报告之上接入受控 Agent |

## 计划变更

- 补充七条可验收实施路线，明确学习任务、工作时间和每日可见进度；
- 明确 Stage 03 与 Stage 04 的关系查询边界，避免占位能力被误称为图风险证据。
- 路线 0 根据真实 IEEE-CIS 产物固定字段与来源约束，未修改模型或数据切分。
- 路线 1 只实现五个工具的接口、错误分类与契约测试；真实查询和工作流继续留给路线 2～5。
- 路线 2 实现交易/历史特征只读查询，新增 `feature_record_missing` 错误码；不包含模型或关系查询。
- 路线 3 实现风险模型工具与配置/metadata 同源检查；仍不生成关系证据或调查报告。
- 路线 4 实现本地严格历史共享实体关系和图占位；路线 5 才组合工具生成报告与审计。
- 路线 5 实现固定状态工作流、报告/审计 schema 和命令行入口；Stage 03 仍等待路线 6 总验收。

## 阶段复盘

阶段完成后填写。
