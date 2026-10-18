# 路线7：面试练习与20个追问

2026-10-18；题目/答题核对点已准备，**本人演练仍Pending**。先看问题自己回答，再看核对点；用自己的经历补充，不把答案模板当口述完成。配套[求职条目与两段讲稿](stage-10-presentation.md)、[十案复核](stage-10-human-review.md)。

## 75分钟学习安排

先用25分钟画出浏览器→API→有界证据工作流→模型判断/程序核验→人工审批→存储，标出哪些部分没有调用LLM。再用25分钟分别讲Agent/GNN两段，实际计时、写自己的稿子并指出AI辅助与未理解部分。最后25分钟随机挑下面至少六题回答，记下三道答不清的问题和需要阅读的函数。十案逐案复核另计实施时间，建议分两次各五案，记录实际耗时；不声称全部学习在75分钟完成。

## 系统与LLM职责

1. **为什么叫Agent，模型能自由选择工具吗？** 这是应用控制的有界工作流；StateGraph执行固定节点，模型在证据齐备时输出受限字段。不能把draft_stub/verify_stub当成真实模型推理，也不宣称开放式规划。[流程](../../src/graphrisk/agent/workflow.py)、[可靠运行器](../../src/graphrisk/agent/reliability.py)。

2. **哪些内容由LLM产生，哪些由程序产生？** 模型给历史/关系/图可用性、政策引用选择和未知项等字段；程序重算前提、拒绝错误，再从原事实生成报告文字。字段通过不证明模型自由文本推理良好。[QualityReportWorkflow](../../src/graphrisk/agent/quality_reporting.py)、[semantic_issues/verified_analysis](../../src/graphrisk/agent/quality_evidence.py)。

3. **这些字段规则也能算，为什么还要LLM？** 当前任务没有证明LLM不可替代或相对规则的业务增量。它验证了有界生成与核验链路，也暴露6/10不足；后续应把确定性计算与需要语言理解的解释分开，再冻结未见题评估。不能先用规则替换错误再称模型提升。[后续边界](../data/stage-10-agent-card.md)。

4. **为什么有引用仍然不能认定政策适用？** 引文存在只说明出处可定位；实际政策可能需要独立账户数、10分钟卡尝试或授权，当前证据没有测量。当前协议要求not_established，不是LLM已验证业务规则。[证据核验](../../src/graphrisk/agent/quality_evidence.py)、[政策检索](../../src/graphrisk/agent/policy_retrieval.py)。

## 数据、图和模型决定

5. **你怎样避免时间泄漏？** 按时间切分，历史只用严格早于目标的事件，同秒视为同时；训练图不含未来关系。时间正确仍不能证明匿名供应方宽表字段上线时可得，必须保留source_as_received限制。[历史特征](../../src/graphrisk/features/transaction_features.py)、[时间索引](../../src/graphrisk/graph/temporal_index.py)、[数据卡](../data/stage-09-5b-data-card.md)。

6. **设备/邮箱共享说明同一人或团伙吗？** 不说明。identity_key是启发式，缺失键和实体共享都需要谨慎解释；它们只是调查线索，缺少认证身份/授权证据不能下结论。[数据卡](../data/stage-09-5b-data-card.md)。

7. **GraphSAGE究竟实现了什么，如何排除只是空架子？** 两层SAGEConv、非线性/dropout、取目标根表示再分类；维度、根与batch跨图边有校验。结合梯度/两跳审计和真权重重放讨论实现正确性；它们不证明已经调到最优。[模型](../../src/graphrisk/graph/model.py)、[审计](../../reports/stage-09-route-2-graphsage-audit.md)。

8. **为什么不继续硬调GNN追上树？** 三折固定校准规则均alpha=1、增量0，不满足晋级；有限预算下停止是模型决定。树和图输入信息也不完全相同，有边无边/同特征消融回答的是不同问题，不把负结果推成所有GNN无效。[模型卡](../data/stage-09-5b-model-card.md)、[确认](../../reports/stage-09-route-5b-5-confirmation.md)。

9. **AP 0.544、ROC 0.905是什么意思？** AP关注正类排序的precision/recall表现，ROC考察TPR/FPR跨阈值排序；二者都不是固定阈值准确率。这里是三折外层描述性均值，test已观察、分数未校准，不能解释为90.5%交易判对或分数等于欺诈概率。[E01～E03](../data/stage-10-final-claims.md)。

10. **17.7万覆盖与590540数据有什么区别？** 177162是原validation/test的目标评分覆盖；训练行可能只供历史关系，不具备独立评分资格。邻居relation_only的null不能填0，也不能称低风险。[覆盖服务](../../src/graphrisk/graph/coverage_service.py)、[覆盖验收](../../reports/stage-09-route-5b-6-coverage.md)。

