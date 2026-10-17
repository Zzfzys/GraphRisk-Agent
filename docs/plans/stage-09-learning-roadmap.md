# Stage 09 学习路线：真实评估、GraphSAGE 诊断与质量工程

> 状态：学习规划已撰写；2026-10-15路线5B-0～7实际材料就绪，LB0～LB7本人Pending；L0～L8/L5A及LB本人完成状态独立记录。

2026-10-10 LB5实际材料已齐：[150分钟确认练习](../data/stage-09-confirmation.md)、[完整真实验收](../../reports/stage-09-route-5b-5-confirmation.md)。九图/三树真实fit、三折150,588外层与88,581历史test、16次完整权重重放通过；XGB平均ROC0.905089/AP0.544367，三个alpha1、增量0、GraphSAGE不晋级。现在可完成网格手算、全部种子/最差折/CI/曲线、冻结决定与只读核验五次30分钟练习；LB5本人仍Pending，新LLM/付费0。
> 配套：[阶段总纲](stage-09-quality-operations.md) · [实现路线](stage-09-implementation-roadmap.md)
> 原15.5h；路线5B追加18h，修订33.5h已包含在112h中；每天30～60分钟，长练习分多天

## 学习方式

先写预测，读当前契约/代码，亲手做最小例子，对照实际结果，再用自己的话解释。执行代理说明改了哪些文件、为什么、怎么验证；你负责核对原理和复现，测试不能代签学习。

笔记六栏：日期/路线、预测、读过文件、命令与版本、结果/失败、我的解释/疑问。私人笔记可放 `reports/generated/stage09/learning/`；公开只放脱敏汇总，不贴密钥/DSN/原始交易/私有 Prompt。待实现入口由路线完成后提供准确命令。

| 学习项 | 对应路线 | 时间 | 学完能做什么 |
|---|---|---:|---|
| L0 版本与证据 | 0 | 0.75h | 找到实际模型 |
| L1 真实多交易评估 | 1 | 2h | 解释分母、失败、语义核验 |
| L2 GraphSAGE 实现 | 2 | 1h | 手算两跳与根预测 |
| L3 采样与公平对照 | 3 | 1h | 发现泄漏/混杂 |
| L4 消融与训练诊断 | 4 | 1.5h | 读曲线与模型决策 |
| L5 同集重放 | 5 | 1.25h | 区分模型变化/LLM波动 |
| L5A Agent质量改进 | 5A | 2.5h | 核对数值、缺口与模型原始判断 |
| LB0～LB7 强基线/图增量/覆盖 | 5B | 18h | 解释输入信息、公平实验、晋级/退出和可用率 |
| L6 CI 与分层测试 | 6 | 1.5h | 解释自动化边界 |
| L7 追踪与恢复 | 7 | 1.5h | 定位问题、核对恢复 |
| L8 个人复现与表达 | 8 | 2.5h | 独立展示与诚实讲指标 |
| 合计 | | 33.5h | 约 30% 学习 |

## 路线5B新增学习（2026-10-09，待本人完成）

完整练习见[5B学习路线](stage-09-route-5b-learning.md)，对应[5B实施路线](stage-09-route-5b-model-enhancement.md)。LB0～LB7依次学习：实验合同与时间用途、原始特征与泄漏、增强XGBoost与公平对照、规模/参数/漂移、图消融与条件增强、融合与三折三种子、held-out/OOF/prospective覆盖、独立复现与面试表达。

路线5B-0～5已有实际材料。LB3读[九次扩量/调参](../../reports/stage-09-route-5b-3-graph-expansion.md)，LB4读[五组消融/增强](../../reports/stage-09-route-5b-4-conditional-graph.md)，LB5读[三折确认](../../reports/stage-09-route-5b-5-confirmation.md)。路线4单折c4/c5关系AP差+0.017504只作探索；路线5最终相对强XGB的简单融合增量0，不晋级。共享38尝试旧失败保留，历史test已封存，不依据外层/test再调参。覆盖与服务留5B-6；LB0～LB7本人练习均待完成。

用户确认基础ROC-AUC最低目标0.80，0.90探索；路线5三折平均0.905089达标，但GNN增量仍失败。原L0～L5A材料和成绩保留，新学习不代替原人工复核，也不把工程Done当本人学完。LB5使用报告中的准确只读CLI；LB6～LB7入口随对应实施补齐。

