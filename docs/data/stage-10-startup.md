# Installation, startup and operating modes

2026-10-18；本指南面向新读者的一份源码检出。作者本机的C源码/E运行目录请使用[专门说明](stage-10-author-workspace.md)。当前运行行为以[架构](../architecture/stage-10-system.md)和[最终评估](../../reports/stage-10-final-evaluation.md)为准。

## Installation

选择你有写权限、空间足够的项目盘。安装Git、uv后，在PowerShell中克隆并选择已有交付分支：

```powershell
git clone https://github.com/Zzfzys/GraphRisk-Agent.git GraphRisk-Agent
Set-Location GraphRisk-Agent
git switch codex/stage09-version-closeout
. .\scripts\enter_project_env.ps1 | Out-Null
uv sync --locked --group demo
uv run --offline python --version
```

预期Python3.11。首次sync可能联网下载Python和依赖，根项目含较大的PyTorch；只有本机已缓存才加`--offline`。demo组安装Streamlit，dev默认组提供pytest。后续新PowerShell仍要先初始化环境。不要用`pip install -U`覆盖锁定版本。

Linux读者可在检出根目录设置等价的项目内路径，再使用同一锁文件：

```bash
mkdir -p artifacts/tmp artifacts/pip-cache
export TMPDIR="$PWD/artifacts/tmp"
export PIP_CACHE_DIR="$PWD/artifacts/pip-cache"
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.uv-python"
uv sync --locked --group demo
```

Linux命令按源码/锁文件核对，本路线实际执行环境为Windows，未作本轮全新Linux安装。Windows以当前安装用户运行，缓存/环境留在项目内；无需改系统TEMP或管理员ACL。

## Public CLI fixture and synthetic data

以下命令在检出根目录运行：

```powershell
uv run --offline python scripts/evaluate_stage06_agent.py
uv run --offline python scripts/generate_synthetic_data.py --config configs/dev.yaml
uv run --offline python scripts/build_features.py --config configs/dev.yaml
```

第一条输出`fake=50/50 real_samples=0`和带时间戳的报告路径，走真实应用机制但使用确定性fixture数据/模型。后两条产生5000笔合成交易及严格历史/时间切分，结果留data/和reports/generated/下；不是IEEE-CIS、真实训练或真实LLM成绩。dev配置开启合成数据重建，重跑会更新它自己的合成产物，勿把它指到你的真实数据目录。首次50案没有网页，`--real`会额外调用旧模型样例，不属于这个公共入口。

## Current real investigation

需要Python3.11锁定环境、Streamlit、本机Ollama，以及授权IEEE-CIS和已经生成且hash匹配的工件：

| 输入 | 准备/检查依据 |
|---|---|
| 标注交易与identity，规范交易及严格历史特征 | [数据卡](stage-09-5b-data-card.md)、configs/ieee_cis.yaml |
| 既有树/图权重、预处理、分数、时点图索引及model decision | [模型卡](stage-09-5b-model-card.md)、[覆盖合同](stage-09-coverage.md) |
| 模拟政策索引 | configs/stage09_coverage.yaml的policy路径，校验绑定版本 |
| 当前enhanced覆盖profile | configs/stage09_coverage.yaml，model_policy指向绑定calendar-v2工件 |
| Ollama | qwen3:4b及配置固定digest；模型同名但digest不同仍不能复用 |

这些私有产物不在Git，单独下载CSV或执行`ollama pull`不能保证全部hash匹配。重建需要遵循相应阶段的冻结合同/预算，不能为试运行自动重训或改hash。

对已经准备好同根目录工件的检出，在两个PowerShell分别运行以下脚本，`$runtimeRoot`取该检出根目录：

```powershell
$runtimeRoot=(Get-Location).Path
. .\scripts\enter_project_env.ps1 | Out-Null
& .\scripts\run_stage09_coverage_service.ps1 -ExecutionRoot $runtimeRoot -Component api
```

