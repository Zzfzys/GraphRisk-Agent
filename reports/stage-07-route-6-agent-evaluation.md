# Stage 07 路线 6：有界 Agent 修复与评估 v2

> 工程门禁：通过（2026-09-28）。Stage 07 仍在进行；路线 7 Compose 故障演练与路线 8 总验收尚未完成。个人 L6 自测由学习者亲自完成。

## 结果与证据

冻结旧集保持不变：Stage 05 的 17 案 SHA-256 为 `d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1`，Stage 06 的 33 案 SHA-256 为 `f91200b170e0665628312bc24ecfd01b928d6a74d861ce8e9bb9eac31caaf684`。重新运行的 50/50 全通过。Stage 07 冻结 15 案 SHA-256 为 `60fcad25b9dda57ecd243a226b7d41ce2bda88d28dd89ce88fd460784091f1d1`；15/15 实际观察通过、0 失败、0 未运行，合计 65 个冻结案例。三个开发案例不计入冻结门禁，本轮仍为 `not_run`，不能算通过。

| 风险组 | 新案例 | 通过 | 主要观察 |
|---|---:|---:|---|
| PostgreSQL 重启、幂等、并发、TTL、断连、写中断、费用 | 7 | 7 | 同 `run_id`/报告哈希、一次决定事件、无孤儿待审、一次费用结算 |
| pgvector 版本、来源篡改、错维度 | 3 | 3 | V2 生效边界命中；篡改/错维度为 503 且无引用 |
| 图空邻居与图服务失败 | 2 | 2 | 真 held-out 零邻居不编造关系；故障为部分证据且不开放审批 |
| 请求链 | 1 | 1 | 创建、读取、审批的请求 ID 绑定同一个运行 ID |
| 模型断连与总时限 | 2 | 2 | 断连 2 次内 503；注入时钟到 60 秒后 504，均无报告引用 |

逐例评分和原始观察在忽略目录 `reports/generated/stage07/agent-eval-v2-route6-http-race.json`。每案核对工具名与参数、四类证据状态、HTTP 分支、政策片段 ID/出处/生效日/正文哈希及不变量：15/15 工具/参数正确；有引文的 9 案 9 条均可绑定到已检索的有效片段，未检出无依据政策结论；审批绕过 0，外部动作 0。7 个持久化挑战全部通过。新探针类型为真数据库 9、真实本地 IEEE-CIS 1、受控故障 4、内存 API 追踪 1；受控故障不能被误称为真实网络事故。

## 失败证据到最小修复

- pgvector 原本把来源哈希/引用 JSON 篡改与普通索引故障都归为 `index_unavailable`。冻结篡改案要求区分证据来源不一致。现在逐请求校验检测到这两类来源漂移时返回 `policy_source_mismatch`，仍禁止任何引用；向量/索引元数据故障继续归为 `index_unavailable`。真库测试覆盖篡改后恢复。
- PostgreSQL 模式原本也为 `partial_evidence` 建立待审会话；图服务故障时可能给不完整报告开放审批。现在仅 `verified` 建立持久待审会话。真库回归确认 `partial_evidence` 可读但 `approval=null`。Stage 06 默认内存模式仍按其冻结契约显示待审，这一双模式差异需在路线 8 的兼容评审中显式处理，不能宣称两者行为完全一致。
- 并发和 TTL 的首轮探针失败来自探针本身：并发探针起初在释放被阻塞的拒绝决定之前等待批准结果；到期探针只修改 DB 到期列，破坏了保存的审批载荷绑定。改为两个独立 FastAPI 客户端真实并发请求，先放行拒绝、再观察批准请求的 409；TTL 使用 1 秒实际到期窗口模拟 30 分钟边界，并注明时间压缩。没有放宽 Agent 最大步数、调用次数、重试、费用或时限。

