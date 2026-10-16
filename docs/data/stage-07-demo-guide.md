# Stage 07 中文演示与学习复盘指南

> 工程验收见[路线 8 报告](../../reports/stage-07-route-8-acceptance.md)。个人 L0～L8 练习由学习者自己完成。该系统是本地作品演示：模拟政策、未校准分数、未认证复核员、无自动处置。

## 1. 启动和固定交易

在仓库根目录使用 Python 3.11、锁定的 uv 环境和已准备好的受限 IEEE-CIS/模型/图/政策工件。按[Compose 操作指南](stage-07-compose.md)准备本地忽略的两个 secret 文件、Stage 06 基础镜像、pgvector 镜像及固定 digest 的宿主 Ollama。不要在终端输出数据库密码，不要对真实业务库运行故障探针。

```powershell
docker compose -f compose.stage07.yaml config --quiet
docker compose -f compose.stage07.yaml up -d --no-build
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
```

`ready` 必须显示 data、risk_model、graph、policy_index、ollama、postgres 全部可用。打开 `http://127.0.0.1:8501`，输入交易 `3488959`、政策日期 `2026-07-01`、图邻居上限 3，点“开始新调查”。记下 URL 的 `run_id`、审批区的报告 SHA-256 和到期时间。检查 XGBoost、GraphSAGE/融合分数、严格历史邻居状态、政策标题/版本/章节/原文出处、审计中的工具和版本。该交易有图分数但合格历史风险邻居为 **0**；“0 个邻居”不是“无风险”。

## 2. 在待审状态重建 API

保持该运行 `pending`，在另一 PowerShell 窗口执行：

```powershell
docker compose -f compose.stage07.yaml up -d --no-deps --force-recreate api
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
```

刷新工作台原 URL。应是**同一个** `run_id`、报告哈希、政策日期、模型/索引版本和审批截止时间，仍可读取 `pending`；重启不应重新推理或延长审批。填一个仅供演示的复核员 ID 和备注，选“拒绝复核建议”。确认状态 `rejected`、审计为 `created,rejected`、外部处置 0。完全相同决定的 API 重试仍只有一次决定事件；相反批准返回 409 `approval_conflict`。不要把未认证 ID 写成真实身份认证。

## 3. 安全失败与数据边界

政策日期改为 `2025-12-31` 并**主动发起新调查**：应是 `insufficient_evidence`、无引用、无审批、生成尝试 0。数据库故障练习只在本地演示栈执行，先确认当前运行 ID，然后：

```powershell
docker compose -f compose.stage07.yaml stop db
# /api/v1/ready、旧运行 GET 和新调查 POST 应返回 503。
docker compose -f compose.stage07.yaml start db
# ready 恢复 200，原 run_id/报告哈希/决定仍可读取。
```

这里的 API 503 是安全停止；不能在 DB 故障时偷偷改为内存新运行。模型断连、超时、审批到期和图服务断连见[冻结挑战报告](../../reports/stage-07-route-6-agent-evaluation.md)：其中有受控故障注入，不等同于本机 Ollama 或真实网络事故。数据库卷保存运行、审批、费用和 16 条 pgvector 政策向量。普通 `docker compose down` 保留卷；`down -v` 会删除卷，不能用于普通重启演练。演练后可保留运行中的工作台，或用 `docker compose -f compose.stage07.yaml down` 释放内存。

## 4. 亲自完成的学习检查

请自己写下并验证以下问题，再在[学习路线](../plans/stage-07-learning-roadmap.md)记录 L8 完成情况：

1. `request_id`、`run_id`、`session_id`、报告哈希、审批到期时间分别绑定什么？一次 GET 为什么产生新的请求 ID，却不能产生新报告？
2. 为什么必须先保存 LangGraph 检查点、再公开 `pending`？重启后怎样证明没有重复计费或重复决定？
3. pgvector 为什么先过滤政策生效日期、再核验 `chunk_id`、出处、正文哈希和模型 digest？无政策时为什么不让模型补写条款？
4. Stage 06 默认内存模式重启返回 410；Stage 07 PostgreSQL 模式可恢复，但部分图证据不开放审批。这两种模式如何准确介绍给面试官？
5. 50/50 旧 fake 案、15/15 新挑战案、三个同交易真实小模型样本，以及一条 held-out 演示各能证明什么、不能证明什么？

建议亲手重演一条重启拒绝和一条 DB 断连；工程报告不能代替个人操作。

## 5. 90 秒面试口述

**0～20 秒：**“这是一个本地欺诈调查 Agent。输入一笔 IEEE-CIS held-out 交易和政策适用日期，系统查交易历史、XGBoost、GraphSAGE 图证据与模拟政策，输出有引用的报告。模型分数未校准。”

**20～40 秒：**展示 0 个合格历史风险邻居和一条政策出处；说明共享实体只是一种启发式关系，引文匹配不等于政策自动适用。

**40～65 秒：**展示 `run_id`、冻结哈希、请求/工具轨迹；在 `pending` 时重建 API，再用同一运行拒绝。审批不触发封禁或拒付。说明 Stage 06 内存模式旧运行重启为 410，Stage 07 显式 PostgreSQL 模式可恢复。

**65～80 秒：**展示无政策时零引用/零生成，以及 DB 断连时 503；说明为什么不能退回内存伪装成功。

**80～90 秒：**交代旧 50/50、新 15/15 与三个同交易真实模型样本的不同证据范围；指出模拟政策、未认证复核员、单 worker、未校准分数和多 worker/生产备份未验收。