# Stage 08 路线 3：冻结的提供商对照合同

> 2026-09-30；20 案 v1。仅用于独立评估，不接入默认调查服务。

## 输入与评分

案例在首次真实对照前固定于 `data/evaluation/stage08/provider_cases_v1.json`。全部为项目原创模拟资料，许可 `project-original-public-demo`；不读 IEEE-CIS 数据、调查报告或数据库。不是用户传入 `synthetic` 标签就允许外发：发送前必须与冻结案例逐字段相等。

- 案例 SHA256：`f1d2b0e3a4e46032aa8db8c8d7b90057a18cc0941835d240c819f03f5614ab0a`。
- 提示词和 schema 合同 SHA256：`0990dcff737fd445e44018d016c642cfc5c53e1bc9d2372a7fe7bcc6f65f9051`。
- 6 案证据归纳、4 案 JSON 字段、4 案证据不足、4 案过期/错误引用、2 案注入。合计 20 个不同模拟实体；黄金答案人工按规则写定，不由待评模型生成。
- 模型仅收到相同 system/user messages，包含日期、目标实体、事实、政策；不发送 expected、rationale、group。
- 严格输出五个字段：case_id、decision、finding、evidence_ids、policy_ids。case_id、结论、归纳类别及去重引用集合必须全部匹配才能通过。
- 日期区间为左闭右开，证据实体与指标时间窗必须匹配。无适用政策或缺所需数值时拒答，引用为空；数值 0 是已知值。
- JSON 解析拒绝重复键。schema 失败时后续引用正确性不可评估；`invalid_citations=0` 必须与 `citation_scored=0` 一起解释，不能称零幻觉。

本评估测受控证据判断和结构化回答，不测自由文本摘要、检索、工具规划、审批或完整 Agent 成功率。20 案是诊断样本，不能外推生产准确率。后续提示词优化必须另开开发版本，保留 v1，不能改冻结题或黄金答案。

## 本地与托管协议

本地固定 `qwen2.5:0.5b`、`qwen3:4b` 的路线 2 digest；候选已安装，不自动拉取新模型。Ollama `/api/chat`，JSON 模式、非流式、非思考，temperature=0、seed=20260929、num_ctx=4096、num_predict=512。调用前后核对 digest；资源预检要求空闲，每模型先卸载，串行完成全部案例。第一例未驻留，其余检查实际驻留。只将输出截断记为单案失败后继续；超时、资源/模型/Token 合同错误停止并保留未运行分母。

托管沿用千问北京 `qwen-plus-2025-12-01`，相同 messages、JSON 模式、temperature=0、max_tokens=512，关闭思考与搜索。仅此独立入口可显式启用真实请求；路线 1 原有 mock-only 入口及默认 GraphRisk 服务保持原状。

共同生成墙钟上限 60 秒、案例截止 125 秒；无自动重试，无本地回退。账本最多允许每案两次人工执行，每次仍计费；本轮正式成绩采用各模型完整运行的首答，不选择最佳结果。第一次本地基线仅跑 5 案的中断保留，之后完整 20 案单列。

本地输入使用 UTF-8 字节保守上界加模板/输出余量，再核对真实 Token usage；该办法不冒充托管 Token 计数。托管生成前调用同一北京 workspace 域的 `/api/v1/tokenizer`，携带固定 model 和相同 messages，只接受有效 usage.input_tokens <=4096；生成回传 prompt_tokens 超过预计数立即判违规并阻止后续调用。

