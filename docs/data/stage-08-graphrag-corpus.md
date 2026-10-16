# Stage08 路线 4：独立语料、图 schema 与冻结问题合同

> v1，2026-09-30。领域为虚构的“星舟”IT 运维组织；所有文档、实体、关系及问题均是项目原创模拟资料。

## 目录与隔离

语料位置：`projects/enterprise-graphrag/data/corpus-v1/`。共 24 份 Markdown：8 份当前服务登记、4 份团队说明、6 份排查手册、4 份变更记录、2 份历史服务登记。58 个片段、22 个实体、40 条有方向的关系、30 个冻结问题。

- `documents/*.md`：可直接阅读的原始来源。JSON 格式的 front matter 写在 `---` 之间，独立包用标准库解析；不引入 YAML 依赖。
- `manifest.json`：文档路径/字节 SHA256、稳定片段 ID、片段文本及 SHA256。
- `graph.json`：显式节点与来源关系。不会运行自动抽取或从风控交易图导入。
- `questions.json`：仅供评估的 30 案、日期、标准实体、路径和必需引用。**禁止送入模型上下文、向量索引或检索候选库。**
- `freeze.json`：以上 27 个文件的字节哈希；其自身 SHA256 进一步固定在独立包校验器中。

独立包无第三方运行依赖，不导入 GraphRisk/Torch/IEEE-CIS/风控政策数据库。`load_corpus()` 只返回文档、片段、节点和边；黄金答案通过单独的 `load_questions()` 接口读取。路线 4 CLI 为验收工具，会同时验证两者；它不是后续问答入口。

所有文件采用 UTF-8/LF；仓库 `.gitattributes` 已固定 Markdown/JSON 换行。SHA256 不作数字签名或真实企业事实证明；它用于发现冻结内容的变化。修改数据后不得重新计算哈希覆盖 v1 以让测试变绿。

## 文档与片段

每篇元数据：doc_id、title、subject_id、version、effective_from、effective_to、authority、supersedes、provenance、license、public_release。来源固定 `project-original-simulated`，许可 `project-original-public-demo`，公开标记 true；正文也声明模拟属性。无真实个人信息、账户、公司制度、生产地址或凭据。

片段以二级标题划分，ID 为 `doc_id#section`。本轮是显式写定的章节切分；路线 5 可在这些稳定片段上索引，不先让模型重切再改变出处。文档内容哈希保存在 manifest，避免把文件自己的哈希写进自身导致循环。

有效期统一左闭右开 `[effective_from,effective_to)`；null 表示无已知截止。日期只接受规范 YYYY-MM-DD，非法日期拒绝。旧版本截止必须等于新版本开始，同一 subject 的文档版本不得重叠；supersedes 必须指向连续版本且主体相同。

## 图 schema

节点字段为 node_id、type、name。节点类型只有 service、team、runbook、change。节点本身是稳定名称记录，不自动证明某个时点存在关系；查询事实必须检查边和来源文档的有效期。

| 关系 | 方向 | 含义与使用边界 |
|---|---|---|
| OWNED_BY | service → team | 正式服务责任归属，只允许 approved 来源 |
| DEPENDS_ON | service → service | 运行依赖，不能自动反向或补全传递边 |
| HAS_RUNBOOK | service → runbook | 排查入口，不证明已经执行 |
| MAINTAINED_BY | runbook → team | 手册维护责任，不等于服务所有权 |
| CHANGES | change → service | 变更关联对象，不自动覆盖服务事实 |
| PROPOSES_OWNER | change → team | 拟交接目标，不能当作正式 OWNED_BY |

每条边必须提供 edge_id、source、relation、target、doc_id、chunk_id、source_quote、effective_from、effective_to。quote 为源片段完整原文；片段必须属于对应文档，关系日期不能超出来源日期，端点类型必须匹配。

校验器除检查 quote 完全一致，还核对本语料约定句式中的主体、谓词、客体顺序。例如“（svc-gateway）直接依赖服务 svc-identity”不支持反向边。这是对受控句式的校验，不宣称通用自然语言关系理解。后续真实语料需要另行设计抽取与审查流程。

OWNED_BY、HAS_RUNBOOK、MAINTAINED_BY 在同源节点上是本集合约定的单值关系，不允许有效期重叠；DEPENDS_ON 可有多个目标。图允许存在依赖环，路线 5 必须以最多两跳与访问去重限制遍历，不能无限扩展。

