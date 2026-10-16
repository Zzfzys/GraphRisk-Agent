# Stage08 路线 3：DeepSeek 接入与验收记录

> 最新状态（2026-10-07）：路线 3 工程验收完成。真实 20 案对照通过；用户导入的小时用量/费用汇总已核对，官方 Paid/CNY 扣费 **0.0283512 元**，内部保守估算 **0.028383 元**。采用明确记录的小时汇总对账口径，未取得逐请求实扣金额；下文未完成记录保留为历史。

## 已交付与最初预检记录

新增 `src/graphrisk/agent/deepseek_comparison.py`，复用 httpx、冻结 20 案、原提示词/schema/评分器和同一个真实费用账本。CLI 增加 `--provider deepseek`；默认调查服务保持原状。密钥仅从进程环境或根目录忽略文件读取，已有进程环境优先，异常/日志不输出密钥、HTTP 原始错误或鉴权头。只允许官方 HTTPS 域名，不跳转、不继承代理。仅允许与冻结原创模拟案例完全相等的输入；不发送 IEEE-CIS 数据。

49 项聚焦测试通过（DeepSeek 新增 16 项 + 原同集评估 33 项），覆盖真实 usage 计数校准、相同 messages、预算持久化、鉴权/超时/重定向、模型及后端变化、错误输出、思考/工具拒绝、Token 超限、账单未知停机和私有环境读取。这些全部为模拟 HTTP 测试，不是 DeepSeek 质量得分。

真实前置证据：`reports/generated/stage08/route3/deepseek-382987383b2f4b21aa9a3e994466c4c6.json`。实际配置文件字节数 0；密钥未配置在发送请求前即被拒绝。真实模型列表、余额、生成与付费请求均为 0 次。

## 提供商切换的合同补充

