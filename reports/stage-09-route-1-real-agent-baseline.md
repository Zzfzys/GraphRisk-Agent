# Stage09 路线1：30笔不同交易的真实本地 Agent 基线

> 2026-10-07；工程实现、冻结和真实观察完成，人工语义复核待用户完成。
> [评分合同](../docs/data/stage-09-real-agent-evaluation.md) · [实现路线](../docs/plans/stage-09-implementation-roadmap.md) · [学习路线](../docs/plans/stage-09-learning-roadmap.md)
> 本轮没有改Prompt、模型、GraphSAGE、重试规则或现有基线配置，没有付费调用。

## 结论和边界

**已补齐30笔不同交易的真实本地调用**，另有3个预注册重复；所有案例有结果，0 not_run，0 fake质量案例。固定 qwen2.5:0.5b / native Ollama 生成，30笔首次全部实际尝试，满足原“至少20笔”的数量门禁。

19笔完整证据报告、10笔明确图不覆盖的部分证据报告、1笔因漏抄证据被阻止发布。程序来源绑定通过29/30，但**原始模型的uncertainty语义字段只有10/30正确**。完整图证据20笔被模型全部写成partial_evidence；原生系统用权威工具状态派生最终报告，没有把错误状态当成事实。这是系统兜底效果，不是LLM完整语义准确率96.7%。

所有可发布报告仅选择固定模拟政策“图关系线索使用”的一个章节，图覆盖选“可用线索”、图不覆盖选“解释限制”，每例只剩一个合格候选。30个真实ID补足了交易多样性，但**不证明多政策选择、自由风险推理或生产欺诈识别能力**。本轮没有无合格政策案例，因此真实正确拒答率不可估计；0正确拒答不代表拒答机制有缺陷。

路线1的工程证据完整，GraphSAGE路线2的基线输入/真实调用/失败归因门禁已具备。计划要求的至少10例人工核对表已生成但没有人类评审提交，完整路线验收仍保留这项待办；AI交叉核对与用户学习不冒充人工完成。

## 固定集合与版本

输入：IEEE验证期88,581笔，显式只读transaction_id和FEATURE_COLUMNS，无欺诈标签。按当前Stage02 champion排序形成等人数三分位，低/中/高各10；不是业务风险阈值。

| 层 | 候选总数 | 图覆盖候选 | 不覆盖候选 | 选覆盖/不覆盖 |
|---|---:|---:|---:|---:|
| 低 | 29,527 | 3,313 | 26,214 | 7/3 |
| 中 | 29,527 | 3,254 | 26,273 | 7/3 |
| 高 | 29,527 | 3,433 | 26,094 | 6/4 |

20覆盖案例全部有严格较早的合法历史图边，历史数量10～19；10不覆盖仍能查询交易、历史特征和XGBoost，没有误报为交易不存在。没有替补或事后换样。只在验证期选择，不访问test标签。

生成前冻结30 ID、工具与政策快照、评分规则、抽核顺序和三个重复（case-01/11/21）。私有目录：[20261007-baseline-v1](generated/stage09/route1/20261007-baseline-v1/)；真实ID/金额/实体/Prompt/输出均gitignored。

- 路线0原161文件核验再次通过，内容SHA256：883f8fc83ff8789f6247e77eb05057bc927d76704e334f9841ef354d21c808d3。
- 路线1新增测量模块/CLI后163文件冻结，内容SHA256：ef06052f0c2de718452f967aea16890ed3c028b01045fdb04dabf0da7b7adefb。
- 案例/评分/快照清单SHA256：f923afb9b8e9bdaf8a9d3af3d071957c25fe3960d68365d0ebff74784035ce4a。
- 模型digest：a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67。
- 主配置configs/stage05.yaml，TF-IDF/内存单worker，政策日期2026-07-01，relation_limit3，Prompt stage05-route5-extractive-v1；GraphSAGE与stage05-route8沿用路线0。
- 原生调用temperature0/512输出Token/180s请求/300s全案，未新增num_ctx或随机seed配置，不声称完全确定性生成。

## 分母、结果和失败

| 指标 | 本轮观察 |
|---|---:|
| 首次不同交易 / not_run | 30 / 0 |
| 首次实际生成交易 / 尝试 | 30 / 30 |
| 固定重复 / 重复生成尝试 | 3 / 3 |
| 全部实际POST预约 / 收到完整真实响应 | 33 / 33 |
| schema合法（全部真实尝试） | 33/33 |
| 首次完整证据（verified） | 19/30 |
| 首次部分证据（partial_evidence） | 10/30 |
| 首次生成/依据失败 | 1/30 |
| 首次程序事实/引用绑定通过 | 29/30 |
| 首次原始draft证据IDs完全复制 | 29/30 |
| 首次原始draft uncertainty正确 | 10/30 |
| 首次同时满足以上两个draft字段 | 10/30 |
| 确定性停止 / 正确拒答 / 超时 / 重试 | 0 / 0 / 0 / 0 |
| 首次待审批 / 重复待审批 | 29 / 3 |
| 已审批 / 未审批外部动作 | 0 / 0 |
| 已观察发布事实/引用安全失败 | 0 |
| 真实机制fixture混入 / embedding / 付费 / 训练 | 0 / 0 / 0 / 0 |
| 全部prompt/output Token | 9,854 / 3,665 |

原始draft字段核对是事后诊断指标，没有重写事前系统来源评分或换样。结构100%和系统绑定96.7%不能掩盖模型uncertainty仅33.3%。未来若优化Prompt/模型，需另立开发集与新版本，再与这份保留基线配对，不修改这轮结果。

