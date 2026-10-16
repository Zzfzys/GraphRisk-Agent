# Stage08 路线 7：冻结集评估复现与学习

2026-09-30：路线 7 工程验收完成；L7 个人练习仍待本人完成。真实本地 `qwen2.5:0.5b` 在固定 30 问 × 3 模式产生 90 条首次问答观测。主 hybrid 模式可回答 18/22、两跳 7/8、无答案拒答 4/4、注入无外部动作 4/4，达到计划门槛；graph 也为 18/22、7/8；text 为 16/22、5/8。模型可能重复运行结果变化，以下命令用于复现流程，不能保证每次都精确重现 18/22。

## 先区分四类证据

| 证据 | 来自哪里 | 它说明什么 |
|---|---|---|
| 91 项独立 unittest | 假 provider 与小 fixture | 评分器、拒答/引用合同能区分真伪；不是 91 次真实问答 |
| 路线 5 90 条检索观察 | 冻结问答仅做 text/graph/hybrid 检索 | 哪些来源片段和两跳路径有候选；没有生成答案 |
| 路线 6 开发样例 | 不同服务的小规模真实本地问答 | 工作流能发布被核验答案，也会拒绝；未达到泛化证明 |
| 路线 7 冻结 90 条首次观测 | 同一真实本地模型/约束，三模式 | 本报告的回答事实、路径、引用、拒答、时间和 Token 数据 |

30 问在路线 4 冻结、路线 5 用于检索诊断，路线 6 也用过同语料的开发问题。因此路线 7 是**冻结套件的首次完整模型问答记录**，不是模型从未看过的独立泛化测试。没有为提高分数修改题目、答案、语料、索引、上限或模型。`text` 模式的检索是 BM25，但问答依然依据已检索原文中的审核边做关系核验，不是完全移除图知识的实验；比较范围是检索方式。

## 实际复现命令

在 `E:\GraphRisk-Agent` 的 PowerShell 运行。先依照[路线 6 指南](stage-08-graphrag-answering.md)启动本机 Ollama，模型目录为 `E:\ollama`；依照[路线 5 指南](stage-08-graphrag-retrieval.md)准备固定索引。将 `--output-dir` 改成新的、位于冻结 data 目录之外的路径。程序的首次观测文件逐题写入，并附来源版本哈希；中断后仅在代码、锁、索引、语料版本完全一致时允许续跑，已有题目不会再调用模型。校验失败不会覆盖旧记录。

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.evaluate_answers --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/index-first.json --output-dir reports/generated/stage08/route7/my-first-run
```

运行完成时有 `run.json`、`summary.json` 和 90 个 `graphrag-NN-{text,graph,hybrid}.json`。返回码 0 表示主 hybrid 模式达到门槛，3 表示完成观测但未达到门槛，2 表示合同/文件错误。每个记录保留原始候选证据、已发布的核验答案或拒答、步骤、原因、usage 和评分，但不会记录未核验的模型自由文本。数据由 24 篇项目原创模拟文档组成，不含 IEEE-CIS 交易。没有托管模型调用或费用。

独立复核器能读取既有 90 文件、检查每个 SHA256，并从原始结果重新打分，**不调用检索或模型**：

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.rescore_answers --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/index-first.json --run-dir reports/generated/stage08/route7/frozen-v1 --output reports/generated/stage08/route7/my-rescore.json
```

`scored-v2.json` 是正式报告采用的复核产物；`frozen-v1/summary.json` 和每题首次结果保留不动。复核修正了统计定义：合法结构化 JSON 即使关系错绑也算结构格式有效，但不能算事实正确；并增加了引用的版本、权威状态、文件路径与 path_id 独立校验。可回答正确数仍为 text/graph/hybrid 16/18/18。

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -v
uv run --offline pytest -q
```

本轮独立项目 **91 tests / OK**；主项目 **451 passed / 18 skipped / 1 warning**，跳过 18 项 PostgreSQL/pgvector 真库测试，因为没有设置 DSN。本轮未修改主系统服务代码或运行配置。

## 如何读指标

“正确”需要回答实体、关系边顺序、所有必要引用、来源元数据和日期全部吻合；误绑的答案即使提到正确团队也不算正确。无答案四题都拒绝发布，注入四题没有动作或答复；但它们主要由确定性输入门禁挡下，没有把恶意手册段交给模型，所以**不能据此声称模型自身抵御了文档注入**。模型调用以返回 usage 的实际值累计；失败没有 usage 时记录未知。P50/P95 与最大耗时应同时看：首题在三模式都受到冷加载影响，最大约 45～60 秒，P95 却约 1 秒。

图/混合可回答都为 18/22，hybrid 没有比 graph 赢更多问答。text 为 16/22，低两题；优势主要来自三个两跳例子，但也有 text 胜出的例子。语料由作者构造、服务名明确、图关系已经人工录入；分数不能外推到真实企业百万文档、随意别名或未审核关系。结果达到的是**计划中模拟小数据集的工程门槛**。

## L7：90 分钟亲手复盘

1. **20 分钟，先预测。** 打开冻结问题 09 和 14 的问题文本、日期，预期它们需要哪两条边与哪两个来源。不要先看模型结论。
2. **25 分钟，图带来的增量。** 对 graphrag-09 读取 text/graph/hybrid 三个首次 JSON，依次看 `retrieval.chunks`、`retrieval.paths`、`result.audit.steps`、`result.answer.claims`。解释为什么 text 找到第一跳仍拒答，graph/hybrid 如何以两个不同文档完成引用。
3. **25 分钟，文本胜出与模型错误。** 对 graphrag-14 做相同检查。三个模式都有两段所需资料，graph/hybrid 却因 `unverified_or_misbound_relation` 拒答。分清“检索没找到”“模型选错”“程序核验挡住”三种问题；不要把核验拒答记为答对。
4. **20 分钟，安全与时间。** 查 graphrag-01 的约 60 秒首次冷启动、21/22 的冲突背景、27～30 的 `generation_calls`。解释为什么 P95 不暴露唯一最慢案例，以及“注入没有执行动作”尚不证明模型抗注入。给 Stage08 路线 8 的演示稿写出两个局限，不补改问题或答案。

个人学习自测仍由你本人完成。详细的 90 条逐例判定、版本哈希和失败解释见[路线 7 报告](../../reports/stage-08-route-7-graphrag-evaluation.md)。Stage08 路线 8 与路线 3 正式账单对账未结束；GraphSAGE 诊断提醒仍在 Stage08 全部完成之后。
