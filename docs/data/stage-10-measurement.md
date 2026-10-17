# Stage 10 路线 1：如何核对实验合同与运行器

2026-10-17：工程材料就绪，本人 L1 Pending。这里的 `check` 和 `dry-run` 不调用真实模型；路线 2 才在独立执行登记后实测。

## 先看这轮做了什么

我们先定好“考哪 10 笔、怎么算通过、最多问模型多少次”，再观察新答案。验证/测试各 5 笔，按时间和 ID 排好后均匀取开头、四分之一、中间、四分之三、末尾；排除历史清单中匹配覆盖集合的 49 个 ID，不读取分数或欺诈标签筛选。重复固定为第一笔验证和第一笔测试。

首次 10 案至少 9 案来源/受限判断联合通过、捏造证据和越权发布为 0；失败、超时和未运行不移出分母。重复不能替代首次失败。这是小样本有界判断门禁，不是欺诈准确率。

旧 Stage 09 评估生成台账仍为 130/144。新台账绑定旧 hash，全阶段最多追加 14 次实际 HTTP 请求，包含重试/失败；12 个计划 run 不等于永远只发 12 个请求。发之前先落盘预约，响应返回再追加完成；超时/崩溃结果不明则停止，不能自动重发或退还额度。CLI 没有 reset/refund 功能。

## 1. 初始化当前源码

```powershell
. 'E:/GraphRisk-Agent/scripts/enter_project_env.ps1' | Out-Null
$stage10Source = 'C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent'
$stage10Frozen = 'E:/GraphRisk-Agent/reports/generated/stage10/route1/20261017-frozen-v1'
$stage10Work = 'E:/GraphRisk-Agent/reports/generated/stage10/route1/20261017-contract-v1'
$env:PYTHONPATH = "$stage10Source/src;$stage10Source;$stage10Source/scripts"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:UV_PROJECT_ENVIRONMENT = 'E:/GraphRisk-Agent/.venv'
Set-Location 'E:/GraphRisk-Agent'
```

## 2. 只读核对已经冻结的实验

```powershell
uv run --offline --no-sync python "$stage10Source/scripts/run_stage10_measurement.py" check --output-dir $stage10Frozen
```

预期 `status=passed`、10 first cases、queue=12、真实 ledger 的 appended_attempts=0/remaining=14（真实实测开始后按实际累加，不要求永远为0）。check 核对原始源码、配置/政策、覆盖依赖、历史排除输入和台账链，不创建或重置台账。它可能需要等待文件 hash 检查。

原始冻结清单含 391 个源文件；随后在任何真实输出前新增的计时模块和测试，作为 `verification-v2/source-appendix.json` 的 2 文件附录封存。check 会显示这两个新增文件的 hash；它们没有改原始题目、预算或 HTTP 守卫。路线 2 还必须在发送前绑定实际执行入口、附录和全体代码版本，不能把“新增文件被列出”当成执行已登记。

不要删除 `reports/generated/stage10/generation-ledger.jsonl` 后重新 freeze，也不要重写旧 Stage 09 台账。完整性失败时保存错误，不绕过检查。

## 3. 跑一次明确的模拟演练

```powershell
$stage10Mock = Join-Path $stage10Frozen ('personal-mock-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage10Source/scripts/run_stage10_measurement.py" dry-run --output-dir $stage10Frozen --result-dir $stage10Mock
```

预期 `mock_http_and_fake_clock_only`、quality_evidence=false、performance_evidence=false、new_real_llm_calls=0。它通过原生 OllamaQualityProvider 发向 MockTransport，创建单独 fixture 台账，走完 12 次模拟发送；真实台账不增加。模拟的 25ms 来自假时钟，不能宣传性能或 Agent 质量。

当前 CLI 只有 freeze/check/dry-run，无真实 run 入口。freeze 已完成，不是日常复查命令；新的实验目录也不能重新获得 14 次额度。

## 4. 运行离线测试

```powershell
$stage10Tmp = Join-Path $stage10Work ('personal-tests-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync pytest "$stage10Source/tests/unit/test_stage10_measurement.py" "$stage10Source/tests/unit/test_stage10_telemetry.py" --basetemp $stage10Tmp -q
```

新增 47 项已纳入最终 CPU 回归 898 passed/1 Windows 权限 skip，不重复累加。显式 E 盘 basetemp 防止 C 盘工作树临时目录权限错误。测试只用小型夹具和模拟 HTTP，不需要 Docker/Ollama 实际在线。

## 5. 跟着代码学习 75 分钟

| 时间 | 实际任务 | 阅读位置 |
|---|---|---|
| 15min | 用 10～40ms 手算 P95=38.5ms；给 1～10ms 加一个1000ms超时，比较全请求P95=505ms与仅成功P95=9.55ms | [percentile/summarize](../../src/graphrisk/demo/stage10_measurement.py) |
| 15min | 读采样与 manifest，解释只选时间/ID、5+5、排除旧案、重复不加分 | [select_cases](../../src/graphrisk/demo/stage10_measurement.py)、[冻结入口](../../src/graphrisk/demo/stage10_registration.py) |
| 20min | 追踪预约→HTTP→响应/不确定，解释为何14次含失败、GET tags不算生成、超时不能自动再发 | [AppendLedger/GuardedTransport](../../src/graphrisk/demo/stage10_measurement.py) |
| 15min | 区分 HTTP 200 与正确报告；读两个质量工作流测试，说明错判断为什么无报告 | [离线测试](../../tests/unit/test_stage10_measurement.py) |
| 10min | 看 Context/Envelope、开始结束UTC、单进程RSS/系统余量/未知显存；解释这些采样不是整机峰值 | [计时附录](../../src/graphrisk/demo/stage10_telemetry.py)、[附录测试](../../tests/unit/test_stage10_telemetry.py) |

在 E 盘私人学习笔记记录预测、命令、版本、结果和自己的解释。本人 L1 仍 Pending，代理没有代签。配套：[实现](../plans/stage-10-implementation-roadmap.md)、[学习](../plans/stage-10-learning-roadmap.md)、[验收](../../reports/stage-10-route-1-measurement-contract.md)。