本次采用官方当前模型名 `deepseek-flash`，非思考模式、JSON Object、temperature=0、max_tokens=512、60 秒请求上限，保持冻结输入和评分合同。官方将该名称映射到 DeepSeek-V4.1-Flash；它是可变别名，不具有原千问日期快照或本地 digest 的同等版本保证。每次校验返回 model 和 system_fingerprint；本轮内指纹变化即停止。模型/后端实际值待真实调用验证，不能提前称已固定权重。[官方模型说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

DeepSeek 未在已核对文档中提供与千问等价的完整消息预计数服务；官方要求以 usage 为准。因此新增一个明确的**付费计数校准步骤**：每案先用完全相同 messages 和非思考配置发出 max_tokens=1 的短请求，用实际 usage.prompt_tokens 验证 <=4096，再发正式 512 上限请求，并要求两次输入计数一致。校准输出不参与质量评分。[官方 Token 说明](https://api-docs.deepseek.com/quick_start/token_usage/)。

这是对原“调用计数服务后生成”的实现修订，不能声称是零成本预计算。首个校准调用前只能用冻结内容来源和 <=3500 UTF-8 字节的输入规模门禁；本集合实际最大 2083 字节。**字节检查不等于供应商精确 Token 保证**；实际计数只在校准返回后取得，超过上限即停止正式请求并保留费用/违规。每案 1 次计数校准 + 1 次正式生成，完整运行共 40 次模型调用、20 个正式答案，不伪称只调用 20 次。每个请求先预留 0.10 元，未知结果锁住后续调用；不自动重试或本地回退。

校准请求可能预热云端缓存，因而延迟/缓存命中与本地有额外差异。分别记录校准和正式生成墙钟、Token 与费用，不能隐藏前置耗时。60 秒每请求、125 秒每案上限不变。

官方非思考开关为 `thinking: {type: disabled}`；收到思考输出/思考 Token、工具调用或拒绝字段，均按协议失败停止。[官方思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。

## 预算与账单

总授权 100 元、80 元停止、单实验 5 元和 0.10 元预留保持不变。复用 `reports/generated/stage08/real-budget.sqlite3`，使用相同实验编号，按提供商区分案例尝试；不能通过切换提供商清空总预算。

2026-09-30 价格核对：Flash 高峰每百万 Token 缓存命中输入 0.04 元、未命中输入 2 元、输出 8 元；空闲价格为其一半。实现始终按高峰价格保守记账，usage 必须满足输入=命中+未命中、总数=输入+输出。4096 输入/512 输出全未命中生成约 0.012288 元上界，计数校准另算；不是实付账单。[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

运行前后读取官方账号余额，只作账号级差额旁证，可能受其他调用、赠金和结算时间影响；不能冒充逐请求正式账单。输出保留 `actual_bill_cny=null`，真实账单收据仍用原对账入口处理。未运行时新增费用为 0，不需要假造零元收据。

## 配置与复现

在 `E:\GraphRisk-Agent\.env.stage08-deepseek` 中填写并 **Ctrl+S 保存到磁盘**：

```dotenv
DEEPSEEK_API_KEY=仅在本机填入真实密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
GRAPHRISK_STAGE08_LIVE=0
```

不要把密钥发送到聊天。私有配置不由测试脚本修改或覆盖。

仅验证鉴权、模型可见性和余额，不生成：
```powershell
uv run --offline python scripts/evaluate_stage08_providers.py --provider deepseek
```

成功返回 `authentication_passed_generation_not_enabled`；退出码 2 表示尚未启用生成，不是鉴权失败。该预检是官方 GET 请求，没有模型生成。只有余额为人民币且可用、指定模型可见时才可继续。

已授权的同集对照使用两个显式开关。可临时设置当前 PowerShell 环境并运行，结束后清除这个非敏感开关：
```powershell
$env:GRAPHRISK_STAGE08_LIVE="1"
uv run --offline python scripts/evaluate_stage08_providers.py --provider deepseek --live
Remove-Item Env:GRAPHRISK_STAGE08_LIVE
```

输出默认写入唯一命名的忽略文件。原 `.env.stage08-qwen` 不读取，千问历史测试保留。不要重跑多次挑最好成绩；冻结集合、提示词和评分器都不得修改。

## 验收与学习

- [x] DeepSeek 独立适配、预算及错误合同的离线测试。
- [x] 真实密钥/官方模型/余额预检通过。
- [x] 20 案同集正式生成及 20 次计数校准，逐例质量和耗时完整保存。
- [x] 实际模型/指纹与 usage 核对、小时汇总账单对账、路线 3 总报告更新（2026-10-07；不是逐请求收据）。

L3 练习：解释别名和固定快照的差别；分别手算校准与生成的费用；说明账号余额下降为什么不自动等于本次逐请求费用。工程测试不能代签个人学习。

最终全量回归：451 passed、18 skipped、1 warning，45.92 秒；日志 reports/generated/stage08/route3/pytest-deepseek.log。18 项因未配置隔离 PostgreSQL/pgvector 测试 DSN 跳过。根 uv.lock 及三个默认运行配置哈希与接入前一致。

## 2026-09-30 16:05 更新：真实 API 与 20 案通过

用户保存配置后，受限执行环境出现 ConnectError；在获准网络环境下，官方 `/models` 和 `/user/balance` 均返回 HTTP 200，指定模型可见、人民币余额可用。连接问题不属于密钥鉴权失败。没有修改或回显私有密钥。

本轮正式结果：**20/20 整案通过，20/20 JSON 与 schema 合格，0 个无效引用（有效评分分母 20），7/7 应拒答案例正确拒答**。五组分别为 6/6、4/4、4/4、4/4、2/2；保留原案例、黄金答案、提示词/schema 与输出上限，没有重试、修复答案或修改评分标准。

| 指标 | 实测 |
|---|---:|
| 正式生成 / 计数校准 | 20 / 20 次，合计 40 次付费模型请求 |
| 正式生成墙钟 P50 / P95 | 687.290 / 855.755 ms |
| 校准墙钟 P50 / P95 | 609.598 / 810.090 ms |
| 每案两次请求墙钟之和 P50 / P95 | 1305.625 / 1550.196 ms |
| 所有请求输入 / 输出 Token | 18886 / 704 |
| 校准费用高峰估算 | 0.014296 元 |
| 正式生成费用高峰估算 | 0.014087 元 |
| 合计预算记账估算 | **0.028383 元** |
| 未知结果 pending | 0 |
| 账号余额接口观测差额 | 0.00 元；不能据此判定免费 |

实际 model 为 `deepseek-flash`，40 次请求观测同一个 system_fingerprint：`aeb56401ca74e127821c4f9126dcb669`。逐案校准与正式请求输入 Token 一致，全部在既定上限内。别名依然不等于不可变模型权重；不将小样本 100% 外推为完整 Agent 或生产准确率。

原始证据：[deepseek-36e2db7e6b0f4f2a8bf7b2f138f701a1.json](generated/stage08/route3/deepseek-36e2db7e6b0f4f2a8bf7b2f138f701a1.json)，SHA256 `142b3bbe35e999a939ca7b350ff6fce6b9a697a26b052662c5e558ae9dd71fd3`。逐案 raw_response、usage、计数、分数、耗时与预算尝试 ID 均保留；输出和账本处于 Git 忽略目录。复核脚本重新按冻结评分器检查 20 个原始回答及消息哈希，全部一致。

API 可用性与本轮质量实测已通过。**路线 3 尚待正式费用对账，Stage08 仍未全部完成。** 当前 0.028383 元是按 usage 与高峰单价的保守估算，`actual_bill_cny` 保持 null；账号余额差额为 0.00，可能涉及显示精度或结算时点，不能代替逐请求账单，也没有生成虚假零元收据。

### 正式账单对账操作（2026-10-03）

操作者登录 [DeepSeek 开放平台](https://platform.deepseek.com/)，在用量/账单中查询 **2026-09-30 北京时间（UTC+8）**，尽量按本项目使用的 API Key、`deepseek-flash` 和运行时段筛选，保存平台可提供的原始明细或导出文件到 Git 忽略目录 `reports/generated/stage08/route3/`。不要将 API Key、账号身份或未经脱敏的账单提交 Git 或贴到聊天。发票/充值记录只能证明付款，不能自动证明这 40 次请求的消耗。

先核对平台证据的粒度、结算币种与时间区间，再与本地 **20 次校准 + 20 次生成、输入 18886 Token、输出 704 Token** 对照。本地 `uv run --offline python scripts/reconcile_stage08_budget.py` 输出的 `settled` 是按返回 usage 结清内部预算预留，**不是官方账单已对账**。若平台提供可唯一对应的逐请求金额，核实每笔对应关系后，按 `docs/data/stage-08-provider-comparison.md` 的收据格式逐笔对账；不能根据请求顺序猜测映射。当前原始评估文件未保留逐请求供应商账单 ID 或独立时间戳，因此如果官方明细缺少足够标识，逐笔映射可能无法证明。

若平台只提供按日/API Key 汇总，必须先排除同一 Key 的其他流量，并检查请求数、Token、币种、缓存/峰谷价与汇总金额。可保留“官方汇总金额已核对”的证据及文件 SHA256，但**不要将汇总金额任意摊给 40 个 attempt，也不要生成零元逐笔收据**。这种情况需要明确修订路线 3 的验收合同，将“逐请求对账”改为可复核的“隔离 Key/时段汇总对账”，并保留逐笔金额未知的限制；修订并核验前，路线 3 与 Stage08 继续保持 In Progress。若平台无可核验用量明细，可通过官方平台帮助/反馈申请该时段与 API Key 的用量证明。

此前空文件、受限网络 ConnectError 的历史证据保留。上一轮代码回归为 451 passed / 18 skipped；本轮只执行真实验收与证据复核，没有修改业务代码，也没有重复全量测试。

### Actual per-case evidence

| Case | Passed | Generation ms | Input/output tokens |
|---|---|---:|---:|
| provider-01 | True | 850.036 | 466/36 |
| provider-02 | True | 695.464 | 466/36 |
| provider-03 | True | 748.24 | 466/36 |
| provider-04 | True | 646.719 | 466/36 |
| provider-05 | True | 758.786 | 499/35 |
| provider-06 | True | 656.683 | 462/36 |
| provider-07 | True | 572.038 | 472/40 |
| provider-08 | True | 711.906 | 472/40 |
| provider-09 | True | 964.423 | 472/41 |
| provider-10 | True | 591.25 | 468/38 |
| provider-11 | True | 446.111 | 466/29 |
| provider-12 | True | 785.717 | 466/29 |
| provider-13 | True | 679.116 | 416/29 |
| provider-14 | True | 776.128 | 432/29 |
| provider-15 | True | 510.636 | 524/36 |
| provider-16 | True | 670.387 | 471/29 |
| provider-17 | True | 698.817 | 524/36 |
| provider-18 | True | 454.679 | 465/29 |
| provider-19 | True | 475.129 | 483/29 |
| provider-20 | True | 803.391 | 487/35 |

## 2026-10-07 官方小时汇总账单对账完成

用户导入两份 DeepSeek 平台 CSV：`amount-2026-09-30_2026-09-30.csv` 和 `cost-2026-09-30_2026-09-30.csv`，均位于忽略目录 `reports/generated/stage08/route3/`。二者账户、模型和时间窗口一致：北京时间 **2026-09-30 16:00～17:00**，`deepseek-flash`。用量导出仅有一个脱敏 API Key，其可见字符与当前本地配置匹配；不声称比较了完整 Key 或保存了当时 Key 指纹。费用导出粒度为账户/模型/小时，没有逐请求金额。

| 指标 | 官方导出 | 原始运行/预算 | 核对 |
|---|---:|---:|---|
| 请求数 | 40 | 20 校准 + 20 生成 | 完全一致 |
| 缓存命中输入 Token | 7680 | 7680 | 完全一致 |
| 缓存未命中输入 Token | 11206 | 11206 | 完全一致 |
| 输入合计 Token | 18886 | 18886 | 完全一致 |
| 输出 Token | 704 | 704 | 完全一致 |
| Paid/CNY 小时扣费 | 0.0283512 元 | 按官方导出单价精确计算 0.0283512 元 | 完全一致 |
| 内部预算上界 | — | 0.028383 元 | 高出 0.0000318 元，全部由逐笔舍入解释 |

精确费用：`7680 × 0.00000004 + 11206 × 0.000002 + 704 × 0.000008 = 0.02835120` 元。内部实现对 40 个请求分别向上取整到整数 micro-CNY，合计 28383；未舍入汇总是 28351.2 micro-CNY，差额 31.8 micro-CNY。逐笔重算与每笔原始 usage 及 SQLite 金额全部一致，无未解释差额。官方导出为 Paid 钱包，不依据此前余额接口的 0.00 差额判零费用。

**验收合同修订。** 原逐请求正式收据要求超出这两份官方导出的粒度；本次采用“指定模型/小时完整汇总，用量与全部 40 个预算尝试对应，官方金额与导出单价精确一致，预算上界差额有逐笔解释”的核对标准。用量导出的全部请求及三类 Token 都被原始运行解释，没有剩余流量；费用导出是账户级小时汇总，不能宣称是逐 Key 或逐请求账单。逐请求实际金额继续未知。此修订不允许任意摊分汇总金额、生成零元收据或掩盖 pending；后续未知请求仍沿用原预算门禁。

核验命令（离线、读取账本，不改变原始记录）：
```powershell
.\.venv\Scripts\python.exe -I reports/generated/stage08/route3/verify_billing_20261007.py
```
报告：`reports/generated/stage08/route3/billing-reconciliation-20261007.json`，结果 `aggregate_reconciled`。核验器检查原始运行哈希、小时/模型/账户、脱敏 Key 可见字符、全部 Token、40 个唯一尝试、每笔预算和精确账单费用。对账前后 SQLite 哈希不变；原始运行 `actual_bill_cny=null` 保留为历史，实际汇总金额在新的对账报告中记录。

- 用量 CSV SHA256：`cab0e5688ca5d6a42276d908218d42620c3afb3fa92a3207703ccd343e60cdd4`。
- 费用 CSV SHA256：`1c28478d99544b6280738d94aa22803a6523644869bd030688a9012a590c2a30`。
- 原始运行 SHA256：`142b3bbe35e999a939ca7b350ff6fce6b9a697a26b052662c5e558ae9dd71fd3`。
- 预算 SQLite SHA256：`e13e31accc0cde3d85c7ea6a0e5ed586338b47f1f42f6e0d97493ece0edb08f4`。

导出中的账号与 Key 字段不进入公开报告。CSV 来源由用户说明为官方导出，离线核验检验一致性，不独立认证其来源。本次新增生成/付费调用 0，未修改预算 SQLite、模型或冻结案例。路线 3 工程完成，Stage08 工程总验收的最后费用门禁关闭；个人学习另行完成。
