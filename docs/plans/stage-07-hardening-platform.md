# Stage 07：Agent 强化与持久化平台

> 状态：Done（路线 0～8 工程门禁通过；个人 L0～L8 自测独立）
> 计划时间：Week 9～10，可靠投入 20～24 小时/周，目标约 45 小时
> 前置阶段：Stage 06 工程门禁 Done
> [实现路线](stage-07-implementation-roadmap.md) · [学习路线](stage-07-learning-roadmap.md)；个人学习与工程验收分别记录。

## 阶段目标

保留 Stage 06 的可演示 MVP，把“单进程内存中看起来可靠”推进到可追踪、可重启恢复、可用真数据库验证的调查 Agent。交付 PostgreSQL 持久运行/检查点/审批/费用、可切换的 pgvector 政策 Retriever、请求级工具轨迹及 Agent 评估 v2。默认本地 TF-IDF 路径继续可用；是否将向量检索升为默认取决于来源和无答案质量。Neo4j 仅在核心门禁后按价值选择，不作为 Stage 07 完成条件。

本阶段继续使用原创模拟政策和未校准分数。审批的 `reviewer_id` 仍未认证，不宣称已具备生产身份认证或自动处置。没有并发证据时 API 继续单 worker，不把“重启恢复”写成“多 worker 生产就绪”。

## Stage 06 交接输入

[Stage 06 总验收](../../reports/stage-06-route-7-acceptance.md)已确认真实 held-out `3488959` 的本地/Compose 调查、模拟政策来源核验、`pending → rejected`、无政策和受控模型断连安全分支；旧 50 个固定案例 50/50，另有 3 个来自同一交易的本地模型样本，305 项全量测试通过。[评估 v1](../../reports/stage-06-route-6-agent-evaluation.md)记录旧 17/新 33 的案例文件哈希；不得修改旧案例来制造 v2 提升。第一轮 48/50 的两项是评估器误判，不能当作 Agent 真实失败。

优先边界：`Stage06Service` 持有内存运行记录、`CostLedger` 和 `InMemorySaver`；API 重启后旧审批明确失效；`reviewer_id` 未认证；固定交易有 GraphSAGE 分数但严格历史风险邻居数为 0。Stage 07 需先冻结持久化和评估合同，再动默认行为。可按[中文演示指南](../data/stage-06-demo-guide.md)重放 Stage 06 基线。

## 小路线门禁与学习任务

| 路线 | 工程状态 | 对应学习 | 目标产物 |
|---|---|---|---|
| [0 前置门禁与选型](stage-07-implementation-roadmap.md) | [x] | L0 状态归属（个人待自测） | [基线/环境/依赖实测](../../reports/stage-07-route-0-preflight.md) |
| [1 失败分类与 v2 契约](stage-07-implementation-roadmap.md) | [x] | L1 冻结评估（个人待自测） | [15 案冻结/评分与初始失败证据](../../reports/stage-07-route-1-failure-contract.md) |
| [2 请求链追踪](stage-07-implementation-roadmap.md) | [x] | L2 ID 和脱敏（个人待自测） | [实测报告](../../reports/stage-07-route-2-request-trace.md) · [数据契约](../data/stage-07-request-trace.md) |
| [3 持久化语义合同](stage-07-implementation-roadmap.md) | [x] | L3 事务/幂等（个人待自测） | [存储合同](../data/stage-07-persistence-contract.md) · [故障矩阵](../data/stage-07-persistence-fault-matrix.md) · [路线 3 报告](../../reports/stage-07-route-3-persistence-contract.md) |
| [4 PostgreSQL 恢复](stage-07-implementation-roadmap.md) | [x] | L4 真库恢复（个人待自测） | [真库实测报告](../../reports/stage-07-route-4-postgres-recovery.md) · [操作说明](../data/stage-07-postgres-service.md) |
| [5 pgvector 检索](stage-07-implementation-roadmap.md) | [x] | L5 时间/向量（个人待自测） | [真库/质量报告](../../reports/stage-07-route-5-pgvector.md) · [操作/数据合同](../data/stage-07-pgvector-retrieval.md) |
| [6 Agent 修复与评估 v2](stage-07-implementation-roadmap.md) | [x] | L6 分组对比（个人待自测） | [新旧评估](../../reports/stage-07-route-6-agent-evaluation.md) |
| [7 Compose 故障演练](stage-07-implementation-roadmap.md) | [x] | L7 容器边界（个人待自测） | [真容器/故障报告](../../reports/stage-07-route-7-compose-faults.md) |
| [8 总验收与复盘](stage-07-implementation-roadmap.md) | [x] | L8 面试讲解（个人待自测） | [总验收](../../reports/stage-07-route-8-acceptance.md) · [中文演示](../data/stage-07-demo-guide.md) |