## L0：实际版本与历史指标（45 分钟）

**实现对应。** 建立 checkpoint/图/政策/Prompt/LLM 版本清单，冻结用途与预算。

**先读。** [恢复验收](../../reports/stage-01-08-docker-recheck-20261007.md)、`configs/graph_ieee_cis.yaml`、`configs/stage05.yaml`、实际 checkpoint metadata；对照[早期训练报告](../../reports/stage-04-route-3-training.md)。

**亲手练习。** 抄出早期与当前 batch/epoch/patience/最佳与停止轮数/PR-AUC，标来源/哈希；画“配置→manifest/encoder→checkpoint→comparison→服务”。列 train/validation/test 与 Agent 案例用途。

**自测。** 修改 YAML 会改变已有权重吗？同名版本能否是不同次训练？看过完整 test 后重切分为什么不自动成为盲测？470/470 不能证明什么？

**交付。** 版本链与三句话边界说明；实施后以当期合同核对，不假设 metadata 永远不变。

## L1：真实 Agent 的分母、失败与依据（120 分钟）

**实现对应。** 冻结 30 笔不同交易，顺序实测当前本地模型，核对全部结果。

**先读。** `scripts/evaluate_stage06_agent.py`、`demo/stage06_evaluation.py` 的 fake/real 分支，`agent/reporting.py` 的 schema/依据核验；实施后读新评分合同。

**亲手练习。**

1. 纸面 10 笔：6 正确完成、2 schema错、1超时、1依据不足正确停止，再给其中2笔各重试一次；算不同交易、首次结果、生成尝试、成功/正确停止率。schema按实际生成分母，端到端按全部案例。
2. 真实基线选充分、图不覆盖/无历史、模型失败各一案，核对事实、政策日期/版本/原文/条件；说明 verified 实际检查什么。
3. 为“共享设备多→已确认欺诈”列缺失依据；解释不确定/拒答何时正确，不能统一算成功或失败。
4. 三个预定重复样例比较输出/时间，区别稳定性与不同交易覆盖。

**自测。** 50fake+同交易3次为什么不是53笔真实质量成绩？欺诈标签为何不是报告解释真值？安全关闭与质量失败怎样分别记录？

**交付。** 分母表、三份脱敏评分解释与错误归因。先保留基线再诊断图，不边测边改 Prompt。

## L2：两层 GraphSAGE 与训练正确性（60 分钟）

**实现对应。** 审计边、根/batch、梯度、train/eval 与时间特征。

**先读。** [本轮手算讲义](../data/stage-09-graphsage-audit.md)、[审计报告](../../reports/stage-09-route-2-graphsage-audit.md)、[图schema](../data/stage-04-graph-schema.md)、`graph/model.py`、`schema.py`、`train_graphsage.py`，结合小 fixture。

**亲手练习。** 画 T0→E0→历史T1→E1→更远T2，标反向边和两层更新；手算均值邻居聚合，说明 T1 可两跳影响 T0，距根四跳的 T2 不能。固定有效权重扰动 T1，预测 logit；核对两张 batch 图的根索引。

**自测。** 实体只有类型特征为何仍能中转历史信息？ReLU/零权重为何可令一次扰动不变？BCEWithLogitsLoss 为什么用 logit，pos_weight 为什么不是校准？

**交付。** 消息图、手算/fixture对比、训练更新说明；小图过拟合不证明真实准确率。

## L3：采样、时间与公平基线（60 分钟）

**实现对应。** 统计局部图，同ID/同根特征比较 MLP/XGBoost/GNN。

**先读。** `graph/temporal_index.py`、`build_artifacts.py`、`schema.py`，[图产物契约](../data/stage-04-graph-artifacts.md)与实际采样表。

**亲手练习。** 选一笔有历史交易，列各实体可见历史、同时间排除、最多五笔选择。解释项目 fanout 实际映射，不套标准 NeighborLoader。比较 full-data XGBoost 与50k GNN，列三种混杂，用公平表控制。

**自测。** 实体共享为何不是真实同客户？较早 validation事件作推理上下文是否一定泄漏？历史特征按目标时间重算为何危险？改采样为何新建manifest？

**交付。** 局部图追踪与公平表；解释无边/冷启动比例，不把缺边直接称模型缺陷。

## L4：消融、早停与有限调参（90 分钟）

