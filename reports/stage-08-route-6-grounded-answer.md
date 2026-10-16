# Stage08 路线 6：有界 GraphRAG 问答、引用核验与拒答验收

2026-09-30：**路线 6 工程合同验收完成；模型质量未全面验收。** 最后一轮 4 个可回答开发样例中真实本地回答通过 2 个，另外 2 个被拒绝发布；4 个应拒答分支全部安全停止。Stage08 保持 In Progress，不能把本轮写成 30 问通过或正式模型质量达标。

## 已交付行为

- 独立标准库 `answering.py`：parse → retrieve → screen → generate → verify → finish，固定步骤、检索、生成、时间、Token 和费用边界。无新增依赖、无自动动作、无托管调用。
- 只接受已检索且当日有效的片段、带原文的关系和完整路径。缺第二跳、方向/端点错绑、伪造/重复/漏引用、未解决的交接冲突均拒绝。text 模式也只从实际返回片段对应的已审核边验证关系，不能补入未检索来源。
- 模型输出只有 decision / edge_ids / citation_chunk_ids / reason；核验后用事实模板渲染，原子关系逐条绑定原文引用。模型自由文本不会成为用户答案；本产品明确是受限关系问答与证据选择，不是通用生成式知识问答。
- 模型 API 使用其原生对话模板（raw=false），固定 qwen2.5:0.5b；不更换模型、不放宽预算。发布前再次检查冻结来源。正常审计只记录哈希、用量、步骤和稳定错误码，不返回未验证草稿。

## 版本与固定预算