每路线完成后更新本文件的任务、证据、决定与计划偏差。约 45 小时含 12 小时学习/复盘；每天至少 30～60 分钟。工程 Done 不代替个人 L0～L8 自测。

## 实现任务

- [x] 保留 Stage 06 旧 50 案与版本哈希，新增并冻结 15 个不同失效机理的挑战案例；记录真实观察、故障注入与推测的区别；
- [x] 建立服务端请求 ID、业务运行 ID、工具轨迹、模型/检索版本和脱敏审计的关联；
- [x] 冻结可持久状态、报告哈希、审批/费用事务、TTL、迁移和失败安全合同；
- [x] 用 PostgreSQL 真库实现报告/运行、LangGraph 暂停恢复、审批审计和费用状态的重启恢复；
- [x] 正式接入可切换 pgvector Retriever，保持政策来源、版本、日期、无答案和错误契约；本地 TF-IDF 保留；
- [x] 只对新证据暴露的 Agent 缺陷做有界修复，形成分组、逐例、fake/真实分开的评估 v2；
- [x] 通过本地与 Compose 的真实数据库恢复、故障注入、隐私扫描及旧 Stage 06 回归；
- [x] 仅在核心门禁后评估 Neo4j 的额外展示价值；如不做，记录延期理由，不影响本阶段完成。

## 交付物

- 前置/架构/故障矩阵和数据库迁移契约；
- PostgreSQL 持久运行/审批/检查点及费用状态；
- pgvector 政策检索与本地 dense/TF-IDF 对照报告；
- 请求链和结构化审计说明；
- 冻结的新案例与 Agent evaluation report v2；
- Compose 真库重启演练、Stage 07 总验收、README 和下一阶段交接。

## 验收标准

以下每项必须有真实运行或自动化证据，不能用设计图、静态 Compose 或虚构截图代替：

- [x] Stage 06 冻结 50 案仍为 50/50 且 SHA-256 未变；新增 ≥12 案按预期安全分支通过，逐例失败保留；
- [x] 同一 `run_id` 的合格 `pending` 报告在 API 进程/容器重启后可读取并继续人工拒绝，报告哈希、版本、TTL、审计和费用不漂移；重复提交幂等，冲突和过期安全拒绝，外部动作 0；
- [x] PostgreSQL 真库迁移、断连、写入中断和并发决定测试通过；数据库不可用时明确失败，不假装内存恢复；
- [x] pgvector 真库先按政策日期过滤，再检索并保留 `chunk_id`、原文哈希、出处、模型 digest/维度；无政策、错版本、错维度和损坏输入不产生无依据引用；
- [x] API 请求 ID 可以关联创建、工具轨迹和审批审计，日志不含测试标签、原始交易行、密钥或完整 Prompt；
- [x] 旧 API/工作台和 Stage 06 本地/Compose 默认路径回归；新 Compose 中 PostgreSQL/pgvector、API 和工作台真实启动、故障恢复和关闭；
- [x] 评估 v2 并列报告工具/参数、引用、无依据结论、审批绕过、恢复、延迟、Token/费用，与 v1 的可比部分明确对照；三个旧真实样本仍单独报告；
- [x] `uv run --offline pytest`、锁文件/构建、Git/镜像受限文件检查通过，并有 README/中文演示与已知限制。