**实现对应。** 固定消融、至多6候选、三个种子，验证选型。

**先读。** [自助训练指南](../guides/graphsage-self-training.md)、当前 history 与训练代码、路线4实验表。指南默认命令会读test，不用于候选搜索；先理解已有 validation-only/run-name。

**亲手练习。**

1. 画loss/验证PR-AUC，标最佳/停止轮，列欠拟合、过拟合、时间迁移所需证据。
2. 预测完整/去边/扰乱/去历史统计，对照实际；区别重训消融和旧权重删边敏感性。
3. 连正例率解释PR-AUC，核对实际 AP 或积分公式；看 ROC-AUC/Top-K，不能只用accuracy/0.5阈值。
4. 比三个种子与早晚验证，配置冻结后看一次测试复核，写测试已观察限制。

**自测。** 多epoch为何不证明训练充分？为什么不反复看test直到赢？没增益时如何决定保留图证据但不用GNN默认评分？

**交付。** 曲线解释、假设/结果表、模型决策段落，限定本数据/预算，不宣称“完全体”或最优上限。

## L5：同集重放与版本切换（75 分钟）

**实现对应。** 模型决策接入，LLM/Prompt/政策固定重放30笔。

**先读。** `graph/service.py`、`compare_models.py`、`agent/components.py` 与配对报告。

**亲手练习。** 找三对前后报告，比较分数/来源、关系、政策、引用、状态/耗时；解释模型确定变化和生成波动。观察受控错配拒绝与回滚，不覆盖旧权重。

**自测。** 同时改GNN/Prompt/LLM能归因吗？XGB单分数能说融合吗？反复同集为何不代表独立泛化？

**交付。** 三对归因与回滚解释；LLM修复另立实验。

## L6：分层测试与 CI（90 分钟）

**实现对应。** CPU/fake、真库与边界自动化，真实本地另留证据。

**先读。** `pyproject.toml`、真库skip条件、`scripts/evaluate_stage07_contract.py`；实施后读workflow/测试策略。

**亲手练习。** 标注时间fixture、图CPU、fakeAgent、真DB、真实LLM、CUDA六类的依赖。解释锁定安装/缓存miss与offline测试；制造一个小合同错误观察失败再恢复，不推密钥。

**自测。** 新runner不能全offline的原因？18skipped和passed区别？测试embedding不证明真实质量？只有YAML为何不算远程通过？

**交付。** 依赖表、失败/修复记录，实际远程执行后才写run URL；否则明确本地证据。

## L7：追踪、漂移、安全与恢复（90 分钟）

2026-10-17材料就绪、本人Pending：[90分钟准确命令与三次练习](../data/stage-09-operations.md)、[实际验收](../../reports/stage-09-route-7-observability-safety.md)。每次30分钟依次读事件链、解释历史/合成漂移、只读核对实际恢复与备份，不启动真实模型。

**实现对应。** 字段补齐、基础漂移、故障/隔离备份恢复。

**先读。** [追踪契约](../data/stage-07-request-trace.md)、[持久合同](../data/stage-07-persistence-contract.md)、`serving/request_trace.py`。

**亲手练习。** 按request/run/attempt追模型超时与DB不可用，列节点/耗时/重试/终态；用标为合成的漂移解释变化，比较卷down/up与备份恢复新库的哈希/审批。

**自测。** 依据不足与schema错误为何不同？拒答升高一定退化吗？漂移能证明准确率下降吗？演示reviewer_id为何不是鉴权？

**交付。** 两张事件链、漂移限制与恢复表，不截图秘密/DSN。

## L8：个人复现与面试表达（150 分钟）

2026-10-17实际材料已齐：[准确命令与150分钟练习](../data/stage-09-final-reproduction.md)、[本人复核模板](../data/stage-09-personal-review.md)、[本地总验收](../../reports/stage-09-route-8-acceptance.md)。本人Pending，不由代理代签；历史回放/模拟机制/真实质量分别解释。

**实现对应。** 总验收与交接；本人亲手核对。

**先读。** 当期总验收、模型决策、基线/重放和准确命令，不能拿Stage08日志当本期成绩。

**亲手练习。**