```powershell
$runtimeRoot=(Get-Location).Path
. .\scripts\enter_project_env.ps1 | Out-Null
$env:GRAPHRISK_STAGE06_API_URL='http://127.0.0.1:8000'
Remove-Item Env:GRAPHRISK_HISTORY_REPLAY -ErrorAction SilentlyContinue
& .\scripts\run_stage09_coverage_service.ps1 -ExecutionRoot $runtimeRoot -Component workbench
```

本脚本设置memory，API监听127.0.0.1:8000，UI监听127.0.0.1:8501。浏览器打开http://127.0.0.1:8501。在第三个初始化后的窗口可只读检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/coverage/3488959
```

health为进程存活，ready核对依赖，coverage检查该交易的合格评分覆盖；这些读取不创建新调查。覆盖资格不等于模型预测正确。真实调查提交字段为transaction_id、dataset_source=ieee_cis、policy_as_of（页面默认2026-07-01）和relation_limit。创建会发送真实模型请求并可能重试；当前研究追加账本剩2次，个人浏览/审批练习优先用历史回放，不反复新建或重发失败题。普通工作台服务的本地费用限制不等于Stage10全局实验次数守卫，正式实验仍用受控运行器。

脚本在前台运行，保留两个窗口；各自Ctrl+C停止本轮启动的进程。已占用端口应先识别自己已有服务；不强制结束其他应用。当前memory服务重启丢失运行与审批；刷新页面继续读取原run_id，不会重新生成。

## PostgreSQL mode

在真实模式工件之外，需要可用数据库、显式迁移和受限角色。DSN只通过本地忽略配置或进程环境注入，应用不会自动加载任意`.env`文件。准备以下环境变量后，在同一源码检出初始化的PowerShell执行：

| 变量 | 含义 |
|---|---|
| GRAPHRISK_STAGE07_MIGRATION_DSN | 有迁移权限的数据库连接 |
| GRAPHRISK_STAGE07_APP_DSN | 专用应用角色连接，供授权脚本使用 |
| GRAPHRISK_STAGE07_DSN | API使用的受限应用连接 |

```powershell
uv run --offline python scripts/migrate_stage07.py
uv run --offline python scripts/grant_stage07_app.py
$env:GRAPHRISK_STAGE06_CONFIG=(Resolve-Path configs/stage09_coverage.yaml).Path
$env:GRAPHRISK_STAGE07_STORAGE='postgres'
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

在另一个初始化窗口运行UI：

