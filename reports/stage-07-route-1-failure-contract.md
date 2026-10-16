# Stage 07 路线 1：失败分类与 Agent 评估 v2 契约

> 2026-09-28：工程门禁通过；L1 个人练习待学习者完成。Stage 07 整体仍为 In Progress。本文只冻结案例和评分，不声称持久化、pgvector、追踪或新案例已全部通过。

## 冻结输入与分类

- Stage 05 原 17 案 SHA-256：`d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1`；Stage 06 原 33 案 SHA-256：`f91200b170e0665628312bc24ecfd01b928d6a74d861ce8e9bb9eac31caaf684`。两份原文件及期望未改。原 50 案由 Stage 06 评估集成测试在全量回归中继续验证。
- [新增冻结集](../data/evaluation/stage07_agent_frozen_v2.yaml)：15 案、15 种机制，SHA-256 `60fcad25b9dda57ecd243a226b7d41ce2bda88d28dd89ce88fd460784091f1d1`。[开发集](../data/evaluation/stage07_agent_dev_v2.yaml)：3 案，可用于实现期调试，不计入冻结验收分母。政策语料 SHA-256 `5944138255497bb595d46513a7100045363d1f35d99fd2607954567e64145513`。
- 每例含输入/故障、预期工具与参数、证据状态、政策 chunk、HTTP/业务分支、审批、错误码、必须保持的不变量。冻结集只按既定预期评分；如果后续证据要求改语义，需记录版本变更或新建 v3，不能默默改本集追求通过率。

分类规则是：`observed_stage06_gap` 表示 Stage 06 已观察到的能力边界，不一定是产品缺陷；`controlled_fault` 表示已有受控故障或可在当前测试层触发的路径；`risk_hypothesis` 是尚待路线 2～7 以探针验证的风险。Stage 06 首轮 48/50 的两项是评估器误判：把 `not_ready` 当错误码、把图 `unavailable` 当 `error`。它们已按旧接口契约修正，不能列为 Agent 缺陷，也不计为新增产品修复。

| 冻结案例 ID | 独立机制 | 分类 | 目标分支 |
|---|---|---|---|
| `pending_restart_recovers` | 待审批检查点跨进程恢复 | 已观察边界 | 200 / `verified` / `pending` |
| `decision_retry_after_lost_response` | 决定已提交但响应丢失后的幂等重试 | 风险假设 | 200 / `rejected` |
| `conflicting_decisions_race` | 并发相反决定只保留先提交者 | 风险假设 | 409 / `approval_conflict` |
| `approval_expiry_survives_restart` | 重启不得延长 30 分钟审批窗口 | 风险假设 | 410 / `approval_expired` |
| `database_read_outage_fails_closed` | 数据库读断连明确失败，恢复后哈希可核对 | 风险假设 | 503 / `persistence_unavailable` |
| `database_write_interrupt_no_orphan_pending` | 写入中断不公开孤立待审批 | 风险假设 | 503 / `persistence_unavailable` |
| `creation_retry_cost_once` | 带幂等键的创建重试只结算一次 | 风险假设 | 200 / `verified` |
| `policy_superseded_version_excluded` | V1/V2 切换日先按日期过滤 | 受控故障 | 201 / `verified` |
| `policy_chunk_hash_tamper_rejected` | 政策正文/出处哈希错绑拒绝引用 | 风险假设 | 503 / `policy_retrieval_unavailable` |
| `embedding_dimension_mismatch_rejected` | 查询向量维度错配拒绝引用 | 风险假设 | 503 / `policy_retrieval_unavailable` |
| `empty_graph_neighbors_not_fraud_ring` | 固定 held-out 的零合格邻居不虚构团伙 | 已观察边界 | 201 / `insufficient_evidence` |
| `graph_service_failure_is_partial` | 图服务失败与有效空邻居区分 | 受控故障 | 201 / `partial_evidence` |
| `trace_links_create_read_approval` | 三次 HTTP 请求沿同一 `run_id` 关联 | 风险假设 | 200 / `rejected` |
| `model_connect_failure_bounded` | 本地生成模型断连按预算终止 | 受控故障 | 503 / `generation_unavailable` |
| `model_deadline_bounded` | 模型超时不越过总截止时间 | 风险假设 | 504 / `deadline_exceeded` |

