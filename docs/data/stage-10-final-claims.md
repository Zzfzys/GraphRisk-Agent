# Stage10 最终公开主张与证据索引

2026-10-18。供最终评估、README和求职条目引用；新增数字必须登记来源与分母。本表的私有路径相对E:/GraphRisk-Agent，公开只发布汇总。SHA256用于绑定字节，不是数字签名或来源认证。当前审计 `reports/generated/stage10/route3/20261018-final-evaluation-v2/audit.json` 逐文件登记完整字节散列；失败v1保留。

| ID | 公开主张/评价单位 | 版本、分母与限制 | 公开来源 | 私有证据及本轮操作 |
|---|---|---|---|---|
| E01 | XGB/GraphSAGE AP、ROC、Top1%/5% | 5B三折每折50196，图每折3种子；先种子指标均值再折均值；不同信息输入 | [三折确认](../../reports/stage-09-route-5b-5-confirmation.md) | stage09/route5b/20261010-confirmation-v1下plan、outer-evaluation、outer-labels；artifacts/stage-09/route5b/20261010-confirmation-v1三折outer-scores；本轮models-recomputed.json重算 |
| E02 | alpha均1，AP增量0，0/3正收益，不晋级 | recipe 645c714f…；decision 09f49369…；固定校准规则，非所有GNN无效 | [模型卡](stage-09-5b-model-card.md) | 同目录fusion-choice、model-decision、outer-evaluation、paired-bootstrap；点估计重算，既有bootstrap区间绑定复用 |
| E03 | 历史test XGB AP/ROC .537789/.903670，图ensemble .168238/.776202 | 88581；历史已见；图先平均分数，不同于E01先平均指标 | [三折确认](../../reports/stage-09-route-5b-5-confirmation.md) | 同目录historical-test、test-labels；artifact historical-test四数组；本轮独立重算 |
| E04 | 当前Agent首次6/10 Fail，重复2/2、4拒绝、unsafe0 | stage09_coverage profile；Stage10 manifest 2861adba…、execution a713aa5b…，首次与重复分开 | [路线2](../../reports/stage-10-route-2-real-measurement.md) | stage10/route2/20261017-real-v2/results、snapshots、prepared；本轮agent-rechecked.json核验12结果并重判 |
| E05 | 历史5A回归30/30、新增10/10 | 旧profile；45不同交易/63生成含开发重复，不能替代E04 | [Stage09总验收](../../reports/stage-09-route-8-acceptance.md) | stage09/route8/20261017-final-v1/audit-evidence-v1/result.json；历史核验结果绑定复用，不称新运行 |
| E06 | 50/50、15/15机制案例 | Stage06/07 fake/故障类；非65个真模型成功 | [Stage06](../../reports/stage-06-route-6-agent-evaluation.md)、[Stage07](../../reports/stage-07-route-6-agent-evaluation.md) | stage01-09-recheck/20261017-v1/stage05-06-mechanisms.json及Stage09验收；历史机制证据，不重测 |
| E07 | GraphRAG text16/22、graph/hybrid18/22；两跳5/8、7/8、7/8 | 30题×3模式90记录，实际生成19/20/21；既见模拟集，text仍用图核验 | [冻结集评估](../../reports/stage-08-route-7-graphrag-evaluation.md) | stage08/route7/frozen-v1的run、summary、90原答案；本轮graphrag-rescore.json重判 |
| E08 | 分层延迟与错误率 | Stage10 real-v2，worker1/concurrency1；n100读API、10工具、5权重、12模型/调查；嵌套计时不相加 | [路线2](../../reports/stage-10-route-2-real-measurement.md) | 同E04 prepared/envelopes/results；本轮重算12模型与调查分位，准备组原样绑定引用 |
| E09 | 历史小时实扣.0283512元，内部.028383元 | 2026-09-30 16～17时UTC+8，40请求；未知逐请求实扣 | [账单](../../reports/stage-08-route-3-deepseek.md) | stage08/route3两CSV、原run，stage08/real-budget.sqlite3；本轮billing-rechecked.json再次离线对账 |
| E10 | 历史六项CI成功、分层1035/1skip，15探针另列 | 0e326b0、run39042905865；非本路线新CI/新质量 | [路线2](../../reports/stage-10-route-2-real-measurement.md) | stage10/route2/20261017-execution-v1/acceptance.json和run-39042905865-attempt-1；保存证据复用 |
| E11 | 590540原数据、413378/88581/88581切分、177162覆盖 | 时间切分；coverage不等于全量独立评分或准确率；人工时间锚/启发式身份 | [数据卡](stage-09-5b-data-card.md)、[覆盖](../../reports/stage-09-route-5b-6-coverage.md) | stage01-09-recheck/20261017-v1/stage01-data.json与real-coverage/acceptance.json；历史核验复用 |
| E12 | 本轮新增生成/真实fit/付费0；142/144剩2 | 旧生成130+Stage10追加12；真实训练账本不变；学习/实用性Pending | [路线3验收](../../reports/stage-10-route-3-final-evaluation.md) | 本轮audit.json前后3账本hash；不自动训练/扩额/使用余额 |

上述stage09/、stage08/、stage10/和stage01-09-recheck/短路径均在reports/generated/下；artifacts/与数据根另已明示。本轮私有claims-bindings.json给出完整路径和散列，历史只引用与新重算明确区分。公共报告中的百分数保留显示精度，重算用未舍入值；不从四舍五入后的表格反推精确增量。