1. 45分钟：新PowerShell初始化、检查版本与服务，亲手调查2～3个不同固定交易（含不足/拒答），人工拒绝与刷新读回，记脱敏状态/错误/版本。
2. 30分钟：一个CPU回归与版本/时间检查，说明期望和失败。
3. 30分钟：解释一项负实验或真实生成错误，写限制/下一步。
4. 30分钟：Agent和GNN各录90秒，核对报告后重讲。
5. 15分钟：剩余知识点/问题与Stage10计划。

**表达结构。** Agent：任务/工具/审批边界→30笔不同交易与真实分母→首轮失败/评分→变化与限制。GNN：时间图/两跳→同特征对照/消融/预算→指标/模型选择→已观察test/未知。数字实施后从报告填写，不预填成功。

**自测/交付。** 本人操作、两段讲述与待补清单；能解释先测基线、fake/真实、图差距与生产边界，讲不清返回对应L，不代签。

## 个人自测与材料更新

| 项 | 当期材料 | 本人完成 | 记录 |
|---|---|---|---|
| L0 | [实验合同](../data/stage-09-experiment-contract.md)与[门禁报告](../../reports/stage-09-route-0-preflight.md)就绪 | [ ] | 本人待填 |
| L1 | 规划就绪；评分/样本待路线1 | [ ] | 待填 |
| L2 | 规划就绪；审计待路线2 | [ ] | 待填 |
| L3 | 规划就绪；对照待路线3 | [ ] | 待填 |
| L4 | 真实12训练/决策、90分钟讲义就绪 | [ ] | 本人待填 |
| L5 | 规划就绪；配对待路线5 | [ ] | 待填 |
| L6 | 本地分层/CI合同与90分钟材料已齐；远程Pending | [ ] | 本人待填 |
| L7 | 运行字段、历史统计/漂移、实际恢复和90分钟操作讲义就绪 | [ ] | 本人Pending |
| L8 | 准确启动/停止/核验命令、150分钟任务与本人复核模板就绪 | [ ] | 本人Pending |

每条实现完成补充真实命令、文件与预期；模型/评分/范围改变同步练习。只有本人操作与自己的解释能作为完成依据。

## L0 当期实作材料（2026-10-07）

路线0工程通过，本人自测不代签。运行下面两命令，分别预期19 passed、status passed/checked_files161：

    . .\scripts\enter_project_env.ps1
    uv run --offline pytest tests/unit/test_stage09_preflight.py -q
    uv run --offline python scripts/freeze_stage09.py --check reports/generated/stage09/route0/20261007T093634Z/frozen-inputs.json

读metadata与frozen-inputs的bindings，只抄公开参数/哈希，不复制受限ID。解释当前0.085999和早期0.103733的来源，配置修改为何不改已有权重；说明471/18skipped与489/0区别，主TF-IDF ready不代表真实生成正确。解释3GiB起始线为何不能保证WSL/并发稳定，记录Docker当前C与旧D磁盘限制。案例/真实分母由路线1建立。

## L1材料已就绪（2026-10-07，个人学习待完成）

先读[路线1评分合同](../data/stage-09-real-agent-evaluation.md)和[实测报告](../../reports/stage-09-route-1-real-agent-baseline.md)，再打开本机 reports/generated/stage09/route1/20261007-baseline-v1/human-review.md，按固定10例填写事实/引用、raw draft与最终报告区别、审批/动作和理由。额外查看case-08/18/27图边界。2h可拆四次30min：预测分母→读原生reporting/评分→核对已有输出→解释失败与重复。30首次+3重复不是33独立交易；33/33 schema、29/30来源绑定和10/30 uncertainty可以同时成立。case-22合法JSON漏证据应判质量失败但安全关闭。check和离线audit无需新推理，命令见报告；API/Prompt/GraphSAGE都没有变。AI核对材料不能替代人类评审或本人完成学习。

## L2材料已就绪（2026-10-07，本人练习待完成）

[讲义](../data/stage-09-graphsage-audit.md)含双向两跳图、手算3→4/四跳不变、0.5梯度、batch根偏移、logit/BCE/pos_weight、最佳权重clone和历史自身截止。先自己预测再运行20项新测试，按两次30分钟填写学习记录；工程515通过不代表本人完成L2，295节点复算和20图敏感性不证明参数最优。旧基线check因已登记model.py修复会报漂移，使用路线2新清单核验，不覆写原hash。下一L3读真实采样与公平比较，L4再调参。


## L3材料已就绪（2026-10-07，本人练习待完成）