**case-22。** 已有完整交易/图证据，模型输出合法JSON但证据列表7项漏掉1项，且uncertainty错误。原生核验给draft_evidence_mismatch / generation_error，没有报告、审批或外部动作；属于质量失败、安全关闭成功。没有为得到好看成绩重试永久依据错误，也没有用另一个交易替换。

三次重复系统状态、去调查UUID后的报告语义均3/3一致；原始JSON字符串仅1/3逐字相同。重复不增加独立交易分母，不由temperature0推断完全确定性。

AI已检查33结果与原始HTTP一致、重新计算全部评分、核对同工具/政策快照、实际API投影的66个较早邻居与实体类型、全部可发布引用和审批绑定。报告中的风险/图概率未校准、实体只是启发式、人工时间锚点、政策仍需人工判断等限制保留。与来源一致并不证明GNN预测准确，也不证明人类语义复核通过。

## 时间、资源与环境故障

首次冷运行case-01约7.205s，其中模型load_duration约4.831s。后续加载中位1.670ms、最大5.065ms。30首次含失败的总延迟中位1.909s/P95 2.032s（小样本，无生产SLA含义）。

| 首次状态 | n | 延迟中位 | P95 |
|---|---:|---:|---:|
| 完整证据 | 19 | 1.953s | 2.576s |
| 部分证据 | 10 | 1.766s | 1.814s |
| 生成/依据失败 | 1 | 1.884s | 1.884s |

首次启动在服务加载后RAM2.959GiB触发3GiB停止线，尚未发送任何生成请求，证据保留于runtime-resources-before-generation-gate-failure.json。Docker当时启动失败；正常docker desktop stop --timeout45后RAM回升5.25GiB，用原冻结集合继续，不改停止线/WSL/磁盘。

正式运行每2秒共33个资源样本：可用RAM最低3.096GiB，GPU空闲最低5325MiB/利用率最高26%，C/D/E空闲最低8.822/41.083/52.665GiB。样本峰值不是连续硬件峰值；GPU运行态自身占用允许。所有新增测量文件留E盘忽略目录，本轮没有下载模型、构建镜像或调整C盘存储。

**收尾Docker仍正常停止**，Ollama保留原服务；需要数据库/容器路线时再按当时资源检查启动，不能把本轮TF-IDF成功当成Docker恢复证据。全阶段生成账本33/144（剩111），其中路线1额度33/66。以后路线5必须复用此账本，不把另一个入口调用当成受此runner约束。

## 测试、复核和个人学习

机制：[test_stage09_evaluation.py](../tests/unit/test_stage09_evaluation.py)新增24项，包含选样去重/不看标签/稳定tie、配额不足、预算跨重启与phase/global、HTTP确实拒绝超预算POST、invalid-schema仍保留usage、Prompt漂移、独立事实/引用/审批核验、重复分母等。与路线0共43 passed。fixture不计入真实质量。

全量离线：**495 passed, 18 skipped, 1 warning，38.82s**。18项真实数据库测试因没有隔离DSN跳过；本轮没有改DB行为，路线0的489/0skipped真库证据为历史记录，不冒充此次运行。离线审计曾误读展示字段，修正后33份核对通过；这不涉及重新生成或改变评分器。

已有基线只读检查：

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest tests/unit/test_stage09_evaluation.py tests/unit/test_stage09_preflight.py -q
uv run --offline python scripts/evaluate_stage09_agent.py check --output-dir reports/generated/stage09/route1/20261007-baseline-v1
uv run --offline python reports/generated/stage09/route1/20261007-baseline-v1/audit_existing.py
~~~

check不调用模型；audit重新核对原始文件/评分/展示。不要为了复核调用旧run重新生成；已完成目录run会跳过，但它仍要求Ollama/资源门禁。输入代码日后正常变化会导致旧冻结核验失败，先记录版本，不静默覆盖哈希。

新实验需要唯一目录，且计入余下预算：

~~~powershell
uv run --offline python scripts/evaluate_stage09_agent.py prepare --output-dir reports/generated/stage09/route1/<新的唯一目录>
uv run --offline python scripts/evaluate_stage09_agent.py run --output-dir reports/generated/stage09/route1/<同一目录>
~~~

L1材料：[人工核对表](generated/stage09/route1/20261007-baseline-v1/human-review.md)。先按固定10例逐项对照snapshot、result、原文与raw draft，填写本人判断/理由/日期；额外看case-08、18、27了解图缺口。不能把AI核对勾成“人类已评审”，也不需要重新推理。完成2h阅读与自测后另记个人学习，不由测试代签。

## 待办与下一步

- [x] 30不同真实交易、30首次生成、3固定重复、0not_run，所有输出/失败保留。
- [x] 冻结与版本核验、真实调用账本、资源停止、程序全案评分和AI来源交叉核对。
- [x] 清楚区分结构/系统兜底/原始LLM语义/质量失败/安全关闭，原始uncertainty问题不隐藏。
- [ ] 至少10案由用户本人或另一位真实评审完成语义核对；个人L1尚未签收。
- [ ] GraphSAGE实现/梯度/两跳传播专项审计，属于下一路线2，尚未开始。
- [ ] 当前小模型原始语义问题与政策多样性缺口移交后续开发/Stage10，不在基线途中调Prompt或更换模型。

基线工程证据完备，可以按计划开始GraphSAGE正确性诊断。路线1整体保留“人工复核待完成”，Stage09整体仍In Progress；没有把负结果或学习待办写成已完成。

后续版本说明（2026-10-07路线2）：GraphSAGE输入校验已修复，旧清单会仅报model.py源码漂移；旧输出与权重保持，20正常验证图新旧logit差异0。请用[路线2新清单/命令](stage-09-route-2-graphsage-audit.md)核对当前版本，原清单不更新。人类复核表仍可直接查看已有文件，不需重新生成。