## 评估与费用

11. **50/65机制案通过，为什么真模型只有6/10？** fake测试预算/分支/恢复等机制，不能测本地模型理解；当前首次十笔的4个失败必须留在分母，重复2/2不补分。旧5A30/30、10/10也不能挪到增强profile。[E04～E06](../data/stage-10-final-claims.md)。

12. **什么叫“通过”？人工复核能把自动分数改好吗？** 当前自动联合门槛检查来源和受限判断，9/10是预冻结的小样本门禁；本人复核关注事实/引用/未知/实用性，不是欺诈真值，也不替换自动6/10。允许uncertain和not_applicable并保留所有十案。[本人复核合同](stage-10-human-review.md)。

13. **为什么不报P99？中位数代表线上速度吗？** 真实调查n=12不足以支持稳定尾部结论，P99为空；两个100次只读API组与LLM请求分开。调查HTTP和模型HTTP是嵌套测量，不相加；keep_alive=0下不能凭第二次就说warm。[计时/预算](../../src/graphrisk/demo/stage10_measurement.py)、[E08](../data/stage-10-final-claims.md)。

14. **失败/超时是否计费，能重试吗？** Stage10扩展账本绑定旧130/144，真实请求在发送前预留；失败和未知结果也消耗，未知不退款自动重发。现在142/144剩2，不能用新文件重置预算。小时DeepSeek对账不能证明逐请求正式实扣。[AppendLedger](../../src/graphrisk/demo/stage10_measurement.py)、[E09/E12](../data/stage-10-final-claims.md)。

## 服务、恢复与风险

15. **审批是否执行封卡，reviewer_id能证明身份吗？** 审批只记录与run/session/report hash绑定的决定，不调用真实业务处置。当前reviewer未认证；它不是生产鉴权。重复同决定幂等，不同决定/内容冲突拒绝。[审批](../../src/graphrisk/agent/approval.py)、[服务](../../src/graphrisk/serving/investigation_service.py)。

16. **API重启后如何恢复？memory和PostgreSQL一样吗？** memory重启丢状态。PostgreSQL在版本/TTL核验通过后读取派生报告与检查点，不重新生成；结果发布/审批采用多个受控阶段，不把它宣传成模型调用与数据库的一笔原子事务。PG部分证据无审批，不能把memory兼容语义当相同。[Postgres服务](../../src/graphrisk/serving/postgres_service.py)、[时序](../architecture/stage-10-system.md)。

17. **数据库失联、重复创建或过期请求怎么办？** 不静默切内存，不盲重发生成；同幂等键异请求409、未知/未发布503待核对，过期410。实例恢复与备份到新库是不同证据，当前单worker不承诺生产灾备或多worker一致性。[恢复指南](../data/stage-09-operations.md)。

18. **漂移告警是否说明模型准确率下降？** 只说明相对冻结参考的分布变化，需要新标签/时间对齐评估才能判断性能下降。参考/特征/模型绑定不同不能直接比；阈值是项目演练门槛，不是生产公认保证。[漂移实现](../../src/graphrisk/monitoring/drift.py)、[运维报告](../../reports/stage-09-route-7-observability-safety.md)。

## GraphRAG与个人贡献

19. **GraphRAG和政策RAG有什么不同？两跳/混合一定更好吗？** 主系统查模拟风控政策，独立项目处理原创模拟IT资料和两跳关系。冻结既见集graph/hybrid同为18/22，text16/22；混合没有胜出，不能据此证明跨领域泛化。[独立项目](../../projects/enterprise-graphrag/README.md)、[E07](../data/stage-10-final-claims.md)。

20. **用了AI，你真正负责什么，别人如何复现？** 说出本人选题/实验判断、亲自核对的实现和AI完成部分，展示自己的命令/失败笔记；答不出的函数承认待补。路线6证明新检出有缓存安装/工件重放，不证明冷缓存安装或原始数据全量重训；目前工程证据不能代签本人熟练程度。[贡献表](stage-10-presentation.md)、[复现边界](../data/stage-10-clean-reproduction.md)。

## 本人演练记录

记录实际日期、所用提交、两段开始/结束及秒数、自己的讲述、至少六题的回答与三道待补问题。无须录音/录屏；用户已采用图片演示，不额外要求媒体。请把私有笔记另存到路线7证据目录，不覆盖模板。本人记录收到且内容可核对后再更新L7；原Stage09学习、L5A/LB和其他Stage10学习不自动完成。