```powershell
$env:GRAPHRISK_STAGE06_API_URL='http://127.0.0.1:8000'
Remove-Item Env:GRAPHRISK_HISTORY_REPLAY -ErrorAction SilentlyContinue
uv run --offline streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

不要用coverage启动脚本开启postgres：它会强制设回memory。API不自动迁移，缺schema/数据库不可用时不降级到内存。PostgreSQL在TTL与版本核验通过后恢复既有报告和LangGraph审批检查点，读取/审批无需重跑模型；partial_evidence没有审批。memory为旧兼容语义，部分证据案可能进入审批；文档不将两种模式暗写成一致。

PostgreSQL创建支持Idempotency-Key：同键同请求已发布运行返回200及原run_id，同键不同请求409；未发布/结果未知返回503待核对，不自动重发。审批相同决定重试可返回原结果，不同决定/备注冲突409；已过窗口410。TTL默认运行24小时、审批30分钟，重启不续期。详见[持久化指南](stage-07-postgres-service.md)；其中带日期的临时容器名称是历史记录，不是今天仍存在的服务。

## Docker Compose

已有[Stage07 Compose指南](stage-07-compose.md)与[Stage09 5B容器验收](../../reports/stage-09-route-5b-7-acceptance.md)提供相应版本证据。默认compose.stage07.yaml挂载旧Stage07模型路径和私有文件，不能直接称为当前增强coverage配置；干净克隆也没有挂载所需数据/秘密。需要按其合同准备镜像、工件和secret再使用，不把旧`up -d --no-build`当成通用首次启动命令。

路线4只整理说明，没有重建/启动Docker。数据库卷保存状态；API重启与删卷是不同操作。后续[路线6复现](stage-10-clean-reproduction.md)已在新Git检出和新.venv验证公开模式及既有真实工件，明确复用下载缓存；没有本轮Docker/真库重跑、冷缓存安装或原始数据重训。

## Historical replay

现有回放入口是scripts/run_stage09_final.py的serve动作，需要私有Stage09旧报告、Docker与固定pgvector镜像；它建立新的隔离数据库/API，将旧报告装入后供读/审批练习，不发送模型请求。它有意展示当时xgboost-v1/candidate-4，不是当前增强模型的新成绩。

这是作者环境的已有演练入口，精确双窗口命令见[本机回放](stage-10-author-workspace.md#历史报告回放)。GRAPHRISK_HISTORY_REPLAY只是UI提示标识，不会把一个真实后端切换成回放；必须使用serve输出的api_url和对应run_id。端口/服务文件不得从其他轮次猜测。保留旧run_id的过期/冲突含义。

## Independent GraphRAG

独立项目只有标准库核心运行依赖，不需要GraphRisk/Torch、IEEE或数据库。最轻入口在检出根目录：

使用下面的已实现模块入口验证语料：

```powershell
uv sync --locked --project projects/enterprise-graphrag
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.corpus --dataset projects/enterprise-graphrag/data/corpus-v1
```

预期24 documents、58 chunks、22 nodes、40 edges、30 questions。核心环境与根环境独立；完整检索/问答/8502工作台还需要绑定索引、固定embedding/生成模型和demo环境，见[独立项目](../../projects/enterprise-graphrag/README.md)与[操作指南](stage-08-demo-and-interview.md)。两跳检索和主系统风控政策RAG不是同一数据任务。

## Checks and troubleshooting

| 症状 | 如何检查与处理 |
|---|---|
| uv/pip或Python版本不符 | 用uv和锁文件，检查Python3.11及当前环境；依赖有缓存才加offline |
| 无法连接8000 | API窗口需持续运行；看其首条错误，检查端口和ready；只开Streamlit不会自动开启API |
| health200但ready503 | 进程活着但工件/模型/数据库等依赖未就绪；看components，按配置补齐 |
| ready成功但创建调查503 | 输出/来源/语义校验仍可失败，保留run_id/error_code；当前实测有4次此类拒绝，勿无限重试 |
| Ollama模型同名仍失败 | 配置固定digest；对照本机ollama list和配置，不通过改hash绕过 |
| 页面报告丢失或旧run_id410 | memory重启或TTL；需要恢复时预先使用postgres，不能从已丢失内存恢复 |
| PostgreSQL断连503 | 核对服务、角色/迁移/DSN；不自动切换memory或重新调用模型 |
| pytest出现WinError5临时目录拒绝访问 | 每个窗口先环境初始化；必要时给pytest新的项目内--basetemp，勿删系统TEMP |
| 全量pytest显示数据库skip | 需要隔离测试库；无DSN跳过不算真库通过，勿指向工作台库跑故障测试 |
| 内存/磁盘不够 | 保存进度，停止本轮新实验；项目缓存/临时文件留所选项目盘，不改系统或强关用户应用 |

常规测试用`uv run --offline pytest`，UI行为测试在tests/demo/test_streamlit_app.py。本路线检查了文档命令、结构和链接，在既有环境实际执行公开CLI；没有重跑全套pytest、真库、真实LLM或新环境安装，详细范围见[路线4验收](../../reports/stage-10-route-4-readme-startup.md)。

## 路线6后续验证范围

2026-10-18[新检出复现](stage-10-clean-reproduction.md)实际通过CPU915/1平台skip、公开50案/合成5000行、五真实工具/一笔冻结权重、HTTP/AppTest回放；独立标准库环境91通过/3可选UI skip。与路线4当时的既有环境检查分开。真实输入明确复用E盘工件，环境仅复用下载缓存/managed Python，没有旧.venv或隐藏作者源码。历史严格字节校验发现LF/CRLF限制，按显式封存快照处理；旧CLI未被改成自动适配。

独立项目安装前应显式将UV_PROJECT_ENVIRONMENT设为其自己的.venv，避免继承主项目变量。完整命令与90分钟学习见复现指南。
