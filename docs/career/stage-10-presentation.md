# 求职表达：GraphRisk-Agent与独立GraphRAG

2026-10-18；路线7材料。面向用户已选的LLM应用算法、Agent、RAG、AI应用和知识图谱/大模型应用方向，不代表已分析某个最新招聘JD。个人贡献和技能熟练程度由本人确认，当前本人演练/10案实用性复核仍Pending。配套[面试练习](stage-10-interview-drills.md)、[本人复核](stage-10-human-review.md)。

## 简历项目条目草稿

**GraphRisk-Agent｜基于证据的风控调查应用｜个人作品**

技术：Python、FastAPI、LangGraph、Streamlit、Ollama、XGBoost、PyTorch/PyG、PostgreSQL；政策检索默认TF-IDF，pgvector为可切换实现。下面是项目已实现能力的事实草稿；只保留自己能够解释、复现并说明AI辅助过程的条目。个人起止时间/承担内容待本人补充，不编造任职公司或业务规模。

- 构建交易调查的有界工作流，串联交易/严格历史特征、主模型风险、共享实体与实验性图证据、带版本的模拟政策检索，校验结构化模型判断、引用和人工审批；错误输出被拒绝发布，审批不执行真实封卡。
- 建立按时间切分的树/图/简单融合对照，增强XGBoost三折外层平均AP **0.544367**、ROC-AUC **0.905089**；GraphSAGE融合无增量，不晋级，保留关系证据与实验角色，由XGBoost提供主评分。[E01/E02](../data/stage-10-final-claims.md)
- 对当前增强配置冻结10笔首次真实本地模型调查，来源与受限判断联合通过 **6/10，未达到9/10门槛**，四笔无效输出被拦截；两次固定重复单列，未用模拟案例或重复补分。[E04](../data/stage-10-final-claims.md)
- 实现PostgreSQL派生报告/检查点与审批恢复、报告hash绑定、幂等与TTL检查，并区分memory重启丢失和数据库恢复；具备分层回归、私有证据台账及有缓存的新检出复现记录。[持久化](../data/stage-07-postgres-service.md)、[复现](../data/stage-10-clean-reproduction.md)

简历篇幅不足可选前三条；不把6/10删去后换成旧5A的30/30或“65案真实模型全部通过”。也不要把所有框架列为已熟练掌握。图学习经历单独描述已有科研事实，不把这份作品包装成企业任职。

**Enterprise GraphRAG｜带出处与拒答的模拟IT知识问答｜独立小项目**

- 基于项目原创模拟语料实现文本、最多两跳图及混合检索，绑定实体/关系/政策时间版本与必要引用，缺少证据或输出无效时拒答；语料24文档、58块、22节点、40边。[语料与边界](../../projects/enterprise-graphrag/README.md)
- 在曾用于诊断/部分开发的冻结30问中，可回答22问，text通过16/22、graph和hybrid均18/22；两跳各为5/8、7/8、7/8，混合方法未胜过图方法，不称新盲测或跨企业泛化。[E07](../data/stage-10-final-claims.md)

主项目的模拟风控政策RAG与独立IT GraphRAG是两个任务，不说GraphRAG已接进每笔风控调查。数据是公开授权交易和原创模拟文档，不是公司内部知识库。

## 按目标岗位选择讲述重点

| 目标方向 | 优先展示 | 必须能解释的取舍 |
|---|---|---|
| LLM应用/Agent算法 | 有界流程、结构化输出、引用/语义校验、真实失败和预算 | LLM只判断受限字段，程序也能计算其中部分事实；尚未证明开放式规划或自由推理收益 |
| RAG算法/知识图谱与大模型应用 | 时间版本过滤、冻结问题、两跳路径、必需引用与拒答 | 主项目默认TF-IDF；独立GraphRAG是既见小型模拟集，hybrid与graph持平 |
| AI应用工程 | API/UI、幂等、检查点/恢复、故障分支、可复现安装 | 单worker、reviewer未认证、无真实业务动作；工程通过与质量达标不同 |

这是根据已选方向组织已有项目证据，不是对招聘市场的实时排序。获得具体JD后再核对实际要求，不能为了关键词补写未实现功能。

## Agent约90秒示例稿