费用固定键 `stage07-cost-v2` 在本轮最终重跑前已由同一个隔离测试库的早期探针创建；最终逐例记录 `first_create_replayed=true` 和 `first_runner_calls=0`。该轮确认重放后账本仍仅有 1 个 settled attempt，而不是声称本轮重新计费。若要观察首次创建，需要一座全新的隔离测试库；不能删除或改写现有运行记录来制造首次调用。

## 延迟、Token 与费用的正确比较

旧 50 个固定 fake/受控案例本轮 P50/P95 为 **25.12/465.93 ms**，估算费用合计 0 元；它们使用 fake provider，不是本地模型耗时。15 个 v2 探针的端到端探针墙钟 P50/P95 为 **382.35/5463.54 ms**；其中包含数据库连接、服务重建、压缩 TTL 的 1.1 秒等待、真实图工件装载以及故障注入，不能直接与旧 50 案的 Agent 延迟对比。模型 deadline 案使用受控单调时钟达到 60 秒，墙钟约 114 ms，证明边界分支而非实测 60 秒超时性能。

同一模型 digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`、同一交易 `3488959`、TF-IDF 索引 `9eade9b4c7f38df917267731ebe1d19a0aa8c270cbe1b8234a64d64b1605b3e7` 复测原 Stage 06 三个真实本地样本：当前政策和关系上限 1 都是 `verified`，各 284/97 prompt/output tokens、估算 0 元；政策生效前是 `insufficient_evidence`、不调用模型。真实样本 P50/P95 **1766/3368.9 ms**，三案无引用错绑或审批绕过。三个样本来自同一交易，仅验证固定场景，不能推断欺诈检测准确率或泛化能力；费用 0 元是本地 Ollama 的 API 估算值，不含机器电费。

## 复现与门禁

在仓库根目录先用已有的隔离测试 PostgreSQL/pgvector 库、受限应用角色、迁移角色和本机 Ollama 准备环境；只对隔离评估库设置 `GRAPHRISK_STAGE07_EVAL_ISOLATED=1`。完整探针会短暂改写一条政策哈希并在 `finally` 恢复，因此不要指向业务数据库。

```powershell
$env:GRAPHRISK_STAGE07_DSN = ((Get-Content .env.stage07-app-localtest -Raw).Trim() -split '=',2)[1]
$env:GRAPHRISK_STAGE07_MIGRATION_DSN = ((Get-Content .env.stage07-localtest -Raw).Trim() -split '=',2)[1]
$env:GRAPHRISK_STAGE07_EVAL_ISOLATED = '1'
uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage07-v2-all --require-complete
uv run --offline python scripts/evaluate_stage06_agent.py --real
$env:GRAPHRISK_STAGE07_DSN = ((Get-Content .env.stage07-route6-pytest -Raw).Trim() -split '=',2)[1]
$env:GRAPHRISK_STAGE07_MIGRATION_DSN = $env:GRAPHRISK_STAGE07_DSN
$env:GRAPHRISK_STAGE07_APP_DSN = ((Get-Content .env.stage07-app-localtest -Raw).Trim() -split '=',2)[1]
uv run --offline pytest -q
```

共享评估库的 `cost_budget` 会保留历史预留：在同一库上多次跑集成测试后，两项 2 元预留测试曾因累计额度接近 24 元而失败（其余 334 项通过）。这不是把失败隐藏起来；本次新建一次性隔离数据库 `.env.stage07-route6-pytest` 后完整复跑为 336/336。后续复现也应使用新隔离库，不能重置业务账本来使测试通过。

最终全量测试 **336 passed, 1 warning**（Starlette 测试客户端废弃提示）；`uv sync --locked --offline --group demo --group dev`、`uv build --offline` 和 `git diff --check` 通过。构建警告提示仓库内 `.uv-cache` 可能进入 sdist；实查构建清单没有 `.uv-cache`、本地凭据、原始交易或生成报告。源码未提交新的依赖，默认 TF-IDF 后端未切换。下一门禁是路线 7 的完整容器拓扑与故障演练。
