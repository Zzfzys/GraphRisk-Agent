# Stage 08 路线 1：模型提供商适配与安全输出合同

日期：2026-09-29。状态：**路线 1 工程门禁通过**；Stage08 整体仍 In Progress，路线 2～8 待执行。
配套：[实验合同](../docs/data/stage-08-experiment-contract.md) · [实现路线](../docs/plans/stage-08-implementation-roadmap.md)。

## 本路线实现

复用现有 `ReportProvider.generate(prompt, schema)` 和 `ReportDraft`，新增：
- `src/graphrisk/agent/hosted_provider.py`：QwenEvaluationConfig、ReviewedCase、QwenReportProvider、HostedResult、ProviderFailure/ProviderAudit。
- `src/graphrisk/agent/provider_budget.py`：标准库 SQLite 持久化费用预留；不增加依赖，不更改根 uv.lock。
- `scripts/probe_stage08_provider.py`：必须显式选择 `--provider qwen-fake-http` 的离线协议探针。
- `data/evaluation/stage08/provider_contract_smoke.json`：1 个原创模拟协议夹具，不是路线 3 的模型质量评估集。入口固定其文件 SHA256：`f0fa24ca82b3841cc8336e385e68e2c7c50fc4a6a905141b7821e7ae7d6fc61f`。

默认 Stage06/07 服务和原 Ollama adapter 不导入新模块、不切换 provider。新适配器只接受 httpx.MockTransport；未提供或提供真实传输均返回 `live_transport_disabled`。即使机器已设置真实 key，也不能从当前入口发出付费请求；探针使用独立 fake key 并在退出作用域时恢复环境。

## 输入、输出与信任边界

配置固定北京 workspace HTTPS chat endpoint、快照 `qwen-plus-2025-12-01`，禁止明文 HTTP、IP、查询参数、用户信息、其他地域、伪造域名或未填 WorkspaceId 模板。禁止 HTTP 重定向和自动读取代理环境。鉴权仅由环境变量 DASHSCOPE_API_KEY 读取，不支持 config/命令行写入 key。

请求使用非思考、temperature=0、JSON Object、最大 512 输出 Token；系统提示包含 JSON/schema。使用已有 ReportDraft 进行严格类型/字段校验，再校验交易 ID、synthetic 来源、完整且不重复的证据 ID、候选政策 ID、uncertainty 和建议动作。重复 JSON key、非有限 JSON 数、额外字段、强制执行动作均拒绝。合法返回仍交给既有 Stage05ReportWorkflow 核对政策原文、日期与出处，不能把 provider 的字段绑定当作事实正确性证明。

