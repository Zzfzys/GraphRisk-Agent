# 原 Stage 09 路线 8：个人复现说明

源码在 C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent；环境、缓存、数据、生成证据在 E:/GraphRisk-Agent。以下命令使用当前 C 盘源码，不能省略 PYTHONPATH 后误用 E 盘旧源码。每个新 PowerShell 都先执行初始化。

## 1. 初始化和查看版本

```powershell
. 'E:\GraphRisk-Agent\scripts\enter_project_env.ps1' | Out-Null
$stage09Source = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$stage09Evidence = 'E:\GraphRisk-Agent\reports\generated\stage09\route8\20261017-final-v1'
$env:PYTHONPATH = "$stage09Source\src;$stage09Source;$stage09Source\scripts"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\.venv'
Set-Location 'E:\GraphRisk-Agent'
git -c safe.directory=C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent -C $stage09Source log -1 --oneline
uv run --offline --no-sync python -c "import sys,graphrisk; print(sys.version); print(graphrisk.__file__)"
```

预期 Python 3.11、graphrisk 从 C 盘 71df/src 导入。需要已同步的锁定环境；本轮不是新机器冷安装验收。不要为复现升级依赖、重训或下载模型。

## 2. 只读核对最终证据

在同一个初始化后的 PowerShell 运行：

```powershell
$stage09Check = Join-Path $stage09Evidence ('personal-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage09Source\scripts\run_stage09_final.py" check --output-dir $stage09Check --evidence-dir "$stage09Evidence\evidence-v1" --demo-dir "$stage09Evidence\demo-v4" --regression-root "$stage09Evidence\regression-v1"
```

预期 `action=check, status=passed`。它检查历史结果、源码绑定、回归、旧恢复、备份和模型闭包；不启动 Docker、不生成新报告、不改账本。路径或源码已变时应停止并登记新版本证据，不能忽略失败。500 多份旧工件的哈希检查可能需要等待。

新的人工核对输出为独立目录，不能覆盖 check-v1、evidence-v1 或失败轮次。已有 GitHub CI 的实际提交/run 另见本轮私有 acceptance.json；本地与 Linux 平台 skip 原因分开。

## 3. 启动一个新的历史报告回放 API

需要 Docker Desktop 已运行，且已有 pgvector/pgvector:0.8.6-pg17-bookworm 镜像。正常打开 Docker Desktop，等待 Engine ready；不用修改 WSL 或旧项目。

在完成初始化的第一个 PowerShell 运行：

```powershell
docker info --format '{{.ServerVersion}}'
$stage09Demo = Join-Path $stage09Evidence ('personal-demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage09Source\scripts\run_stage09_final.py" serve --output-dir $stage09Demo
```

看到 `mode=historical_real_report_replay_no_new_inference` 和 service_file 后保留这个窗口。程序创建带随机名称和所有权标签的新数据库及新审批记录，API 使用空闲 loopback 端口；不接入旧数据库，不读取系统中已有 DSN。数据库管理凭据由程序生成，用户无需填写连接串。底层复用路线7的隔离沙箱，所以资源名称可能含 r7，归属仍由本次 lease 精确检查。

两份报告来自 5A 的真实 4B 历史结果，当前只回放，不调用 XGBoost、GraphSAGE、Ollama 或付费 API。页面中的 Token/生成次数/耗时来自当时报告，不是本次调用。其模型是当时的 xgboost-v1/candidate-4；不能称作当前增强 XGB 的新 Agent 实测。

API /ready 在此模式中返回 not_ready/503，因为没有加载风险模型、图模型或 Ollama；读取回放报告仍可用。这与运行实际生产调查服务的 ready 要求不同。只有两笔固定请求可回放，不能随便输入其他交易或修改政策日期。

单进程 RSS 上限1280MiB、可用RAM底线1.5GiB，数据库384MiB；这些不是整机峰值保证。运行最长约1小时，审批窗口约30分钟；超时后重新建一个新演练目录，不改旧记录。不会自动关闭其他应用。

## 4. 启动工作台，操作两笔案例

打开第二个 PowerShell，再执行第1节全部初始化，然后：