## 验证证据

路线 0 的[实测报告](../../reports/stage-07-route-0-preflight.md)记录了 Python/锁文件、Docker/pgvector 真库探针、Stage 06 本地与 Compose 就绪、305 项回归、冻结 50/50 与三条真实本地样本、模型/语料版本和依赖选型。路线 1 的[失败分类与契约报告](../../reports/stage-07-route-1-failure-contract.md)冻结旧 50 与新增 15 案哈希、独立机制及评分规则；首个真实 API 重启探针为 0/1 已观察通过、14 案未运行，5 项聚焦和 310 项全量测试通过。路线 1 当时仅关闭评估合同门禁。路线 2 的[请求链实测](../../reports/stage-07-route-2-request-trace.md)在固定真实 held-out 调查中贯通创建→GET→人工拒绝，5 个工具、6 个 Agent 节点、2 次依赖尝试均有受限日志；冻结追踪挑战通过，重启挑战仍失败，当前新集 1 通过/1 失败/13 未运行，全量 316 项测试通过。该证据关闭请求链门禁，但不等于持久化或 pgvector 验收；个人 L0～L2 自测另行记录。

路线 3 的[持久化/兼容合同](../data/stage-07-persistence-contract.md)与[15 行故障矩阵](../data/stage-07-persistence-fault-matrix.md)已定义创建和决定的提交顺序、幂等/费用对账、过期与迁移规则；[路线 3 报告](../../reports/stage-07-route-3-persistence-contract.md)只证明合同门禁关闭，不是 PostgreSQL 真库恢复证据。路线 4 的[真库报告](../../reports/stage-07-route-4-postgres-recovery.md)完成业务表/检查点/审批/费用恢复：12 项真库集成、受限角色真实 held-out 跨实例演练和冻结重启挑战通过，全量 329 项测试通过。新增冻结集当前 2 通过、13 `not_run`；路线 5～8 当时仍待实现。路线 5 的[真库/质量报告](../../reports/stage-07-route-5-pgvector.md)证明 16 段固定模型的受限检索、30 案同集状态与候选排序完全一致、无答案误报 0/4、真实 held-out 调查有 3 条 pgvector 政策命中；全量 334 项测试通过。默认 TF-IDF 不变；路线 6～8 仍未验收。

路线 6 的[新旧 Agent 评估](../../reports/stage-07-route-6-agent-evaluation.md)完成 50 个旧冻结案例与 15 个新冻结案例，另复测三个同一交易的真实本地样本；全量 336 项测试通过。15 个新案例全部有实际观察并通过；真库来源篡改有独立错误码，PostgreSQL 图服务失败只保留部分证据、不开放审批。开发集三案尚未执行；Stage 07 的完整 Compose/总验收当时仍在路线 7～8。

路线 7 的[真容器、故障与数据边界报告](../../reports/stage-07-route-7-compose-faults.md)及[操作指南](../data/stage-07-compose.md)记录了独立 Compose、16 条政策向量、真实 held-out 调查、API 重建后同运行/哈希审批恢复、重复/冲突决定、DB 断连 503、重启与 down/up 后卷保留。镜像、挂载、权限、Git 忽略、响应和日志边界经过检查；全量 339 项测试通过。路线 8 的总验收见下；个人 L7 自测仍待学习者独立完成。

路线 8 的[逐项总验收](../../reports/stage-07-route-8-acceptance.md)和[中文演示/学习复盘指南](../data/stage-07-demo-guide.md)覆盖工作台真实交易、待审 API 重建后同运行拒绝、幂等/冲突、无政策、真 DB 断连/恢复、受控模型/图/过期分支、Stage 06 本地及 Compose 默认路径、旧 50/50 与新 15/15、339 项全量测试、离线锁/构建及受限数据边界。首次旧隔离评估库因固定幂等键遗留返回 14/15，记录保留；新隔离库为 15/15。浏览器自动化首轮拒绝操作实际触发批准，另起工作台运行用键盘完成拒绝并核验终态；这两项观测均未隐藏。个人 L0～L8 自测尚未由学习者签收。

