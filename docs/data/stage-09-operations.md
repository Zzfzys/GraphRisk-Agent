# 原 Stage 09 路线 7 操作与学习说明

本路线让我们回答三件事：一次调查在哪里失败、输入分布是否发生变化、数据库备份能否真正恢复。它没有改模型权重，也没有提高 Agent 的真实质量成绩。当前源码在 C 盘 71df 工作区，环境与生成证据在 E 盘。

## 日志怎样看

沿 `request_id → run_id → attempt_id` 查找日志前缀 `graphrisk_operation`。旧 `graphrisk_trace` 保留原格式，新日志补齐运行摘要；读取或幂等重放不是一次新执行。不要把两个日志流重复相加。

| 字段 | 含义与限制 |
|---|---|
| request_id / run_id | 服务端请求与调查标识；旧结果未记录的 request_id 保留 null，不补造 |
| kind / tool / attempt_no / attempt_id | 整次运行、工具、依赖尝试、HTTP 或审批；attempt_id 由运行及尝试位置确定 |
| versions | ML、GNN、模型决策、LLM、Prompt、政策的哈希绑定；运行摘要给出完整已知版本，HTTP/审批只含当时可用的绑定，reported_model 表示旧接口传入的 revision-or-model，不能冒充固定 revision |
| status / error_family | 终态与失败类别，区分不足、schema、依据、超时、存储、生成等；不输出原始异常文本 |
| elapsed_ms | 当前事件耗时；工具、依赖、整次运行与 HTTP 延迟分别统计，不相加冒充端到端 |
| prompt_tokens / output_tokens | 实际观察值；缺失为 null，汇总同时报告观察分母 |
| generation_attempts / retries | 生成发送与重试计数；幂等读取不再计入 |
| graph_state | 图证据 available/unavailable/not_recorded；无图不等于无风险 |
| report_sha256 / prompt_sha256 | 对照原证据的绑定；日志不包含完整 Prompt、原始交易或审批备注 |
| cold_warm | 本轮全部 unknown；Ollama 的 load_duration 单列，不能凭毫秒阈值推断冷/热启动 |
| external_action_count | 固定 0；审批记录不执行封卡或冻结 |

哈希是同一版本的关联信息，不是加密或匿名化保证。日志仍按内部工程证据管理。故障演练扫描新的 API 响应和两个白名单日志前缀，不宣称检查了所有电脑日志。

## 看运行统计

`metrics-v1/result.json` 把路线 1、5 各 30 笔主案和各 3 次重复分开。两组是同一批 30 笔交易，不是 60 笔独立交易。`saved_quality_pass` 复述当时评分，不是本轮新增实测；verified 也不是欺诈标签真值。依据不足、图不可用、schema 失败、依据失败分别看。

主案端到端 P50 约 1.91 秒，两组 P95 约 2.03/1.99 秒；只描述这次历史小样本，不能作为生产 SLO 或并发容量。旧案没有 request_id 和独立冷/热启动观测时明确缺失。完整工具耗时、Token、失败类别与重复案见私有 JSON。

## 看漂移

参考分箱在比较前写入 `reference.json` 并绑定哈希。只读取既有预测与两列数值特征，不读取标签、不重算预测。按严格时间边界划分前 44,289 / 后 44,292 条，同一时间不跨组。固定同一个模型和分数文件才能比较分数分布。

本地合同使用 PSI 0.10 为 warning、0.25 为 alert，0.5 计数平滑；含低/高溢出与缺失桶，恒定参考也能比较。门槛用于本项目演练，不是业务验证后的标准。实际历史金额、24 小时交易数、风险分数 PSI 约 0.002737、0.081160、0.002705，均低于预先冻结门槛；不证明模型准确或未来稳定。

合成扰动另列：金额大幅平移，并向每列加入 25% 缺失，三列均 alert。合成测试只证明告警机制能响，不能作为真实数据漂移。没有新标签，不能从 PSI 推断 ROC-AUC/AP 下降；这是已观察历史测试集，也不是新盲测。

## 如何复核或重新演练

每个新 PowerShell 先执行以下初始化。所有输出必须用新目录，禁止覆盖前次失败或通过证据。

