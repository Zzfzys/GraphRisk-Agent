# GraphRisk-Agent 中文说明

[English](README.md) · [启动指南](docs/data/stage-10-startup.md) · [系统架构](docs/architecture/stage-10-system.md) · [项目成绩单](reports/stage-10-final-evaluation.md)

这是面向LLM／Agent应用工程岗位的个人作品：输入交易编号，系统收集交易、历史、模型、关系与模拟政策证据，通过结构化判断和引用核验生成调查报告，再交给人工复核。网页由Streamlit呈现，FastAPI负责状态，LangGraph执行有界流程。

## 当前能证明什么

| 内容 | 当前结果 | 解释 |
|---|---|---|
| 主评分 | XGBoost三折平均AP 0.544367、ROC-AUC 0.905089 | 满足项目基础门槛，尚无真实业务收益证明 |
| GraphSAGE | 简单融合图增量0，不晋级 | 关系线索与实验分数保留；未证明已达到理论最优 |
| 当前真实Agent | **首次6/10，未达9/10门槛** | 4笔错误判断被拦截；2次重复单列；不是欺诈准确率 |
| 独立GraphRAG | text16/22、graph/hybrid18/22 | 小型既见模拟集，hybrid与graph持平 |
| 运行速度 | 完整调查HTTP中位11.36秒 | 本机12次尝试含4次503；没有稳定生产P99结论 |

见[12项主张与证据](docs/data/stage-10-final-claims.md)。历史工程CI对应0e326b0，分层共1035项通过、1个平台skip，另有15机制探针；工程通过不能给真实Agent质量加分。

截至2026-10-18，Stage10路线0～6工程交付完成，[路线7表达/面试/本人复核材料](docs/career/stage-10-presentation.md)已准备，本人验收与路线8待完成；[路线6新检出复现](docs/data/stage-10-clean-reproduction.md)已在下载缓存可用条件下通过，冷缓存安装与原始数据重训未验证；[路线5图片演示](docs/data/stage-10-picture-demo.md)已按用户要求替代录屏，整体仍In Progress。按用户决定，工程总审计后再提醒开展独立Agent质量改进。当前原始失败记录与GNN不晋级结论保留。

## 第一次从哪里开始

新读者先在自己选择的可写项目盘克隆仓库，按[英文README的公共入口](README.md#start-with-the-public-mechanism-demo)安装锁定的Python3.11环境，运行50案CLI机制演示。预期`fake=50/50 real_samples=0`；这一步没有网页，也没有调用真实模型。

你当前这台电脑采用C盘71df源码、E盘环境/工件的布局，请直接使用[本机分离路径启动说明](docs/data/stage-10-author-workspace.md)，避免在E盘旧源码中继续开发。

| 想做什么 | 去哪里 | 需要什么 |
|---|---|---|
| 看公开机制演示、合成数据流水线 | [通用启动](docs/data/stage-10-startup.md) | 已同步依赖；无需真实数据/模型 |
| 打开当前真实调查网页 | [本机启动](docs/data/stage-10-author-workspace.md) | 既有授权工件、Ollama固定digest、空闲8000/8501端口 |
| 重启后保留报告和审批 | [PostgreSQL模式](docs/data/stage-10-startup.md#postgresql-mode) | 先迁移、授权，显式选择postgres并注入私有DSN |
| 练习看报告和人工拒绝，避免新生成 | [历史回放](docs/data/stage-10-author-workspace.md#历史报告回放) | 私有旧报告、Docker及固定镜像；页面标记回放 |
| 看独立知识助手 | [GraphRAG入口](docs/data/stage-10-startup.md#independent-graphrag) | 独立项目；完整问答需固定索引/本地模型 |

当前coverage启动脚本明确选择memory模式，API重启后报告/审批丢失；UI刷新只读既有run_id。PostgreSQL保留数据库中的派生报告和审批检查点，但仍有TTL/版本校验；任何partial_evidence报告在postgres中均不建审批检查点。审批只记录决定，项目没有封卡/阻断交易动作，reviewer_id也未认证。

## 页面怎样读

交易区显示交易事实与严格早于当前事件的历史；模型区显示XGBoost主分数和GraphSAGE实验分数；关系区表示共享实体线索；政策区显示模拟条文与出处；未知项说明仍缺什么证据；审批区对当前报告绑定的复核建议记录批准/拒绝。图不可用不等于风险0，分数0.9不等于已经证实90%欺诈概率，引文存在不证明政策适用。

主项目政策RAG为调查提供模拟风控条文；独立GraphRAG在另一份原创模拟IT语料中检索文本和最多两跳关系，两者数据与成绩分别记录。工作流的工具顺序由代码控制；系统仍须人工判断，当前能力边界见[限制清单](docs/data/stage-10-known-limitations.md)。

项目结构与完整历史见[阶段索引](docs/plans/README.md)。边做边学请读[90分钟L4任务](docs/data/stage-10-architecture-learning.md)，亲自画一次请求和一次审批恢复流程；本人完成情况另记。
