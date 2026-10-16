# Stage 07：边实现边学习路线

> 状态：工程路线 0～8 已通过；个人 L0～L8 练习与自测仍待完成
> 配套：[实现路线](stage-07-implementation-roadmap.md) · [Stage 07 总纲](stage-07-hardening-platform.md)
> 节奏：Week 9～10，每周 20～24 小时；Stage 07 总约 45 小时，其中约 12 小时用于针对性学习、代码阅读与复盘。每天至少 30～60 分钟，较长练习拆成数天完成。

## 学习方法和每日记录

每条路线都按“先预测结果 → 读一小段现有代码/契约 → 运行最小例子 → 对照测试与审计 → 用自己的话解释”进行。AI Agent 可以帮你写代码和查错，但你必须能独立说明状态为什么安全、失败时没有越权、评估为什么可信。先保留 Stage 06 的旧 50 案与真实样本，再做新实验；不因看完视频或测试变绿就自动宣称理解。

个人笔记每次只写五栏：`日期/路线`、`我预测的状态与原因`、`实际命令/输入`、`观察到的状态/审计`、`我能解释的原因与仍不懂处`。只记可公开的 ID、哈希和汇总指标；不要复制原始交易行、标签、连接密码、完整 Prompt 或未经检查的报告。阶段总纲只记录工程决策和可复现证据，个人学习笔记可留在本地。

| 时段 | 工程路线 | 学习主题 | 最小可讲成果 |
|---|---|---|---|
| Week 9 Day 1 | 0 | 旧系统状态归属与基线 | 画出内存记录/检查点/费用三角 |
| Week 9 Day 2 | 1 | 失败分类、冻结评估集 | 为 6 个故障写安全预期 |
| Week 9 Day 3 | 2 | 请求 ID 与业务 ID | 追踪一次请求和后续审批 |
| Week 9 Day 4 | 3 | SQL 事务、幂等、TTL | 写四个崩溃时序 |
| Week 9 Day 5 | 4 上半 | 持久检查点和状态恢复 | 读懂一笔 pending 的库中状态 |
| Week 10 Day 1～2 | 4 下半 | 并发决定和费用对账 | 重启后完成一次拒绝 |
| Week 10 Day 3 | 5 | 向量检索与时间版本 | 解释日期过滤先于 Top-K |
| Week 10 Day 4 | 6～7 | 评估对照与 Compose 故障 | 读懂 v2 分组和容器网络 |
| Week 10 Day 5 | 8 | 总验收与 90 秒表达 | 不看稿讲清恢复和限制 |

时间表是学习顺序，不是“某一天必须把路线做完”。若真库或模型环境阻塞，继续完成不依赖它的概念练习，但工程门禁保持未完成。

## L0：从 Stage 06 状态图推导新需求（约 1h）

**读什么。** `src/graphrisk/serving/investigation_service.py` 的 `Stage06Service`，`src/graphrisk/agent/approval.py` 的 `Stage05ApprovalWorkflow`，以及[Stage 06 服务契约](../data/stage-06-service-contract.md)。

**亲手练习。** 画四栏：API 内存记录、LangGraph 检查点、费用账本、工作台 `run_id`。假设进程在“模型已生成报告、待审检查点刚写完”时崩溃，分别预测重启后 GET、审批、费用会发生什么，再查 Stage 06 测试验证。把旧 50/50 拆成“覆盖的安全分支”和“没有覆盖的重启/真库分支”。

**自测。** 为什么报告 JSON 留在磁盘不等于暂停的 LangGraph 会话可恢复？为什么 Stage 06 的 `run_instance_mismatch` 是诚实的错误，而不是持久化？

## L1：故障分类与新评估契约（约 1.5h）

**读什么。** [Stage 06 评估 v1](../../reports/stage-06-route-6-agent-evaluation.md)、`data/evaluation/stage06_agent_cases_v1.yaml`、`src/graphrisk/demo/stage06_evaluation.py`。

**亲手练习。** 从“进程重启、DB 写入失败、同一决定重试、政策过期、模型断连、空图邻居”各写一个案例卡：触发输入、正确业务状态、允许的工具、禁止的动作、审计字段。标记是既往真实观察、受控故障注入还是纯风险猜测。对旧 48/50 初跑解释为什么是评估器误判，不应算模型失败。修改一份本地临时期望使评分失败，再恢复；不可改冻结源文件。

**自测。** 50/50 已饱和时，新增 15 案的价值是什么？为什么开发集可改，冻结验收集不应随着结果改答案？

**当前可复现材料。** [路线 1 报告](../../reports/stage-07-route-1-failure-contract.md)和 `scripts/evaluate_stage07_contract.py --probe-stage06-restart`；工程已完成，个人 L1 仍需自己预测、运行并解释逐例失败。