## 版本与冲突处理

- 轨道订单在 2026-07-01 由应用组切换到平台组；6 月 30 日只能引用旧登记，7 月 1 日只能引用新登记。
- 检索服务在 2026-09-01 由数据组切换到应用组；边界判断同上。
- 库存交接草案拟转交平台组，但尚未批准，正式归属仍为数据组。
- 身份服务转交应用组的提案已取消，正式归属仍为安全组。

两条冲突问题要求同时给出正式登记和草案/取消记录，解释为何提案不能覆盖现行事实；不是仅靠文件日期较新就胜出。图保留 PROPOSES_OWNER 用于追溯提案，但标准答案只沿正式关系判断责任。

## 冻结问题与黄金答案

| 分组 | 数量 | 黄金依据 |
|---|---:|---|
| direct | 8 | 8 个服务的当前正式责任团队，每题一条来源边 |
| two_hop | 8 | 前 4 题服务→依赖服务→团队；后 4 题服务→手册→维护团队，均需两个不同文档 |
| version_conflict | 6 | 四个换版边界＋草案冲突＋已取消提案冲突 |
| no_answer | 4 | 未登记实体、未提供 SLA、早于文档有效期、不存在的直接关系证据 |
| injection | 4 | 伪造恢复结论、伪造引用、要求关闭审计、要求外发密钥 |

22 个可回答问题；4 个无答案及4个注入问题均预期 abstain。注入段落是原文中的不可信数据，不会变成节点指令或被执行。未运行模型，因此本轮只能验证攻击夹具的存在和代码不执行它，不能称“模型抗注入 4/4”。

黄金字段：decision、answer_entities、evidence_paths、required_chunks、reason。未来回答仍需要自然语言事实核验；不能只输出实体列表就宣称自由文本正确。两跳问题要求路径相连、恰好两条合法关系、两份不同来源且全在题目日期有效；所有路径边都必须被 required_chunks 覆盖。没有来源的事实不能通过凭常识补齐。

问题和黄金依据由项目编写者在 AI 协助下按显式来源规则编制，没有调用待评的 Ollama/DeepSeek 模型生成标准答案，也没有先看模型成绩再改题。语料并非真实企业数据，不能将模拟样本的通过率包装成线上能力。

四个无答案项的审查理由：语料节点中没有月港推荐服务；所有文档都未承诺订单 SLA 数值；全部资料开始于 2026-01-01 或更晚；网关的直接依赖记录是身份服务，没有网关→检索服务这一关系。缺少该边的证据不等于证明现实中不存在该依赖。

## 验证入口

从 `E:\GraphRisk-Agent` 运行：
```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.corpus --dataset projects/enterprise-graphrag/data/corpus-v1
uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -v
```

可加 `--output reports/generated/stage08/route4/自定义新文件名.json` 保存证据；现有文件不覆盖，输出不能写入冻结语料目录。两次验证报告应字节相同，因为报告没有运行时随机 ID 或时间。代码、数据、哈希不匹配时非零退出并给出错误码。

根项目回归继续使用 `uv run --offline pytest -q`。子项目用标准库 unittest，避免为了语料校验给独立环境添加框架；自己的 pyproject/uv.lock 不变。语料目前作为仓库的显式外部数据目录传入，尚未宣称 wheel 自动包含数据或已经完成安装后的问答演示。

## L4 学习练习（30～60 分钟）

1. 打开 service-gateway-v1 的 dependency 段，再打开 service-identity-v1 的 owner 段；自己画出“网关→身份服务→安全组”，给两条边分别写出处。
2. 对照 runbook-gateway-v1 与 service-notify-v1：通知服务由应用组负责，而所用手册由平台组维护。说明为什么二者不能混为一谈。
3. 手算 6 月 30 日和 7 月 1 日订单归属，再说明为何不能引用已经到期的旧文档。
4. 阅读库存交接草案和身份取消记录，找出阻止提案升级为事实的原句。
5. 阅读一条不可信附录，说明“原文确实包含这个命令”与“系统应该执行命令”的区别。

写下自己的理解，再运行测试；个人学习不由工程测试代签。路线 5 才建立文本/向量与两跳检索，路线 6 才产生模型答案。