计数接口设计依据 [阿里官方 Tokenization SDK](https://github.com/dashscope/dashscope-sdk-python/blob/main/dashscope/tokenizers/tokenization.py)。**当前没有真实端点/凭据证据证明该 workspace 的计数服务支持此固定快照及消息模板计数。** 若接口不支持、缺 usage 或计数不匹配，则停止，记录 `not_run`/错误，不能偷换模型、使用本地近似计数冒充已验证。该项需要真实配置后验证，mock 测试不能关闭门禁。

客户端墙钟含 HTTP 与解析；Ollama 服务端 duration 单列，不与托管未提供的服务端耗时作等价比较。两家 tokenizer、chat template、缓存、计算硬件不同，本地有 seed、托管无 seed 保证；同输入不能消除这些差异。P50/P95 只描述此小样本；失败请求保留。未测首 Token 延迟和并发。

## 预算与对账

用户授权本阶段最高 100 元；代码在 80 元停止，单实验 5 元，每次外部请求先预留 0.10 元，串行。账本固定 `reports/generated/stage08/real-budget.sqlite3`，不使用测试账本。**不要删除、改路径或复制空账本绕过限额。** 该账本只约束此评估入口，不含用户其他程序的 API 支出。

按 2026-09-30 核对的[千问 Plus 价格](https://help.aliyun.com/zh/model-studio/qwen-plus)，北京非思考短上下文输入 0.8 元/百万 Token、输出 2 元/百万 Token。4096 输入加 512 输出估计 0.0043008 元/案，20 案生成约 0.086016 元上界；**这是生成估算，不是账单，也不含计数服务费用。**

计数服务价格尚未确认，因此每次计数成功仍保留整笔 0.10 元作为保守预算占用；20 案约占用 2 元加生成估算。超时、断连、usage 缺失保留 pending，阻止下一笔调用。输出答错仍计生成费。供应商账单才是实际费用依据，不能把预留、估算或免费额度当作已对账实付。

查看账本：
```powershell
uv run --offline python scripts/reconcile_stage08_budget.py
```

人工核实账单后，在忽略目录保存 JSON 收据，三个字段为 `attempt_id`（账本返回）、`actual_micro_cny`（实际元数乘 1000000 的整数）、`bill_reference`（本机保留的账单定位）。调用：
```powershell
uv run --offline python scripts/reconcile_stage08_budget.py --receipt reports/generated/stage08/route3/receipt.json
```

收据由操作者核实，脚本不自动证明其真实性；不编造零元收据消除 pending。重复同收据幂等，冲突拒绝；保留前值和收据哈希，Token/费用违规状态不能用对账清除。

## 本地配置与运行

先检查待发送的原创模拟内容，不会调用模型：
```powershell
uv run --offline python scripts/evaluate_stage08_providers.py --provider prepare
```

运行本地对照（空闲 GPU；输出默认唯一命名，避免覆盖）：
```powershell
uv run --offline python scripts/evaluate_stage08_providers.py --provider ollama-baseline
uv run --offline python scripts/evaluate_stage08_providers.py --provider ollama-candidate
```

在仓库根目录手工创建已被 Git 忽略的 `.env.stage08-qwen`，仅填以下三个字段；不要把密钥发到聊天或提交 Git：
```dotenv
DASHSCOPE_API_KEY=在本机填入真实密钥
GRAPHRISK_QWEN_ENDPOINT=https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
GRAPHRISK_STAGE08_LIVE=1
```

endpoint 必须取自北京 workspace 控制台的完整地址，不能把占位符原样运行。参照[官方 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。脚本只加载上述三个字段，已有进程环境优先；禁重定向、不继承 HTTP 代理、不记录密钥/原始鉴权异常。

检查配置，不发生外部请求：
```powershell
uv run --offline python scripts/evaluate_stage08_providers.py --provider qwen
```

确认本地配置后运行已授权的小额真实评估：
```powershell
uv run --offline python scripts/evaluate_stage08_providers.py --provider qwen --live
```

真实计数接口不通就先修对应端点/权限，不通过清空账本或自动重试掩盖错误。每完成一案增量保存输出、Token、分数、费用状态、请求消息哈希；退出码 1 表示测量含失败，2 表示前置条件未就绪。参照输出 status、逐案错误和未运行数量解释。全部分数通过也不自动表示费用已对账。

## 学习 L3

先预测 provider-07（零值）、provider-17（政策换版边界）、provider-19（注入）的答案，再读原始输出。说明 JSON、schema、事实、引用这四关为什么不能合并；用一次 timeout 解释“没有答案”为什么不等于“没有费用”。最后手算上述生成费，写出 Token 计数和真实账单各自还缺什么证据。个人学习自测由本人完成。

## 2026-09-30 DeepSeek 最初接入记录（历史）

用户已选择 DeepSeek 替代千问完成真实对照。新增独立适配、相同冻结输入的计数校准/生成、usage/指纹与预算校验；原千问合同留作历史回归。当前磁盘上的 .env.stage08-deepseek 为 0 字节，真实预检在联网前返回 authentication_unconfigured，路线 3 与 Stage08 保持未验收；没有付费请求。模型别名、计数校准的合同差异、配置与命令见[DeepSeek 接入记录](../../reports/stage-08-route-3-deepseek.md)。个人学习仍需本人完成。

## 2026-09-30 16:05 真实 DeepSeek 验收更新

配置已保存，鉴权/模型/余额可用；受限环境 ConnectError 在获准网络环境下解决。本次固定 20 案真实生成 20/20 通过，另有 20 次付费计数校准，7/7 应拒答案正确；每案消息和提示词/schema 哈希保持一致。生成 P50/P95 为 687.290/855.755ms，计入校准的两请求之和为 1305.625/1550.196ms。usage 高峰费用估算共 0.028383 元，未知请求 0；账户余额差额 0.00 不代表免费，正式账单仍待对账，路线 3 保持未全面关闭。见[真实证据与局限](../../reports/stage-08-route-3-deepseek.md)。L3 可用这些真实数据学习，个人自测不代签；阶段完成后的 GraphSAGE 提醒不变。

## 2026-10-07 DeepSeek 本轮对账粒度修订

此轮官方导出为账户/模型/小时扣费与单个脱敏 Key 的小时用量，不含逐请求费用。以完整汇总对账关闭本轮费用验收：同一小时/模型/账户，全部 40 次请求及命中/未命中/输出 Token 与本地一致，无未解释用量；CSV 单价计算精确等于官方 Paid/CNY **0.0283512 元**。内部 **0.028383 元**上界与实扣差额 **0.0000318 元**由逐请求向上取整完全解释。详见[逐项对账与证据哈希](../../reports/stage-08-route-3-deepseek.md)。

这项修订仅改变本次验收可声明的账单粒度，不改变预算停机、预留或逐请求收据工具。原始运行与 SQLite 不覆盖，不将官方汇总强行写入某个 attempt；新的 `billing-reconciliation-20261007.json` 保存实际汇总金额与限制。仍不得将 `settled` 直接解释为已取得逐请求正式收据。脱敏 Key 仅核对可见字符，账号/Key 原值不公开。