## L2：请求链追踪与日志脱敏（约 1h）

**读什么。** `api/investigation.py`、`src/graphrisk/serving/investigation_views.py` 与 Stage 06 审计字段。

**亲手练习。** 在实现的 trace 中选一次调查创建、一次 GET 和一次人工拒绝，画出同一 `run_id` 对应三个 HTTP `request_id`，指出 `session_id`/`approval_id` 和报告哈希在哪里出现。核对日志只含状态、工具、耗时、版本与错误码；用测试证明日志不含原始交易行、标签、密钥或 Prompt 全文。

**自测。** 客户端自填 `request_id` 能否证明复核员身份？为什么刷新页面不应复用创建请求的 request ID 作为“同一次 HTTP 调用”？

**当前可复现材料。** [路线 2 报告](../../reports/stage-07-route-2-request-trace.md)、[请求链契约](../data/stage-07-request-trace.md)与 `uv run --offline python scripts/probe_stage07_trace.py`。工程已验收，个人 L2 仍需自己画链路、复现并解释日志的脱敏边界。

## L3：事务、幂等和恢复合同（约 1.5h）

**读什么。** 路线 3 的新存储契约、`Stage06Service.run/get/decide` 与现有审批测试。

**亲手练习。** 画四条时序：①报告已生成但写检查点失败；②检查点已写但 API 记录未发布；③审批已提交但响应丢失，浏览器重试；④两个请求同时批准/拒绝。每条写出可见状态、唯一键/条件更新、事务或补偿、错误码、费用变化与外部动作数。再画 30 分钟审批窗口与 24 小时运行记录 TTL 两条时间轴。

**自测。** 为什么“写了数据库”仍可能产生双扣费或孤立 `pending`？为什么仅在单 worker 测试通过不能声称多 worker 安全？

**当前可复现材料。** [路线 3 存储合同](../data/stage-07-persistence-contract.md)、[故障矩阵](../data/stage-07-persistence-fault-matrix.md)和[合同报告](../../reports/stage-07-route-3-persistence-contract.md)。工程合同门禁已关闭；四条时序图和费用/事务解释仍由学习者亲自完成。

## L4：真库检查点、状态恢复和对账（约 2.5h）

**读什么。** 路线 4 选定的数据库迁移、持久检查点适配文档和集成测试；对照 `InMemorySaver` 的现状。仅使用路线 0 实测兼容的版本，不按网上片段盲装新框架。

**亲手练习。** 在测试库创建一笔 `pending`，记录数据库中的 run、检查点、审批审计和费用预留（只看必要字段），关闭 API，重新启动后用原 `run_id` GET，再拒绝并重复提交相同决定；检查报告哈希、审计事件数、费用与外部动作 0。接着故意改一个备注或哈希，看冲突/绑定错误。断开数据库再请求，确认不会悄悄转内存审批。

**自测。** 哪个状态是系统事实来源？数据库恢复后为什么仍要重新核对报告哈希和版本？如果数据库卷丢失，应该向用户显示什么？

**当前可复现材料。** [路线 4 真库报告](../../reports/stage-07-route-4-postgres-recovery.md)、[操作说明](../data/stage-07-postgres-service.md)、`uv run --offline pytest -q tests/integration/test_stage07_postgres.py` 与 `uv run --offline python scripts/probe_stage07_postgres.py`。工程门禁通过；亲自查看四类数据库事实、解释崩溃对账与权限边界后再勾选个人 L4。

## L5：pgvector、政策时间与引用完整性（约 1.5h）

**读什么。** [Stage 05 政策索引契约](../data/stage-05-policy-index.md)、`src/graphrisk/agent/policy_retrieval.py`，以及路线 5 的表结构和对照测试。

**亲手练习。** 用共享设备政策 V1/V2 的切换日画半开有效区间。先筛日期再算余弦 Top-K；解释如果先取 Top-K 再删过期版本，会怎样漏掉有效政策。手算两条单位向量的点积排序，然后查真库返回的 `chunk_id`、正文哈希和来源；让一个错维度或错 digest 的查询失败。把本地 dense 与 pgvector 的命中和无答案结果并排记录。

**自测。** `source_quote_verified` 到底证明了什么？余弦分数为什么不等于政策适用概率？同分时用 `chunk_id` 排序解决什么问题？

**当前可复现材料。** [路线 5 真库/质量报告](../../reports/stage-07-route-5-pgvector.md)、[操作/数据合同](../data/stage-07-pgvector-retrieval.md)、`uv run --offline pytest -q tests/integration/test_stage07_pgvector.py` 和两份固定案例逐例 JSON。工程门禁已通过；个人须亲自画日期边界、核对一条真实引用与错维度拒绝后再勾选 L5。