先读[采样/时间/公平对照讲义](../data/stage-09-sampling-baselines.md)与[实际结果](../../reports/stage-09-route-3-sampling-baselines.md)。两次30分钟分别追踪实体cap/交易去重/同时间排除，解释28维根矩阵、业务特征与全训练期差别。用只读check重载验证，勿重复train消耗已用完的一次额度。532项通过不代签学习；GNN比MLP高0.010517 AP但ROC略低且预算/架构不同，路线4才重训消融。服务ID覆盖与局部图有历史不是同一分母；100%验证有历史不证明模型训练充分。


2026-10-07 L4材料就绪：先读[真实消融/调参报告](../../reports/stage-09-route-4-graphsage-diagnosis.md)与[90分钟讲义](../data/stage-09-graphsage-diagnosis.md)。运行只读check核对模型和指标，勿重复训练/源测试读取；本人写曲线解释、消融预测偏差和三种子决策，不能用工程Done代签。


2026-10-08 路线5进行中：显式 configs/stage09.yaml 绑定路线4 no-promotion 决策，Stage02 XGBoost 主评分、candidate-4 实验性图分数、无融合；旧 configs/stage05.yaml 保留回滚。正在验证错配/审批/展示边界与30+3固定同集重放；此记录不是验收通过，L5本人练习待完成。


2026-10-08 路线5工程完成：显式stage09配置绑定验证决策，XGBoost主评分/candidate-4实验性图分数/无融合；30不同交易+3固定重复共33真实本地调用，18完整/10部分/2依据漏抄关闭，程序来源绑定28/30、原始uncertainty正确10/30，不能当推理准确率。30主评分不变，20图分数/14邻居顺序改变；10未覆盖保留。真实API错哈希409/拒绝200/动作0，记录真实投影AppTest与旧配置30工具快照回滚通过，追加生成0。全阶段66/144，新增训练/付费0；根586 passed, 18 skipped, 1 warning in 48.01s（18缺DSN），新增29机制测试。L5本人学习与路线1人类复核待完成，路线6～8未执行、Stage09仍In Progress。见[路线5报告](../../reports/stage-09-route-5-agent-replay.md)与[75分钟讲义](../data/stage-09-agent-replay.md)。


## 2026-10-08 路线 5A 插入与工程完成

按用户要求在路线5与6之间增加[Agent质量改进](stage-09-route-5a-agent-quality.md)，配套[学习路线](stage-09-route-5a-learning.md)。执行顺序5→5A→6，路线6须等待5A工程验收；旧路线5原始成绩不覆盖。主动工作追加8h（学习2.5h），阶段总计52h/学习15.5h。原全局144次预算不增加，本轮最多66次本地生成，不添加训练、下载或付费调用。

2026-10-15 LB6材料已齐：[路线6工程验收及操作](../../reports/stage-09-route-5b-6-coverage.md)、[五次30分钟练习](stage-09-route-5b-learning.md)。亲自比较覆盖分母、relation_only、时区特征一致性和只读核验，学习完成须由本人确认。

2026-10-15 LB7实际材料就绪：[5B端到端总验收](../../reports/stage-09-route-5b-7-acceptance.md)、[三次30分钟个人模板](../data/stage-09-5b-review-template.md)、[模型卡](../data/stage-09-5b-model-card.md)、[数据卡](../data/stage-09-5b-data-card.md)。默认只读入口scripts/check_stage09_5b.py；完整806通过、1旧skip、新增核验器11通过及24真库只证明对应工程合同，不增加真实LLM质量。本人完成和讲稿仍Pending。


## 2026-10-16 L6材料就绪，本人仍Pending

先读[分层合同与准确复现命令](../data/stage-09-ci.md)和[实际验收报告](../../reports/stage-09-route-6-ci.md)。Windows/Linux各815/1平台skip；真库24与15机制探针、独立GraphRAG91+3、两个wheel/新镜像和47文件Git往返通过。远程无run URL，仍Pending；这些材料不代签学习，也不增加真实模型质量样本。

三次各30分钟：①列六类依赖与真假模型分母；②跑14项CI合同测试、静态/Git字节检查，解释网络隔离/anyio与skip失败；③跑CPU，Docker就绪时观察真库24和15各自账号，核对公开产物白名单，并解释为什么本地容器通过不能填写GitHub Actions通过。完成自己的依赖表和失败/修复记录后再勾L6。