```powershell
. 'E:\GraphRisk-Agent\scripts\enter_project_env.ps1' | Out-Null
$stage09Source = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$stage09Evidence = 'E:\GraphRisk-Agent\reports\generated\stage09\route7\20261017-ops-v1'
$env:PYTHONPATH = "$stage09Source\src;$stage09Source;$stage09Source\scripts"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\.venv'
Set-Location 'E:\GraphRisk-Agent'
$stage09Check = Join-Path $stage09Evidence ('personal-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage09Source\scripts\run_stage09_operations.py" check --runtime-root 'E:\GraphRisk-Agent' --output-dir $stage09Check --metrics-dir "$stage09Evidence\metrics-v1" --drift-dir "$stage09Evidence\drift-v1" --recovery-dir "$stage09Evidence\recovery-v3"
```

预期输出 `action: check, status: passed`。它复核输入、合同、资源、15 个探针、备份字节哈希、源/恢复逻辑清单、清理与边界记录，不启动 Docker、不训练、不调用 LLM。保留备份文件才能再次通过完整复核；将来归档/清理后应记录状态，不能悄悄放宽校验。

需要亲自重复数据库演练时，先确保 Docker Engine 可连接、本机已有 `pgvector/pgvector:0.8.6-pg17-bookworm`，再在相同初始化下运行：

```powershell
$stage09Recovery = Join-Path $stage09Evidence ('personal-recovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage09Source\scripts\run_stage09_operations.py" recovery --runtime-root 'E:\GraphRisk-Agent' --output-dir $stage09Recovery
```

程序只创建有随机名称与所有权标签的新容器，384 MiB 数据库限制、loopback 端口；创建 source 与另一个全新空 restore 库。它会停止/重启自己的源库验证断线，再实际 `pg_dump --format=custom --no-owner --no-privileges`，通过 `pg_restore --exit-on-error` 写入新库；不会操作旧项目容器或卷。恢复后检查三个审批状态、报告哈希、检查点、幂等、合成预算与 pgvector，继续原待审并验证冲突。

12 表逻辑清单覆盖行内容、列/约束/索引和扩展版本；numeric 使用数据库精确文本，避免大金额的小数变化被 float 吞掉。库所有者与权限不计入内容等同，恢复后重新授予并验证受限应用角色。损坏的备份须先拒绝；不对已有库执行 --clean 或覆盖恢复。

备份在 `E:/GraphRisk-Agent/artifacts/stage-09/route7/<批次>/<演练>/`，属于未加密私有合成调查备份；不提交 Git、不进入 wheel/镜像，不放同步公开目录。密码仅本轮内存和只读挂载文件，退出时移除；本轮拥有的容器/匿名卷清理，备份保留至路线 8 人工复核后由用户决定归档/清理。Windows chmod 不是完整 NTFS ACL 隔离，不能当秘密管理或生产备份策略。

## 已验证的故障与边界

模型不可用及 ReadTimeout 均最多 2 次尝试、1 次重试；总截止使用受控时钟验证，不宣称硬杀死任意卡死进程。无当期政策时不生成；返回旧政策版本或注入 auto_block 时关闭失败。版本匹配控制组正常，GNN 版本不匹配拒绝。真实 DB 停止时返回 503，重启后可读；真正恢复另库后幂等创建的 runner.calls 仍为 0。

本轮故障/恢复演练中的生成/embedding 是机制 fixture，不代表真实语义效果。新训练、真实 LLM、付费调用均 0。演示是单 worker；reviewer_id 未认证，没有实际封禁工具。生产鉴权、授权、多 worker、加密/异地/全实例恢复、生产 RTO/RPO 与告警通知均未验收。

## L7 本人练习：三次 30 分钟

1. 30 分钟：读取 `recovery-v3/trace.jsonl` 与 `completed-probes.json`，选 DB 503 和模型失败各一条，画 request/run/attempt 链；指出重试上限、终态和缺失字段。不要把私有响应贴入公开笔记。
2. 30 分钟：对照 `drift-v1/reference.json` 和 result，解释分箱、阈值、缺失与合成扰动。写出“分布稳定不等于准确、漂移也不等于退化”的具体原因；用两笔样本手算 P50/P95，指出小样本限制。
3. 30 分钟：运行上面的只读 check，核对备份哈希、source/restored 逻辑哈希与 sandbox-cleanup，解释为什么 down/up 不等于恢复新库；说明待审恢复、终态冲突和幂等不再生成。

交付两张事件链、一张恢复核对表和三条限制，保存在私有学习目录。本人未亲手完成前，L7 一直 Pending；代理运行通过不能代签。