新增冻结集分类数为已观察边界 2、受控故障 3、风险假设 10。`empty_graph_neighbors_not_fraud_ring` 的交易 `3488959` 是既定 held-out，实际可得图分数但严格历史邻居为 0；这里检验的是安全表述，不把它说成欺诈团伙。政策及模型错误在 `expected_response` 使用公开 API 错误码，内部 `retrieval_error`/`generation_error` 只属于运行状态。带幂等键的创建重试是路线 3～4 的目标合同，目前 API 尚无此语义。

## 评分规则与第一条真实探针

[评分器](../src/graphrisk/demo/stage07_evaluation.py)在读取案例时先核对旧 17、旧 33、新 15 的源字节哈希和政策语料版本；缺观察值的案例标为 `not_run`，分母只为实际执行过的案例。每条观察必须含故障探针类型、被注入的故障、原请求、工具/参数、证据状态、HTTP/审批/错误分支和引用。引用逐条与本仓库的规范政策 chunk 比较 `chunk_id`、`doc_id`、章节、原文、版本、出处与正文 SHA-256，并核对半开生效日期及被检索片段。审批绑定检查 `run_id`、交易、会话和报告哈希；费用、图、追踪与外部动作使用各案显式不变量。逐例失败检查保留在 JSON 中，不删失败行。开发集与冻结集分别汇总。

[探针](../src/graphrisk/demo/stage07_probes.py)用确定性 Stage 05 证据驱动真实 FastAPI `TestClient`，先由旧服务创建 `pending`，关闭该服务，再在新服务实例用同一 `run_id` GET；不是凭预期伪造的产品结果。实际返回 `410 run_instance_mismatch`，相对目标 200、`pending`、原报告哈希保持，评分失败项为 `response_http_status`、`response_branch`、`response_error_code`、`response_approval`、`stable_report_hash`。该失败是路线 3～4 要解决的持久化缺口，旧内存模式仍应诚实报错。

复现：

```powershell
uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage06-restart
uv run --offline pytest -q tests/integration/test_stage07_agent_evaluation.py
uv run --offline pytest -q
```

本次初始报告：`reports/generated/stage07/agent-eval-v2-initial-20260927T162335Z.json`（本地忽略目录，UTC 文件名）。冻结集 **15 总数、1 已观察、0 通过、1 失败、14 未运行**；已观察通过率 0/1，不能称 0/15 产品通过率。开发集 3/3 未运行。以后完成全部探针，可用 `--observations <案例ID映射JSON> --require-complete` 强制要求 15 条冻结案例全部通过；评估脚本不自行生成未做过的数据库/向量/追踪观察。

评分器验证使用专门的评分夹具，故意篡改引用原文、正文哈希、过期 V1 检索片段、审批报告哈希和故障标记，均命中对应失败检查；这些夹具不计入产品通过率。聚焦测试 **5 passed**；全量 `uv run --offline pytest -q` **310 passed、1 条第三方 Starlette/anyio deprecation warning**。旧 Stage 06 的 50 案通过测试仍在全量内。未运行真实 PostgreSQL、pgvector、跨进程恢复、请求链或新集全部分支，因此本报告仅关闭路线 1，不关闭 Stage 07 总验收。

## 学习任务 L1（个人待完成）

自己从表中选 6 种机制，各写“触发输入 → 正确安全结果 → 哪条证据证明它”，其中至少含一次评估器缺陷与一次产品能力缺口。先预测重启探针结果，再运行上述命令，打开本地 JSON 的 `splits.frozen.rows[0].failed_checks` 对照。解释为什么 `not_run` 不等于通过，以及冻结集不能随实现结果改答案。个人能独立解释后再在学习记录勾选 L1；工程测试不代签。