## 2026-10-16：本地 Git 版本收口完成，远程 CI 仍 Pending

[版本收口报告](../../reports/stage-09-git-closeout.md)：已从 bfbdc40 建立 `codex/stage09-version-closeout`，集成提交 `cc1da7dc92779c5260ea64721b313c8a52d43120` 保存 553 份工程文件，最终文档另作提交，最终 HEAD 与可恢复 Git bundle 哈希见本轮私有 acceptance。排除了秘密、交易原始数据、权重、缓存和本地助手状态；C 盘原始字节快照与 E 盘旧源码另存，E 盘 main 的 317 行原工作区状态不变。

从集成提交独立干净检出后，278 Python AST、47 冻结文件字节往返、Windows CPU 815 passed/1 符号链接权限 skip、真库 24 零 skip 及 15 受限账号机制探针、GraphRAG 91+3、两个 wheel 通过。复用锁定的 E 盘环境，源码导入来自检出；不宣称冷缓存安装或本轮新镜像验证。Git 行尾规范化后的检出指纹 b1009e… 与原始字节 a80fccc… 分开记录；最终文档提交不改变已测运行代码树。

无 Git 远端、无推送、无远程 run URL；下一步是核对远端和公开边界并补路线 6 远程证据。原路线 7/8 及后续 GNN 检索仍未启动。两个账本不变，原路线 6 的 435 份清单核对通过；新增真实训练/LLM/费用均 0，GNN 不晋级和本人学习 Pending 不变，Stage 09 整体仍 In Progress。

本轮 Git 学习材料已齐，本人未代签：用 30 分钟亲自观察 `git log -2 --oneline`、`git status` 和分支，解释“本地提交、bundle 备份、远端推送、CI 运行”四者的区别，并按报告将 bundle 恢复到新目录后核对 HEAD；不要用 reset/clean 覆盖现有 E 盘旧工作区。


## 2026-10-17：原路线 6 真实 GitHub CI 工程验收通过

