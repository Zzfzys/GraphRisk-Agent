# Stage 07 路线 8：端到端验收与复盘

> 工程状态：Done（2026-09-29，路线 0～8 的工程门禁）。个人 L0～L8 自测仍由学习者独立完成。本报告只覆盖本地单 worker 演示，不宣称生产部署。

## 固定输入与证据

真实 held-out IEEE-CIS 交易为 `3488959`，政策日期 `2026-07-01`，关系上限 3。Stage 07 独立 Compose 使用 PostgreSQL 17 + pgvector 0.8.6、宿主固定 digest Ollama、API 与 Streamlit 工作台；[路线 7 故障报告](stage-07-route-7-compose-faults.md)记录拓扑与权限。工作台创建的最终拒绝案例为 `run-5422a706e8d8-bdafc1611e824fc3ad7957f14991220b`，冻结报告 SHA-256 为 `8ae0cebb9080ab5b1b406f3d08c24d113efff9a837bb4356bf3de9ebdff7c390`，审批到期时间 `2026-09-29T03:44:48.364467Z`。

| 门禁 | 期望 | 实测与证据 |
|---|---|---|
| 工作台证据 | 真实模型、图、政策可解释，缺边不编造 | 页面 `verified/pending`；XGBoost 0.1967、GraphSAGE 0.2969、融合 0.1883；合格历史风险邻居 0；一条模拟政策 `policy-1cd398a94233baa2ed38d318`，出处 `data/policies/stage05/manual-review-v1.md`；分数和关系均标注不确定性。引文出处核验不代表自动判定政策适用。 |
| 重启恢复与人工决定 | pending 时重建 API，旧运行/哈希/TTL 不变；工作台可拒绝 | `up -d --no-deps --force-recreate api` 后 API 与页面仍读到同一 run/hash/`pending`。工作台拒绝后 API 为 `rejected`，审批审计仅 `created,rejected` 两条；到期时间未延长，本地 API 估算费用 0 元、484/100 prompt/output tokens、外部动作 0。 |
| 幂等与冲突 | 完全相同决定只算一次；相反决定不能覆盖 | 同一绑定载荷重试拒绝为 HTTP 200、仍 2 条审计；随后批准为 HTTP 409 `approval_conflict`，终态仍 `rejected`。 |
| 无政策 | 不编造条款或开放审批 | 真实 Compose 请求 `policy_as_of=2025-12-31` 得 `insufficient_evidence`、0 引文、`approval=null`、生成尝试 0。 |
| 数据库故障 | 明确失败，不回退内存 | 真容器 `stop db` 后 ready 503/`postgres=false`；旧运行 GET 和新调查 POST 均 503 `persistence_unavailable`。重启 DB 后 ready 200，同一拒绝运行及哈希可读。 |
| 模型、图、过期 | 安全停止并区分故障类型 | 冻结 v2 受控模型断连 HTTP 503 `generation_unavailable`、超时 HTTP 504 `deadline_exceeded`，均无引用/审批；压缩到 1 秒的真实 TTL 案跨服务恢复后 HTTP 410 `approval_expired`；图服务故障为 `partial_evidence` 且 PostgreSQL 模式无审批。它们是受控注入，不是本次宿主 Ollama 故障。 |
| 旧默认路径 | Stage 06 本地和 Compose 可用，内存语义不被伪装为持久 | 旧 Compose API/UI 就绪，真实交易 `verified/pending`、1 引文、0 邻居，人工拒绝后审计 2、外部动作 0；重建旧 API 后旧 run 返回 410 `run_instance_mismatch`。当前源码的 `scripts/run_stage06_local.py` 也完成 API/UI 就绪和真实 `verified/pending` 调查。 |
| 卷与数据边界 | 普通重启保留 DB，受限数据不进入镜像/Git/UI | Stage 07 `down`/重新 `up --no-build` 后相同拒绝运行和哈希可读。应用角色受限，DB 默认无宿主端口，API/UI 仅回环。镜像内无 secret、原始/派生 IEEE-CIS、模型工件和生成报告；镜像环境无 DSN/password。响应字段及容器日志扫描未发现原始标签、身份键、向量或本机 secret 值。 |

第一次浏览器自动化尝试点击“拒绝”时实际触发了“批准”；该演示运行被保留为 `approved`，外部动作 0，不能计为拒绝成功。随后在独立运行用键盘激活工作台的“拒绝”按钮，页面/API 均确认 `rejected`；既有 Streamlit AppTest 同时覆盖批准与拒绝按钮。未能证明首次异常属于应用逻辑，故保留这一自动化操作限制，不抹去观测。最终重启→拒绝使用了另一个由工作台创建的 `run_id`。

## 冻结评估、测试与构建

