# Stage10路线3：最终评估、卡片与限制验收

2026-10-18：**路线3工程Done；真实Agent质量仍Fail6/10，GNN不晋级，L3本人Pending；Stage10整体In Progress。**

这一步把已有实验整理为能追溯的成绩单，并检查数字是否来自真实记录。没有调整模型、提示或题目，也没有新增训练/真实生成/付费。

## 交付与验证

| 条件 | 实际交付/证据 | 结论 |
|---|---|---|
| 模型、Agent、GraphRAG、性能分组汇总 | [最终评估](stage-10-final-evaluation.md)，各集合/版本/分母与失败分开 | 通过 |
| 公开数字可追溯 | [主张E01～E12](../docs/data/stage-10-final-claims.md)，私有claims-bindings.json及audit.json保存路径/hash | 通过 |
| 独立重算模型指标 | 16份封存分数、17组指标比较；AP/ROC/Top-K与原记录误差小于1e-10；三折和历史test行序一致 | 通过，无新预测/训练 |
| Agent与GraphRAG来源/评分 | 既有只读评分器重判12条Agent、90条GraphRAG，原始输入/结果/代码绑定通过 | 通过，质量6/10未变 |
| 费用与预算 | 官方导出40请求小时汇总离线重核，3份生成/fit账本前后hash不变，142/144剩2 | 通过，逐请求实扣仍未知 |
| 模型/数据卡与限制 | 更新[模型卡](../docs/data/stage-09-5b-model-card.md)/[数据卡](../docs/data/stage-09-5b-data-card.md)，新增[Agent卡](../docs/data/stage-10-agent-card.md)/[限制](../docs/data/stage-10-known-limitations.md) | 通过 |
| 学习及计划同步 | [105分钟学习](../docs/data/stage-10-evaluation-learning.md)，总计划/实现/学习/交接/索引同步 | 材料完成，本人Pending |

已有bootstrap、旧5A、机制测试、覆盖和0e326b0 CI为绑定后复用，本轮未重跑旧训练/预测/全套pytest/真库/镜像。不把1035历史测试通过写成本轮新测试；本轮只修改Markdown，采用证据复核、数字核对、相对链接及Git diff检查。完整检查数、文件hash和Git状态见本轮私有acceptance.json。

## 发现与处理

若干当前摘要还停在路线2实测前，已更新；带日期的历史条目保留。新增GraphSAGE同集合结果，并区分种子指标均值与历史test分数ensemble。强调供应方匿名字段时点、图训练上限、既见测试集、模拟政策和有界工作流限制。

首轮私有审计v1已完成模型/GraphRAG与账单核验，但附加来源登记错把预算SQLite放在route3子目录，发生FileNotFoundError。修正为既有核验器登记的真实路径，在v2新目录重跑；v1脚本/输出/失败保留。仅修审计路径，不修改原始账本或模型结果。

## 复核与下一步

正式私有目录：E:/GraphRisk-Agent/reports/generated/stage10/route3/20261018-final-evaluation-v2/。audit.py为只读验证器，输出采用独占创建；再次全量复核需复制它到新的E盘忽略目录后运行，不能覆盖当前证据。日常可用学习页已有check命令核验12条Agent。

下一路线4整理英中文README、架构和启动说明。按用户约定保留6/10与4笔失败，路线0～8工程交付及总审计完成后再提醒独立Agent质量专项；不提前消耗剩余预算或启动GNN检索。人工复核、视频和个人学习必须由本人完成，不能代签。