JSON Object 不保证符合业务 schema，因而保留本地核验；接口合同来源：[百炼结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。当前没有验证账号权限、真实接口可用性或真实模型质量。

输入首先要求与已审查夹具的完整 prompt 和固定 schema 一致；入口先校验文件哈希，不接受用户随意填写 synthetic 标签来外发内容。库中的 ReviewedCase 由可信调用方构造；哈希与声明只是审查记录，不能自动检测所有敏感数据，也不能防止操作者改代码绕过边界。本路线没有网络出口。

HostedResult 保留原 ProviderResult 字段，增加 finish_reason、request_sha256、evidence_binding 和 fake_http 标记。ProviderFailure 只携带固定错误码、受限结束原因、Token、费用状态和耗时，不回显异常正文、响应内容、完整 prompt 或 Authorization。没有自动重试、输出修复或切换到本地的行为。

## 失败与费用矩阵

| 场景 | 结果 | 费用处理 |
|---|---|---|
| 未审查输入、缺 key、发送前 deadline 耗尽 | 拒绝，不发送 | 不预留 |
| 配额耗尽、同案尝试超过 2 次 | 拒绝，不发送 | 不新增预留 |
| 401/403、429、其他非 200、重定向 | 分类错误，不跟随重定向 | 无可核对 usage，保留预留 |
| 超时、断连、协议/解码失败 | transport 类错误，无 fallback | 保留预留，后续调用停止 |
| 缺失/非法 usage、坏响应信封 | 拒绝 | 保留预留 |
| 模型快照不符、输出截断、schema 或证据错配 | 拒绝，不修复成成功 | 有效 usage 仍结算 |
| 返回超出输入/输出合同 | 拒绝并锁住后续调用 | 记录 usage 费用并标记 violation |
| 超过 deadline 才返回有效响应 | 不返回成功 | 有效 usage 仍结算 |

账本单位为整数 micro-CNY；请求前原子预留 0.10 元，已结算加未结算不能超过 80 元阶段停止线或 5 元单实验上限（用户授权总额 100 元，20 元余量不自动启用）。SQLite BEGIN IMMEDIATE 使跨实例并发预留串行；pending/violation 存在时不再发送。更换 provider 实例或重启不能清零同一账本，不能通过修改同一账本配置抬高预算。超过单次预留的 usage 仍如实入账并阻断。

费用按冻结单价估计，不等于供应商账单。超时没有 usage 不能记零。当前 SQLite 中全部为**模拟记账**；CLI 每次使用新的 fake 账本以隔离探针场景，绝不能用于真实费用清零。路线 3 必须采用唯一的阶段真实账本，并实现账单对账/未知费用解除机制。

`fake_input_token_bound` 仅用于协议测试，不是经过验证的 tokenizer 上界。路线 3 仍需核对完整 messages/schema 的真实 Token 计数或经验证的保守上界、真实 endpoint、模型权限和当时单价；这些未完成前不得移除 live 保护。httpx timeout 用于请求超时，返回后另核 deadline；尚未证明真实网络环境中的精确墙钟上限。

## 验证与复现

聚焦命令：
```powershell
uv run --offline pytest tests/unit/test_stage08_hosted_provider.py tests/integration/test_stage08_provider_workflow.py -q
uv run --offline python scripts/probe_stage08_provider.py --provider qwen-fake-http
uv run --offline python scripts/probe_stage08_provider.py --provider qwen-fake-http --scenario wrong_citation
uv run --offline python scripts/probe_stage08_provider.py --provider qwen-fake-http --scenario truncated
uv run --offline python scripts/probe_stage08_provider.py --provider qwen-fake-http --scenario timeout
```

探针输出为忽略目录 `reports/generated/stage08/provider-*.json`。合法输出应通过；三个错误场景应返回预期拒绝，`contract_passed=true` 表示拒绝行为正确，绝不是模型正确率。全部结果固定标明 actual_hosted_calls=0、actual_hosted_cost_cny=0、quality_evaluation=false。

本轮最终证据（2026-09-29）：

| 检查 | 实测 |
|---|---|
| 新增聚焦测试 | 56 passed（54 单元 + 2 工作流集成） |
| 全量测试，带真实隔离 DB 环境 | **395 passed**，61.30 秒；1 个已有 Starlette/AnyIO 弃用 warning |
| 旧 Agent v1 | **50/50**；三个本地场景为 verified / insufficient_evidence / verified |
| Agent v2 冻结集 | **15/15**，0 failed，0 not_run；另 3 个开发案仍 not_run |
| 四种 fake HTTP 探针 | ok、wrong_citation、truncated、timeout 均 contract_passed=true |
| 根锁与旧 50/15 文件哈希 | 与路线 0 一致 |
| 实际千问调用 / 费用 | **0 次 / 0 元** |

最终日志在 `reports/generated/stage08/route1-recheck/`：`pytest-baseline.log`、`agent-v1-9cfbd1a7cb.json`、`agent-v2-9cfbd1a7cb.json`、`baseline-summary.json`。旧真实三场景仍来自同交易，只有两个实际生成调用；不能算三个独立模型质量样本。

**首次失败保留。** 初次全量 394 项与旧 50 案通过；随后补充解码异常处理与对应测试。首次 v2 在连接迁移数据库时超时（`route1/agent-v2.log`，exit 1），Docker 检查发现无运行容器，原 --rm 隔离测试容器已不存在，不能断言具体退出原因。没有修改业务库或应用行为来绕过此失败。

使用已有本地 pgvector 镜像，新建 `graphrisk-stage08-route1-test`，仅监听 `127.0.0.1:15433`，限制 512MiB/1 CPU；凭据随机生成，只写入忽略的 .env.stage08-route1-*。新建独立 pytest/eval 数据库（后缀 9cfbd1a7cb），真实应用评估使用受限角色、迁移使用专用 admin。设置隔离确认变量后完整重跑，得到上述 395/50/15 结果。未启动或修改原 Compose 业务服务。临时容器验收后释放，生成证据保留；再次运行真库回归需先准备新的隔离 DB，不能用过期旧库重放固定幂等键冒充新实验。

## 学习 L1 与后续

亲手对照 wrong_citation 与 truncated 的错误码和账本：为什么它们没有生成有效报告，却仍有模拟费用？再看 timeout：为什么 usage/费用未知，预留不能释放？解释 JSON 语法、字段约束、证据绑定、原文适用性分别在哪一层核验。L1 自测需本人完成。

下一路线仍是路线 2 本地模型可行性。路线 3 才启用经过门禁的真实 API、冻结 20 案并分别评价首答/失败；本路线不会把 fake 测试宣传为模型能力。

## Stage 08 完成后的约定

用户要求 Stage 08 完成后提醒 GraphSAGE 调整。已设置当前会话的每日条件检查提醒（automation ID: stage-08-graphsage）；阶段未完成或状态无变化时保持安静。同时在路线 8 验收及 Stage09 交接清单保留提醒，不提前自动训练。

Stage09 开头先补多笔不同交易的真实本地 Agent 基线，然后用 8～12 小时主动工作预算诊断 GraphSAGE 实现、同特征对照、邻居采样和训练充分性；训练运行时间另记，预算从可选监控展示等工作中调整。现有权重和测试记录冻结，禁止反复按已查看的测试集调参。