## 风险与降级方案

- Docker 磁盘、拉取或 PostgreSQL/pgvector 镜像受阻：先完成不依赖真库的路线 1～3；延长日历时间并保留 Stage 07 `In Progress`，不得把内存或 mock 结果标为真库通过。
- LangGraph 持久检查点适配与当前锁文件不兼容：路线 0/3 明确最小兼容方案；只保存报告 JSON 不满足“会话可恢复”门禁。
- pgvector 排名或无答案结果不达标：保留本地 TF-IDF 默认，修复来源/日期/阈值问题；后端可切换但质量门禁不通过时 Stage 07 继续未完成。
- Neo4j 成本高：维持现有本地图查询并记录可选扩展的取舍，不删改 GraphSAGE 历史语义。
- 未认证审批字段：只做本地演示，保持 `reviewer_authenticated=false`、回环访问和外部动作 0；不以请求 ID 替代认证。正式身份集成需单独设计与验收。
- 真库中可能保存派生报告：最小化字段、约束 TTL、凭据放忽略的本地环境；原始 IEEE-CIS、标签和模型权重不进入 DB 或 Git。

## 决策记录

| 日期 | 决定 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-21 | 基础设施后置到 MVP 之后 | 避免环境问题阻塞核心价值验证 | Stage 06 采用本地/内存实现 |
| 2026-09-27 | Stage 07 按“基线与评估→追踪/合同→PostgreSQL 恢复→pgvector→v2/容器验收”排序 | Stage 06 已 50/50，主要风险是重启丢状态、缺故障覆盖和数据库/政策版本错绑 | 先冻结验收，旧默认演示保留；未通过真库门禁不能标 Done |
| 2026-09-27 | PostgreSQL/pgvector 为必做，Neo4j 为可选；正式认证不在本阶段伪称完成 | 两周 45 小时应优先验证可靠性与持久化，避免多基础设施同时扩张 | 保留未认证标识与本地边界；Neo4j 不阻塞 Stage 07 |
| 2026-09-27 | 路线 0 固定 PostgreSQL 17 + pgvector 0.8.6、PostgresSaver 3.1.2、psycopg 3.3.6；pgvector Python 0.5.0 仅为已测候选 | Docker 镜像/扩展 SQL、Python 依赖解析及真库检查点往返均实测通过；见[报告](../../reports/stage-07-route-0-preflight.md) | 路线 4 锁定检查点和 psycopg；路线 5 复用 psycopg 参数化向量，无需新增 pgvector Python 包；旧 TF-IDF 默认演示保留 |
| 2026-09-28 | 路线 1 冻结 15 个 v2 挑战与独立评分，开发集另存 3 案 | 避免旧 50/50 饱和和新实现调答案；真实重启探针暴露当前 410 失败 | 后续路线按固定合同实现；未运行案例保持 `not_run`，错误码目标与兼容由路线 3 明确 |
| 2026-09-28 | 路线 2 用服务端请求 ID 贯通 API、工具/Agent 与审批审计，日志只保留固定元数据 | 真实本地 held-out、并发/错误/无政策与脱敏测试均通过 | 追踪字段仍随内存运行；路线 4 再将必要审计纳入持久状态，不能把请求 ID 当身份认证 |
| 2026-09-28 | 路线 3 冻结双模式、checkpoint-first 发布、决定意图/费用账本与幂等重试合同 | 现有 Stage 06 三份内存状态无法靠单独保存报告实现恢复；七个冻结挑战直接要求重启、故障与不双扣 | 路线 4 按 P01～P15 真库实现；合同完成不等于运行恢复 |
| 2026-09-28 | 路线 4 显式 PostgreSQL 模式、版本化迁移、持久检查点、决定意图和费用账本通过真库验收 | 冻结重启案例由旧内存 410 转为真库 200；受限角色真实 held-out 跨实例审批与创建重放通过 | 内存默认保留；路线 5 做政策向量后端，路线 7 做容器重建/故障演练，Stage 07 仍 In Progress |
| 2026-09-28 | 路线 5 固定 dense 索引导入 pgvector，使用现有 psycopg 参数化向量，无新增 Python 依赖 | 16 段精确查询和逐请求核验足够；30 案与本地 dense 完全一致但 P50 更高 | pgvector 作为显式候选，TF-IDF 继续默认；Compose/Agent v2 仍待验收 |
| 2026-09-28 | 路线 6 对 15 个冻结挑战逐例探针，并针对真库来源错码与部分图证据审批做最小修复 | 旧 50/50、新 15/15、真实固定三样本与 336 项测试通过 | PostgreSQL 与旧内存模式在部分证据审批上暂有差异；路线 8 须明确兼容策略，路线 7～8 未验收 |
| 2026-09-29 | 路线 7 使用独立 Compose 和一次性迁移容器；DB 默认仅内部网络，宿主 Ollama 仅固定地址 | 真容器完成重建、断连/恢复、down/up 与数据边界演练，避免改变 Stage 06 默认演示 | 工程路线 7 通过；Stage 07 总验收仍待路线 8 |
| 2026-09-29 | 路线 8 工程门禁 Done，Neo4j 暂缓；保留 PostgreSQL 对部分图证据的安全收紧 | 真 Compose 恢复/故障、同集评估和旧默认回归有逐项证据；现有图查询足够演示时间/共享实体语义 | 不把单 worker 演示称作生产；正式统一审批语义、鉴权与备份交给后续阶段 |