```powershell
$stage09Demo = (Get-ChildItem -Path "$stage09Evidence\personal-demo-*" -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
if (-not $stage09Demo) { throw '请先在第一个窗口启动个人回放 API。' }
$stage09Session = Get-Content -LiteralPath "$stage09Demo\service.json" -Raw | ConvertFrom-Json
if ($stage09Session.mode -ne 'historical_real_report_replay_no_new_inference') { throw '当前服务不是已核对的历史回放模式。' }
$env:GRAPHRISK_STAGE06_API_URL = $stage09Session.api_url
$env:GRAPHRISK_HISTORY_REPLAY = '1'
$stage09Session.views | ForEach-Object { 'http://127.0.0.1:8502/?run_id=' + $_.run_id }
uv run --offline --no-sync streamlit run "$stage09Source\streamlit_app.py" --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

打开刚打印的两个链接。若8502已占用，在命令和链接中一起换成空闲端口，例如8503；不要结束其他服务。多个个人演练同时运行时，明确选择自己第一个窗口打印的目录，不按“最新”猜测。

页面必须看到“历史真实报告回放”提示。先读风险主分数、版本、图状态、关系线索、政策原文与日期、未知条件；分数未校准，共享设备不证明同一人或团伙。不要把模型原始判断通过率当欺诈识别准确率。

- 完整证据案：填写复核员演示ID和备注，点击“拒绝复核建议”，再刷新。预期“已人工拒绝”、外部处置0；拒绝的是建议，系统没有封卡或通知。
- 图不足案：图分数应不可用，不能显示为0；PostgreSQL当前规则不为 partial_evidence 建立审批，因此没有审批按钮。API 的审批用 report_sha256/session_id 为空，但数据库内部报告哈希仍保存；这与内存演示模式有差别。不要因此批准缺证据报告。

刷新不应再次运行模型；API重建实例后的恢复已由工程演练验证。无当期政策、模型失败、超时等情况另由50/15机制案例验证，不冒充额外真实调查质量样本。

## 5. 停止与资源清理

先在工作台窗口 Ctrl+C，再在回放API窗口 Ctrl+C。API退出会精确清理本轮自己的数据库容器/匿名卷与密码文件；检查本次目录的 sandbox-cleanup.json。测试结果和学习笔记保留在E盘。

不要运行旧项目的 down -v，不要使用通配符删除Docker卷。如果窗口被强制结束、电脑断电或Docker失联，先保留 sandbox-boundary.json 和日志，按其中精确名称及 lease 核查；清理未证实前不能宣称完成。关闭Docker Desktop由你决定，本路线不会修改或删除旧镜像/卷。

路线7的未加密合成备份继续私有保留；本轮复核通过不意味着自动删除。你决定未来归档或清理时再记录位置与状态；删除后完整旧备份check会按设计失败，不能偷偷放宽。

## 6. 跑一组CPU回归

在另一个初始化后的窗口，先停止个人回放和工作台以减少资源竞争，然后：

```powershell
$stage09Regression = Join-Path $stage09Evidence ('personal-regression-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$stage09Source\scripts\stage09_ci.py" cpu --output-root $stage09Regression
```

查看 cpu/public-summary.json。CPU层显式排除4个数据库文件；缺DSN跳过不算数据库通过。需要复现26项真库和15受限探针时，用同一个新output-root将cpu改成database，并先检查Docker；该层只建立新的随机数据库，不配置或公开DSN。

## 7. 本人学习150分钟与交付边界

45分钟操作上述两笔不同案例；30分钟解释一组回归/版本/时间边界；30分钟分析一个真实生成错误或GNN负实验；30分钟各练一段90秒Agent和GNN讲述；15分钟整理未知与后续计划。模板见[本人复核表](stage-09-personal-review.md)。L8和前面尚未完成的本人学习/至少10案人工实用性复核分别保留Pending，不能由工程测试代签。

Agent讲述要区分30笔基线、同集重放、5A的30回归/10新增/开发/重复，以及一笔Docker烟测；GNN讲述要解释强树基线、公平对照、三折alpha=1/增量0和不晋级。现有真实质量证据证明的是受限结构化判断，不是自由文本推理、欺诈真值或当前5B全部输入下新模型质量。

本阶段工程可以有结论，但Stage09整体在本人学习与人工复核完成前继续In Progress。后续GNN关系模式检索由用户另行启动，最多一周探索；Stage10同样尚未开始，不自动扩展为生产鉴权、多worker或生产DR承诺。