以下只作组织结构示例，尚未经本人计时。内容描述项目已做工作，不代表每个动作都由本人手写完成；本人尚未确认的贡献不能照读为亲自完成。读懂后用自己的话重写，实际耗时记录到私有笔记。

> 我做的是一个辅助人工调查交易的个人作品。用户输入交易编号，系统按固定边界收集交易、严格较早的历史、XGBoost分数、图关系和带出处的模拟政策，再让本地模型输出受限判断，由程序核验事实、引用和未知条件。通过的报告进入人工复核，审批只记状态，不封卡。工程用FastAPI、LangGraph和Streamlit，也有PostgreSQL报告与审批恢复。我把机制测试和真实模型评价分开：当前配置十笔首次真实调查只有六笔联合通过，另外四笔被拦截，没达到预设门槛；两个重复结果不补分。报告中的事实文字主要由程序生成，所以我不把它说成模型自由推理能力。项目开发中使用了AI辅助，我会具体说明自己能复现和解释的部分，以及仍未掌握的代码。后续质量改进会另冻结开发与未见验证集，不重跑旧失败题救分。

追问时打开[架构](../architecture/stage-10-system.md)、[Agent卡](../data/stage-10-agent-card.md)与[E04/E08](../data/stage-10-final-claims.md)，区分调查HTTP和模型HTTP；11.36秒是本机12次尝试含失败的中位数，不是生产SLO。

## GNN约90秒示例稿

同样未经本人计时，不要求背诵。

> 我的图学习背景主要用在判断关系信号有没有增益，而不是预设GNN必须赢。项目先审计了两层GraphSAGE的根节点预测、梯度和两跳传播，再检查采样、严格时间边界、同特征与有边无边对照，并在有限预算内做消融和调参。最终用三时间折、每折三图种子确认：增强XGBoost平均AP约0.544、ROC-AUC约0.905；固定校准规则下三个融合都把树权重选为一，图没有增量，因此没有进入主评分。关系查询仍能帮助提供线索，图分数保留实验角色。这个结论只说明当前数据、特征和预算下没有确认融合收益，不代表GraphSAGE已调到理论最好，也不能证明所有GNN无效。历史test已被观察，匿名供应方特征的上线时点也未独立证实，所以我不宣传新盲测或业务收益。后续关系模式检索将作为另外的任务单独启动。

追问时打开[模型卡](../data/stage-09-5b-model-card.md)、[三折确认](../../reports/stage-09-route-5b-5-confirmation.md)与[模型实现](../../src/graphrisk/graph/model.py)。不能用某折早停验证的最好数字代替外层结果。

## English project summary draft

**GraphRisk-Agent — a personal, evidence-grounded fraud investigation application.** Built a bounded workflow over transaction history, XGBoost scores, experimental GraphSAGE signals and cited simulated policies, with structured-output validation and human approval records. The enhanced XGBoost baseline achieved mean AP 0.544367 and ROC-AUC 0.905089 over three temporal outer folds; GraphSAGE was not promoted after showing no fusion gain. The current local Agent passed 6 of 10 first-case checks, below its frozen 9/10 gate; four invalid outputs were blocked. It is a portfolio system with no production deployment, business-impact or open-ended planning claim. AI-assisted development and personal ownership should be described explicitly.

**Enterprise GraphRAG — a separate simulated IT knowledge assistant.** Implemented source-bound text, two-hop graph and hybrid retrieval with refusal. On a previously used frozen question set, graph and hybrid each passed 18 of 22 answerable questions, compared with 16/22 for text. These are separate project results, not an unseen enterprise benchmark.

## 个人贡献核对表（本人填写后再用于面试）

| 具体能力 | 项目证据 | 本人可复现/解释及AI辅助情况 |
|---|---|---|
| 需求与系统分工 | 工作流/架构与报告职责 | 待本人填写 |
| 数据/模型实验 | 时间切分、消融、三折决定 | 待本人填写 |
| 服务与恢复 | 报告绑定、审批幂等/TTL、恢复 | 待本人填写 |
| 真实评估 | 十笔首次失败与重复分母、预算 | 待本人填写 |
| 仍不理解的实现 | 具体文件/函数与待补知识 | 待本人填写 |

不能写“独立手写全部代码”“已熟练掌握”或“本人已经完成复核”，除非确有相应本人记录。简历是事实草稿，不是代理替本人作出的能力认证。