旧 Stage 05/06 冻结集文件哈希分别保持 `d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1` 与 `f91200b170e0665628312bc24ecfd01b928d6a74d861ce8e9bb9eac31caaf684`；旧集 50/50。Stage 07 新集哈希 `60fcad25b9dda57ecd243a226b7d41ce2bda88d28dd89ce88fd460784091f1d1`，新 15/15、失败 0、未运行 0。三个开发案例仍 `not_run`，不计入冻结门禁。旧集 fake/受控探针 P50/P95 为 28.97/538.92 ms，无依据政策结论和审批绕过均为 0；三个固定真实本地样本状态为 `verified,insufficient_evidence,verified`，P50/P95 为 1921/3890.2 ms，来自同一交易，不能推断总体准确率。新集含真库、受控故障和真实本地样本，墙钟不能与旧 fake 案直接比较；同集逐例工具/参数、引用、审批、费用边界见[路线 6 报告](stage-07-route-6-agent-evaluation.md)。

首次在旧隔离评估库重跑新集为 14/15：固定创建幂等键 `stage07-cost-v2` 已指向上一轮运行，其审批已过期，失败检查为 `creation_retry_cost_once.response_approval`。保留观测 `reports/generated/stage07/agent-eval-v2-route8.json`。在**全新隔离数据库**重跑为 15/15，且该案首次创建确实调用 runner 1 次、费用结算 1 次、重放 HTTP 200/`pending`；观测 `reports/generated/stage07/agent-eval-v2-route8-fresh.json`。不能删除旧运行或改冻结预期来制造通过。旧 50 与三个真实样本观测在 `reports/generated/stage07/agent-eval-v1-route8.json`；这些生成文件均被 Git 忽略。

全量 `uv run --offline pytest -q` 在新隔离测试库为 **339 passed，1 条 Starlette/AnyIO 弃用警告**，58.24 秒；`uv sync --locked --offline --group demo --group dev`、`uv build --offline`、三种 Compose 配置语法检查、`git diff --check` 通过。构建对仓库内 uv 缓存给出“可能进入 sdist”警告；实际检查 wheel 无受限路径，sdist 在 raw/interim/processed 下仅含三个 `.gitkeep`。Git 在敏感范围只跟踪这些占位文件；本机 secret 和隔离数据库 DSN 文件均被忽略。API 公共响应的受限字段扫描为空，容器日志未出现两个 secret 原值或 PostgreSQL URL。本次检查是有限样本的边界证据，不替代未来审计。

## 复现入口

按[Stage 07 Compose 操作指南](../docs/data/stage-07-compose.md)准备本地授权数据、忽略的凭据、固定模型与独立容器栈，再按[中文演示指南](../docs/data/stage-07-demo-guide.md)从工作台操作。回归命令如下；新 v2 评估必须按[PostgreSQL 操作说明](../docs/data/stage-07-postgres-service.md)使用迁移、授权后的**全新隔离数据库**，通过本地环境变量提供应用/管理员 DSN，不复用演示库或先前评估库，也不要把连接串写入报告。

~~~powershell
uv sync --locked --offline --group demo --group dev
uv run --offline pytest -q
uv run --offline python scripts/evaluate_stage06_agent.py --real
uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage07-v2-all --require-complete
uv build --offline
docker compose -f compose.stage07.yaml config --quiet
~~~

真容器断连、重建与 down/up 的步骤见演示指南；受控模型/图/过期注入对应冻结挑战集，不能仅靠上面 Compose 语法命令宣称故障通过。所有生成 JSON 留在忽略目录 reports/generated/stage07/，个人复现时记录案例哈希、运行 ID、报告哈希、状态和审计条数即可。
## 兼容策略、限制与交接

Stage 06 内存模式继续作为默认 TF-IDF 演示；旧运行跨 API 重启返回 410。Stage 07 PostgreSQL/pgvector 必须显式选择；DB 不可用为 503，不静默降级。对 `partial_evidence`，旧内存模式为保持冻结合同仍可能显示待审，PostgreSQL 模式只允许 `verified` 创建审批。这是**有意的不兼容安全收紧**，迁移演示和面试必须讲清；正式统一语义及版本化 API 合同留给后续独立改动，不在 Stage 07 暗改旧 50 案。

`reviewer_id` 仍未认证；所有批准/拒绝只写审计，不执行封禁、拒付或通知。政策均为原创模拟文本，模型分数未校准，固定交易严格历史邻居为 0；多 worker、远程部署、备份/恢复、正式鉴权及真实风控效果未验收。Neo4j 本阶段不接入：现有图查询已能展示时间/共享实体语义，增加第二图数据库对本轮恢复与引用证据收益不足，避免让它阻塞主门禁。

[Stage 08](../docs/plans/stage-08-local-model-graphrag.md)接收冻结评估集、模型 digest/引用合同与独立 GraphRAG 范围；不把当前 3 个同交易样本作为模型排名依据。[Stage 09](../docs/plans/stage-09-quality-operations.md)接收单 worker 限制、未认证复核员、可观察的请求 ID/工具轨迹、DB 备份和安全日志边界，设计 CI、监控与正式鉴权。个人学习复盘和 90 秒讲解见[中文演示指南](../docs/data/stage-07-demo-guide.md)；个人自测不由工程验收代签。