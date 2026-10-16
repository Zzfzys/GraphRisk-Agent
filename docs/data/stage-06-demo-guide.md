# Stage 06 中文演示与学习指南

> 工程验收见[路线 7 报告](../../reports/stage-06-route-7-acceptance.md)。本指南供学习者独立复现，演示用本地 IEEE-CIS 数据与原创模拟政策，不展示测试标签。

## 1. 准备环境与工件

在仓库根目录用 PowerShell 和 Python 3.11。按[README 的数据管线](../../README.md#ieee-cis-data-pipeline)、Stage 02 模型、Stage 04 图比较、Stage 05 政策索引步骤构建本地工件；具体路径与固定版本见[演示契约](stage-06-demo-contract.md)。受限 IEEE-CIS 数据、模型文件不在 Git 中，新机器须自行准备。Ollama 须提供固定 digest 的 `qwen2.5:0.5b`，且本机 `11434` 可访问。

```powershell
uv sync --locked --group demo
uv run --offline pytest -q
uv run --offline python scripts\validate_stage05_end_to_end.py
```

最后一条核对 held-out 归属、正常报告、无政策及受控模型断连；日志/运行文件进入忽略的 `reports/generated/stage05/`。缺工件或 digest 不符时按错误信息补齐，不改冻结测试交易与标签。

## 2. 一键本地演示

```powershell
uv run --offline python scripts\run_stage06_local.py
```

出现 `Stage 06 ready` 后打开 `http://127.0.0.1:8501`。输入交易 `3488959`、政策日期 `2026-07-01`、图邻居上限 `3`，点击“开始新调查”。依次检查交易和历史、XGBoost 与 GraphSAGE/融合分数、图查询、模拟政策的标题/版本/章节/原文出处、运行审计。该样例有图分数，但严格历史邻居数为 **0**；不能把空邻居说成“安全”。

在“人工审批”查看报告 SHA-256 与 `pending`，填写演示复核员 ID、备注，点“拒绝复核建议”。应得到 `rejected`、外部动作 0；刷新后同一 `run_id` 仍显示终态，不会重跑模型。复核员 ID 未认证，批准/拒绝都不执行封禁、拒付或通知。按 Ctrl+C 停止本地服务。

打开 `http://127.0.0.1:8000/docs` 可核对 OpenAPI；`GET /api/v1/ready` 应显示五项依赖就绪，`POST /api/v1/investigations` 创建调查，`GET /api/v1/investigations/{run_id}` 重读，`GET/POST /api/v1/investigations/{run_id}/approval` 读取或提交审批。PowerShell 创建请求示例：

```powershell
$request = @{transaction_id='3488959'; dataset_source='ieee_cis'; policy_as_of='2026-07-01'; relation_limit=3} | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/investigations' -ContentType 'application/json' -Body $request
$run.status
$run.report.validation_status
$run.approval.status
```

## 3. 无政策与模型不可用

把工作台政策日期改为 `2025-12-31`，**主动再次点击**“开始新调查”。应显示 `insufficient_evidence`、无引用、无审批按钮；API 审计中的 `generation_attempts=0` 证明没有让模型补造政策。模型不可用时运行 `uv run --offline python scripts\validate_stage05_end_to_end.py`，预期终端有 `outage=generation_error`。这里用受控断连 provider，不关闭真实 Ollama；API 503 映射与界面错误由集成测试覆盖。真实服务异常先看 `/api/v1/ready` 和 `reports/generated/stage06-local/` 日志。

## 4. Compose、评估和复盘

先停止本地启动；Docker Desktop 与所需本地工件可用时运行：

```powershell
docker compose config --quiet
docker compose up -d --no-build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
docker compose down
```

新机器没有 `graphrisk-stage06:local` 镜像时改用 `docker compose up --build -d`，并预留磁盘空间。API 通过 `host.docker.internal:11434` 连宿主 Ollama；API 数据/工件卷只读，工作台无数据卷。评估命令：

```powershell
uv run --offline python scripts\evaluate_stage06_agent.py
uv run --offline python scripts\evaluate_stage06_agent.py --real
```

前者为固定 fake 50 案；后者另外执行 3 个固定本地模型样本。它们来自同一交易，50/50 也不是欺诈检测准确率。逐案、Token、费用和失败限制见[评估 v1](../../reports/stage-06-route-6-agent-evaluation.md)。

## 5. 90 秒面试口述

0～15 秒：说明系统让风控调查的结论有证据、有出处，重要建议交给人，输入 held-out 交易与政策日期。15～45 秒：展示两个模型分数、严格历史图查询和一条政策原文出处；说明分数未校准、引文核验不等于政策适用。45～65 秒：展示受控工作流审计与冻结哈希，填备注并拒绝，刷新仍为 `rejected`，外部动作 0。65～80 秒：无政策时没有引用、生成或审批；受控模型断连安全失败。80～90 秒：说清 50 案例、3 个同交易真实样本、单进程内存状态、模拟政策及未认证复核员等限制。

独立练习：不看稿解释 `run_id`、`session_id`、报告哈希各自绑定什么；为什么 0 个邻居不是低风险；服务重启后为何旧审批失效；怎样从审计证明无政策时没调用 LLM。个人学习自测由本人勾选，工程测试不能代签。