## 计划变更

将原总纲中笼统的“优化工具描述”细化为由新挑战集驱动的最小修复；明确运行记录、检查点、费用和审批必须作为同一恢复合同，不能只换 Retriever 或只持久化报告。与[总体计划](../../PROJECT_PLAN.md)的 Stage 07 目标一致；路线 0 已完成环境与真库兼容探针；路线 1 完成新评估合同与初始缺口测量；路线 2 贯通请求链与安全审计；路线 3 冻结持久状态、故障矩阵与兼容合同；路线 4 完成真 PostgreSQL 的运行/审批/费用恢复；路线 5 完成 pgvector 业务与同集质量验收，Agent v2 的 15 个冻结挑战已逐例通过；独立 Compose 的真实故障与数据边界门禁已通过；路线 8 总验收已通过，个人学习自测仍单独进行。

## 阶段复盘

工程复盘：Stage 07 将 Stage 06 的进程内调查推进到显式 PostgreSQL 模式的跨进程恢复，pgvector 保留可切换，旧内存默认演示仍可运行。新挑战 15/15、旧集 50/50，但真实模型仅三个同交易小样本；来源校验、零引文和拒绝审批比笼统通过率更能证明安全边界。旧隔离评估库污染及浏览器自动化误操作已在总验收中保留。后续仍需正式复核员鉴权、多 worker 一致性、备份恢复和真实业务效果验证。个人 L0～L8 练习与 90 秒讲解由学习者自己完成。

2026-10-07 当前环境复测：根功能测试通过，但 Docker/WSL ERROR_NO_SYSTEM_RESOURCES，18 项真库测试 skipped，15 个冻结持久化探针未运行；需要恢复隔离数据库后补测。2026-10-02 工程验收保留，本轮不冒称真库通过。见[跨阶段复测](../../reports/stage-01-08-recheck-20261007.md)。

2026-10-07 Docker 恢复后的跨阶段验收：真实 Engine 29.8.0、磁盘 D:\docker2\DockerDesktopWSL；新隔离数据库中根 470 passed/0 skipped，Stage07 冻结 15/15、Stage05/06 机制 17/17 与 50/50，独立核心/UI、真实本地调查及 Stage06/07 容器重启/断连/卷恢复均通过。项目终端加入本地 NO_PROXY；首次引擎中断和验收脚本错误保留。测试栈已关闭释放内存，镜像/卷保留，新增付费与训练均为 0。详见[恢复完整复测](../../reports/stage-01-08-docker-recheck-20261007.md)。单交易真实样例与 GraphSAGE 质量边界保持，Stage09 尚未开始。
