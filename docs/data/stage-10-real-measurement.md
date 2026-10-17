# Stage 10 路线 2：实测操作与学习

这次测的是当前增强 XGBoost 配置下，Agent 能否忠实使用工具证据，以及耗时在哪里。它不是欺诈识别准确率实验，也不重新训练模型。首次10笔交易和2次固定重复沿用路线1清单；重复不增加首次质量分母。

## 先设置本机路径

```powershell
. 'E:/GraphRisk-Agent/scripts/enter_project_env.ps1' | Out-Null
$stage10Source = 'C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent'
$stage10Real = 'E:/GraphRisk-Agent/reports/generated/stage10/route2/20261017-real-v2'
$env:PYTHONPATH = "$stage10Source/src;$stage10Source;$stage10Source/scripts"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:UV_PROJECT_ENVIRONMENT = 'E:/GraphRisk-Agent/.venv'
Set-Location 'E:/GraphRisk-Agent'
```

## 先核对，再读结果

```powershell
uv run --offline --no-sync python "$stage10Source/scripts/read_stage10_results.py" check --output-dir $stage10Real
uv run --offline --no-sync python "$stage10Source/scripts/read_stage10_results.py" report --output-dir $stage10Real
```

check 校验代码、配置、数据/权重/政策和追加台账，不调用模型。report 重新检查保存的报告/原始判断，生成新的时间戳摘要，不重新生成报告。原始请求和交易仅存私有E盘，不要上传这些目录。

已登记的 register 和已执行的 prepare 不是日常重跑命令：前者封存实验，后者采集真实工具证据、5笔冻结权重重放和两个各100次只读HTTP组；不覆盖已有输出。首版real-v1保留为监控开销诊断；正式real-v2在任何模型输出之前重登记同样的题和唯一台账。

封存的run是真实模型历史入口，本次12条结果均已完整保存。它最后汇总时把两个组件共24条观测误传为12，报错发生在生成和保存全部完成之后；只读结果入口独立重算，未修补任何模型答案。新增分析文件后，原执行器的完整源码校验会拒绝新调用；日常使用上方只读命令。不要删结果、started或台账重发，也不要点击网页来补这批样本。

需要亲自复测无LLM层时，使用新的私有子目录：

```powershell
$stage10Repeat = Join-Path $stage10Real ('readonly-repeats/personal-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage10Source/scripts/read_stage10_results.py" benchmark --output-dir $stage10Real --repeat-dir $stage10Repeat
```

该命令会执行10笔工具取证、5笔冻结CPU权重重放和200次只读接口，可能运行数分钟；至少保留1.5GiB系统可用内存及服务余量，不启动真实LLM、不训练、不改变生成台账。使用原测量代码快照，记录新的时间与来源，不覆盖首次性能结果，不增加真实Agent质量分母。资源不足时保存失败，不强关应用。

## 每类记录代表什么

| 文件 | 阅读问题 |
|---|---|
| execution.json / source-snapshot | 观察输出前究竟登记了哪套代码、输入和口径？ |
| prepared.json / snapshots | 工具实际查到了哪些交易、历史、关系、分数和政策？ |
| prospective/ | 重新加载冻结权重的5笔历史新事件重放，能否复现旧分数？不是新盲测 |
| prepared.json 的 fast | health/coverage各100个HTTP往返；逐接口统计，不与生成混算 |
| started/ | 请求前的时间、输入绑定、模型驻留和台账位置 |
| results/ | 原始模型输出、来源/判断评分、完整HTTP及操作计时、报告和审批状态 |
| *resources*.json | 当前测量服务RSS、整机可用内存、Ollama各进程RSS；这是采样，不是整机内存峰值或显存使用保证 |
| summary-*.json | 保存结果的独立重算；未运行留在分母，重复不补首次失败 |

一次调查包含工具阶段、LLM HTTP往返、校验和审批准备。HTTP200只说明模型响应回来；API201只说明保存了调查，不代表判断通过。操作级Envelope的ok表示调用正常返回，API错误状态和业务质量须另外看http_status/grade；不能把正常返回等同报告合格。

冷暖只凭发送前/api/ps中匹配的digest判断；keep_alive=0可能每次都冷。load_ms来自Ollama响应；请求Token和输出Token无字段时留空。API小组100次允许描述本次经验P99，不代表生产SLO；12次Agent的P99为空，P95也只描述这批小样本。

## 两小时学习任务

1. 30分钟：读[执行入口](../../src/graphrisk/demo/stage10_execution.py)的register/validate/prepare/run，画出冻结→取证→生成→评分→保存。解释为什么恢复不能只看网页有没有报告。
2. 30分钟：对照一笔snapshots、results与HTTP原始判断，自己判断历史/关系/图是否可用，再看grade。不要把自动检查当个人实用性复核已完成。
3. 30分钟：从prepared和summary抄出样本数/P50/P95/错误数/冷暖，分别解释工具、权重、API、LLM和全调查的计时范围；不能相加不重叠范围不明的数字。
4. 30分钟：看资源采样最低余量、各进程RSS和load_ms，写一个有证据的瓶颈判断与一个仍不知道的问题。核对旧130次账本未改变，新增次数来自JSONL预约数。

个人学习记录放reports/generated/stage10/learning/，记录操作前预测、实际命令、版本、失败和自己的解释。L2仍由本人完成。配套：[实施路线](../plans/stage-10-implementation-roadmap.md)、[学习路线](../plans/stage-10-learning-roadmap.md)。