## L6：修复与 Agent 评估 v2（约 1h）

**读什么。** 新旧案例清单、评估逐例 JSON、`src/graphrisk/agent/reliability.py` 的预算/重试状态。

**亲手练习。** 选一个新挑战的第一次失败，写“证据→原因→最小修复→开发集结果→旧 50 案结果→冻结 v2 结果”。把 fake 与真实本地模型的 P50/P95、Token 和费用分开；标注真实三个样本来自同一交易，不能得出准确率。没有失败就挑一条受控故障解释安全分支，不编造缺陷。

**自测。** 引用完整率为 100% 时，无政策案例的分母应该如何处理？为什么不能靠增加重试预算来掩盖坏的终止策略？

**当前可复现材料。** [路线 6 逐例评估与修复复盘](../../reports/stage-07-route-6-agent-evaluation.md)、`src/graphrisk/demo/stage07_probe_suite.py`、`scripts/evaluate_stage07_contract.py`。请亲自从篡改政策与图服务失败两案选一案，写出首次失败、最小修复、旧 50 和新 15 的验证链；真实三个样本只是同一交易的固定演示，不代表准确率。个人 L6 未自动勾选。

## L7：Compose 网络、卷与故障边界（约 1h）

**读什么。** Stage 06 的 `compose.yaml`、路线 7 的 `compose.stage07.yaml` / `Dockerfile.stage07`、[操作指南](../data/stage-07-compose.md)、[真容器与故障报告](../../reports/stage-07-route-7-compose-faults.md)和[Stage 06 容器验收](../../reports/stage-06-route-5-local-compose.md)。

**亲手练习。** 画工作台→API→DB/pgvector→宿主 Ollama 的地址。标出哪个容器可以读取受限数据、哪个卷可写、哪些端口只绑定回环。重建 API 容器前后查同一 `run_id`；只停止数据库时记录就绪与错误，恢复后再查。不要删数据库卷来模拟普通重启。

**自测。** `localhost` 在 API 容器指向谁？`compose down` 与删除数据库卷有什么不同？为什么镜像扫描与 Git 扫描都要做？

**当前可复现材料。** 路线 7 工程已完成：真实 API 重建、DB 停止/恢复、Compose down/up、卷保留、数据边界与 339 项回归均有记录。请按操作指南亲自查同一运行 ID 和哈希，画只读/可写挂载及两条网络；个人 L7 不能由工程实测代签。

## L8：验收复盘和面试表达（约 1h）

**亲手练习。** 不看实现写一页复盘：“Stage 06 不能恢复 → Stage 07 如何恢复”“TF-IDF 默认 → pgvector 如何可切换”“50/50 饱和 → v2 如何看新增挑战”。实际跑一条重启审批、一条无政策或 DB 故障，并从 API 状态、请求 ID、报告哈希和审计说出安全性。最后录一遍 90 秒讲解：输入与证据（20 秒）→引用与时间过滤（20 秒）→重启恢复及人工拒绝（25 秒）→v2 评估与限制（25 秒）。

**自测。** 能否在不说“企业级”“生产就绪”的情况下，准确说明当前可复现能力和未认证复核员、模拟政策、未校准模型分数、多 worker 未验证等限制？

**当前可复现材料。** [路线 8 逐项总验收](../../reports/stage-07-route-8-acceptance.md)记录真实容器/工作台操作、故障与评估观测；[中文演示指南](../data/stage-07-demo-guide.md)给出个人重演命令、学习问题和 90 秒口述。请自己记录一次 pending 重启后拒绝，以及一次无政策或数据库断连的期望与实际；未亲手完成前不勾选 L8。

## 个人完成自检

以下须由学习者亲自运行和解释，Codex 的工程测试不会自动勾选：

- [ ] 我能画出 run、request、session、approval、report-hash 五种标识的归属和生命周期。
- [ ] 我能解释 pending 发布与检查点写入的不一致风险及处理办法。
- [ ] 我能演示重启恢复、幂等重试、冲突拒绝与费用不重复计入。
- [ ] 我能讲清数据库故障时为什么不能静默退回内存。
- [ ] 我能手算一次向量相似度并说明政策日期过滤、引用来源校验。
- [ ] 我能区分旧 50 案、≥12 新案例与同交易三个真实模型样本的结论范围。
- [ ] 我能解释数据库卷、受限数据只读卷和镜像内容各自的边界。
- [ ] 我能在 90 秒内把成果、证据和限制讲清楚。