公开仓库为 [Zzfzys/GraphRisk-Agent](https://github.com/Zzfzys/GraphRisk-Agent)，分支 `codex/stage09-version-closeout`。[成功运行 39015632664](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39015632664) 对应提交 `72c5078ac09849b1ca73b3ae3101389f6d99bea9`，六个任务全部 success：静态 278/冻结字节 47、CPU **816 passed / 1 平台 skip**、隔离真库 **24 passed / 0 skip** 与 **15/15** 受限账号机制探针、独立 GraphRAG **91+3**、两个 wheel 和实际无私有输入镜像构建/无网络 smoke。CPU 的 Linux skip 是 E 盘专用目录保护，不是缺 DSN；不同层分开计数，不冒充一次全量 pytest。

[首轮 39014968083](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39014968083) 的两个 GraphRAG 启动失败保留。原因是相对解释器路径在子进程换目录后失效；已修复为相对调用者定位的绝对路径，并保留 virtualenv 符号链接。15 项 CI 契约及 91+3 独立本地回归通过，云端再次全绿。网站域名连接重置/超时后，通过 GitHub Git Data API 上传相同 Git 对象，逐 blob/tree/commit 核对 SHA，非强制更新分支；没有改写历史或系统网络。

收尾进一步显式配置 setup-uv 的 `cache-local-path`，避免 action 用 runner-temp 默认值覆盖仓库内缓存要求，并在现有 workflow 契约中断言。收尾提交也触发完整 CI；其最终 HEAD、run URL、任务日志和摘要哈希另存 `E:/GraphRisk-Agent/reports/generated/stage09/route6/20261017-github-ci-v1/acceptance.json`，不能把上面的 72c5078 运行误记到另一提交。

原路线6工程 Done，L6本人学习及人工复核独立 Pending；Stage09整体仍 In Progress，下一步等用户启动原路线7可观测/漂移/新库备份恢复，原路线8与后续GNN检索未启动。新真实数据fit/真实LLM/付费调用均0，两个旧账本和GNN不晋级决定保留；CI不是新增真实Agent质量成绩，也不代表生产部署验收。

L6继续按原90分钟预算亲自练习：①打开成功与首轮失败运行，找到两个GraphRAG任务的解释器启动错误，对照相对路径/工作目录/虚拟环境sys.prefix；②运行15项CI契约测试，读新建无pip虚拟环境的实际子进程回归，说明为何不能resolve掉Linux解释器符号链接；③在成功Actions的Artifacts中查看CPU、真库、核心/界面、包各自public-summary，列816/1、24/0、15/15、91/3各自分母，并解释CI夹具不增加真实模型质量样本。完成自己的笔记后再确认L6，不由代理代签。


## 2026-10-17：原路线7已启动，尚未验收

用户授权进入可观测性、漂移与安全恢复；[本路线报告](../../reports/stage-09-route-7-observability-safety.md)和configs/stage09_operations.json先登记合同。复用既有追踪、只读历史统计与冻结同模型漂移参考；故障/备份恢复限新隔离容器和另一个新库。训练、真实LLM、付费调用0，旧预算/负实验/报告不覆盖；L7本人Pending，原路线8及后续GNN检索未启动。


## 2026-10-17：原路线7本地工程验收通过，学习独立Pending

[路线7验收](../../reports/stage-09-route-7-observability-safety.md)与[操作/90分钟学习说明](../data/stage-09-operations.md)已就绪。白名单request/run/attempt和版本哈希日志、幂等计数、资源/超时分类、历史同集30笔分层统计、冻结历史漂移已实现；真实DB停机503/恢复200，pg_dump→另一个新空库pg_restore核对12表88行，待审续接/终态/幂等/预算/检查点/pgvector与受限角色通过，15/15故障恢复探针、6 canary零命中。本轮随机容器/匿名卷及密码文件已清理，私有备份保留至路线8人工复核后由用户决定。

最终本地839 CPU/1旧平台skip、26真库零skip与既有15受限探针、287AST/47冻结字节/两个wheel通过；旧530闭包、confirmation/177162覆盖行及两总账哈希不变，新真实fit/LLM/付费0。历史评分29/30→28/30仅复述、不宣称改善；历史三列PSI低于预设门槛，合成25%缺失等扰动另列，不推断准确率变化；冷/热未知。失败轮次保留，完整新代码/远程run以本轮私有acceptance绑定为准。

Stage09仍In Progress；L7本人Pending，原路线8及后续关系模式检索未启动。单worker、未认证reviewer_id、无实际封禁；生产鉴权、多worker、加密异地/全实例DR和生产RTO/RPO不在已验收范围。当前C盘71df源码、E盘环境/证据边界继续有效，不恢复使用E盘旧源码。


## 2026-10-17：原路线8已启动，验收进行中

已冻结stage09_final.json，开始核验历史真实质量/模型结论、当前API和工作台、适用的分层回归与个人复现交付。真实训练/生成/付费0，旧账本与负结果保留；本人学习/人工复核Pending，后续GNN检索及Stage10未启动。


## 2026-10-17：原路线8本地工程总验收通过，个人与质量限制保留

[原路线8总验收](../../reports/stage-09-route-8-acceptance.md)和[个人复现/150分钟学习](../data/stage-09-final-reproduction.md)、[本人模板](../data/stage-09-personal-review.md)交付。648历史绑定与原始评分/HTTP/选择/130调用重核通过，当前API真实HTTP+Streamlit AppTest回放两份不同真实历史报告，完整案拒绝/刷新/重复200/冲突409、图不足案无SQL审批及分数不补零、新API实例恢复无生成通过。新库只操作本轮lease，演练容器/匿名卷/密码文件清理，路线7备份仍私有保留。

最终本地851 CPU/1旧平台skip、26真库零skip、50/15模拟机制、290AST/47冻结往返/两个wheel通过；Streamlit入口和根conftest纳入源码指纹。路线7后端哈希不变、备份/12表88行/15恢复探针重新核对后复用；旧5B530闭包与两账本不变，新增真实训练/LLM/付费0。最终Git提交、推送及本提交自己的远程run单独保存 E:/GraphRisk-Agent/reports/generated/stage09/route8/20261017-final-v1/acceptance.json，不以旧CI替代。

工程Done；Stage09整体In Progress，L0～L8/LB及人工实用性待本人完成。5A的30回归/10新增受限判断复核通过，但当前5B增强配置没有新真实Agent质量成绩；GNN增量0、不晋级。原路线6～8工程已收口，GNN关系模式检索等用户另行启动，Stage10仍Planned；不增加生产承诺或抹去历史失败。