- 生成模型：`qwen2.5:0.5b`；digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`。
- 问答合同 SHA256：`aa6d8308927a98a65dcf1de815b244e8f44f0e8eb85ff9034eed05aae867d17a`；draft schema v2；schema SHA256：`e6d03cccba4d4eafd0e9749975e6f97f5d22bb29e457271d69a025d6d4a08ff2`。
- answering.py SHA256：`76f43737f578be888e9aab83d632ef0f5f02937fb06d798785a3212964b6edc2`。
- 最多 6 步、1 次检索、1 次生成、0 次重试；整案 125s、生成链 60s；提示词 3000 UTF-8 字节和发送前保守余量检查，实际输入 ≤3328 Token，输出 ≤512 Token，context=4096，temperature=0/seed=0；本轮 API 费用预算 0。
- 字节预检不是精确 tokenizer。HTTP timeout 和步骤截止检查不等于强制结束服务端推理，未收到 usage 时记录 null，不把资源消耗视为零。

## 最后一次真实开发样例（accepted-v1）

所有正式统计以本目录这 8 条观测为准。四个可回答样例来自四个不同主体：网关、订单、审计、身份。没有拿 fixture 或同一主体重复请求代替多样本质量统计。

| 开发样例 | 模式 | 状态 / 原因 | 真实生成次数 | 输入/输出 Token | 耗时 ms |
|---|---|---|---:|---|---:|
| ready-conflict | hybrid | abstained / unverified_or_misbound_relation | 1 | 389/86 | 1032.0 |
| ready-direct | hybrid | answered / verified_evidence_selection | 1 | 252/54 | 1187.0 |
| ready-maintainer | hybrid | abstained / invalid_output_schema | 1 | 360/79 | 828.0 |
| ready-two-hop | hybrid | answered / verified_evidence_selection | 1 | 349/72 | 844.0 |
| refuse-action | hybrid | abstained / unsupported_or_unsafe_request | 0 | 未调用/未得到 usage | 47.0 |
| refuse-conflict | graph | abstained / unresolved_change_context | 0 | 未调用/未得到 usage | 203.0 |
| refuse-date | hybrid | abstained / no_evidence | 0 | 未调用/未得到 usage | 282.0 |
| refuse-unknown | hybrid | abstained / unknown_entity | 0 | 未调用/未得到 usage | 46.0 |

网关直答的终点为 team-platform；订单两跳为 `e-orders-dependency-v2 → e-inventory-owner-v1`，终点 team-data，引用两个文档来源。有效输出的每条 claim 和 citation 已另行对照冻结 graph/原文核验。

两个可回答失败：手册维护者样例为 invalid_output_schema；交接冲突样例为 unverified_or_misbound_relation。代码拒绝发布，因此本轮没有伪造回答流出；但它们仍是模型答题失败，不能算“正确拒答”。未知实体、资料尚未生效、动作/密钥请求、graph 模式缺交接背景四案没有调用生成模型，是确定性门禁样例，不是模型拒答能力测试。

## 开发与运行失败保留

- 首次 live-direct.json：Ollama 未启动，retrieval_unavailable。模型实际位于 E:\ollama，默认 C 盘目录启动触发权限错误；使用既有模型目录并禁止清理/云端后启动。随后 GPU 探测和冷加载超时，live-direct-retry.json、ready-direct.json 和 ready-two-hop.json 保留失败；尝试启动第二服务时端口占用，未覆盖原进程记录。
- 初版模型输出使用 context_chunk_ids。真实草稿将正确 owner 原文放入该字段，被当成非必要背景拒绝；公开模拟内容的调试草稿保存在 development-draft-inspection.json。改为统一 citation_chunk_ids，仍严格要求完整引用集合，未放宽出处核验。旧字段属于未完成开发版，未作为稳定公共协议发布。
- 中间 final/ 使用 raw=true，4 个可回答样例仅 1 个回答通过。对齐已有 Ollama 适配方式，恢复模型原生对话模板后，final-template/ 为 3/4；最后代码新增非法日期错误分类后 accepted-v1/ 为 2/4。同模型、相同温度和 seed 的输出仍有变化，保留全部记录，不挑 3/4 作为最终成绩。
- 以上都是开发调试样例，使用的是路线 4 的模拟知识；它们不是独立泛化测试。没有修改冻结题、语料、Top-K、生成 Token 或超时来刷分。路线 7 必须披露此前的检索诊断和开发使用。

## 测试与隔离证据

- 独立项目全量 unittest：**82 tests / OK，18.382s**，其中本轮新增 31 项；日志在 generated/stage08/route6/independent-tests-final.log。覆盖有效一/两跳、历史切换、草案/取消背景、未知/超范围问题、JSON/引用/关系错绑、漏引用、usage/digest 错误、超时、调用/输入/输出预算、过期来源、生成期间来源变化，以及禁止付费提供商。测试 fixture 明确标记 fixture，不冒充真实模型。
- 主项目：`uv run --offline pytest -q` → **451 passed、18 skipped、1 warning，69.58s**；18 项缺少独立 PostgreSQL/pgvector DSN。此后仅独立问答模块/测试有修改，独立全量已重新运行；主项目依赖与业务路径未变，本轮不宣称真库回归。
- 根/独立 uv.lock、Stage05/Stage05 dense/Stage07 pgvector 配置哈希与路线 5 基线一致；冻结来源再次通过 load_corpus 校验，原索引与 30 问未修改。
- 付费调用 0，无外部工单/邮件/权限动作；GraphSAGE 权重和 GraphRisk 服务未调整。

## 工件、复现与下一步

[逐步命令及四个 L6 练习](../docs/data/stage-08-graphrag-answering.md)说明如何运行、读引用、触发拒答和区分工程/模型质量。源码在 projects/enterprise-graphrag/src/enterprise_graphrag/answering.py，测试在同项目 tests/test_answering.py。

本地忽略工件见 [accepted-v1](generated/stage08/route6/accepted-v1/)；每条保存问题、日期、模式、源代码 SHA256、合同和真实结果。下面为最后观测文件的 SHA256，代码/模型/合同版本不得只用文件名推断。

| 文件 | SHA256 |
|---|---|
| ready-conflict.json | `e1acf704abcae114c982c08b11d40a8c94747475a8ad85419c2c6e0388e33076` |
| ready-direct.json | `9b0c77b5c345586d565bb7abd2c1963a7f6790809cca51f1503f1c40f9a9ba20` |
| ready-maintainer.json | `a23eea475b374787367120f32b22f1b21e4d11771c17c291632b0a6bd6f339b8` |
| ready-two-hop.json | `8cac63caaf8816f8829e4a1dc27991d3f4e9b04a85d5621538aff9165b45edbf` |
| refuse-action.json | `fde475c169b7b4da60fa8b3e910b0974732c4f603db2ef3f9234a674726db140` |
| refuse-conflict.json | `7f1cbc56f128e8acf7109fc25e6eb3c9057331a283b21c6a48e59fc1df2862bf` |
| refuse-date.json | `613528982979458f02905695308669b435c24a739330b1d564eab1206a500e38` |
| refuse-unknown.json | `f6e36937875d9fcd35f6702cf6a3088bb8f301d7506953d7da3db2b1bd3b3741` |

路线 6 的实现目标和故障门禁有证据，故工程完成；L6 个人学习不代签。路线 7 将在冻结 30 问上衡量事实、路径、引用、拒答和注入表现，当前 2/4 小样本不能替代该验收。Stage08 路线 7～8、路线 3 正式账单对账仍未关闭；阶段完成后提醒 GraphSAGE 专项诊断的约定不变。
