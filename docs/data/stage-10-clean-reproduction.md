# Stage10路线6：干净检出与双模式复现

2026-10-18，工程验收通过；本人L6 Pending。这里验证的是**新检出、新虚拟环境、有下载缓存条件下的安装与运行**。没有验证首次联网冷安装、从原始CSV全量重训或新增真实LLM质量。配套[验收报告](../../reports/stage-10-route-6-reproduction.md)、[通用启动](stage-10-startup.md)。

## 本次实际证明了什么

| 层 | 本次结果 | 条件 |
|---|---|---|
| 源码 | 本地Git clone，提交a3f1acc，610受跟踪文件核对，初始无私有工件/秘密/环境 | 不是GitHub联网clone；后续文档提交和远程CI另记 |
| 主环境 | Python3.11.16、147 distributions，新.venv；graphrisk/api/Streamlit/Torch实际路径来自新检出 | uv0.12.18；复用E盘下载缓存与managed Python，没有复制旧.venv |
| 公开模式 | fixture50/50，真实生成0；合成5000行与严格历史特征生成成功 | 没有IEEE、真实权重或作者隐藏数据输入 |
| 主项目回归 | 915 passed、1 Windows符号链接权限skip、0失败/错误 | CPU层；四个数据库测试文件明确排除，不称真库通过 |
| 真实工件 | 177162历史覆盖闭环校验，五个真实工具通过；一笔冻结交易重新执行树/三种子图权重 | 复用授权数据、特征、冻结权重/预处理，不是重训 |
| 分数一致性 | XGB差0，图分数绝对差约1.09e-8，均≤1e-6 | 既见交易一致性检查，不增加质量样本 |
| 报告与页面 | 新环境实际loopback HTTP、AppTest九项行为通过；已存成功201/失败503、审批拒绝/刷新/幂等 | 回放路线2原报告，无新生成；不是本轮新浏览器截图 |
| 独立GraphRAG | 另建自己的.venv，仅enterprise-graphrag一个distribution，Torch/GraphRisk不可导入；语料与91项核心测试通过 | 3个可选Streamlit测试按未安装demo跳过，零真实问答调用 |

真实工件CPU资源记录：最低可用3.251GiB、主进程峰值800.57MiB；沿用1.5GiB/1280MiB合同。树子进程另有记录，不能把主进程当整机峰值。Ollama tags读取确认固定模型可用；没有POST /generate。

## 公开模式：先在一份自己的检出运行

按[启动指南](stage-10-startup.md#installation)克隆、切分支并安装。确认提交；本次测试的a3f1acc及后续交付文档当前只保存在作者本地Git/bundle，尚未推送，不能假定GitHub已包含它。最终推送及对应CI由路线8收口。

每个新PowerShell先初始化，再安装新环境：

```powershell
. .\scripts\enter_project_env.ps1 | Out-Null
$source=(Get-Location).Path
$env:UV_PROJECT_ENVIRONMENT="$source\.venv"
uv sync --locked --group demo
uv run --offline python -c "import sys,graphrisk,api.main,streamlit; print(sys.executable); print(graphrisk.__file__); print(api.main.__file__); print(streamlit.__file__)"
uv run --offline python scripts/evaluate_stage06_agent.py
uv run --offline python scripts/generate_synthetic_data.py --config configs/dev.yaml
uv run --offline python scripts/build_features.py --config configs/dev.yaml
```

所有导入路径应指向这份检出及自己的.venv。`uv sync`首次可能联网，**本轮没有实际执行该冷缓存情形**；依赖完整缓存才可加`--offline`。本机实测使用`uv sync --locked --offline --group demo --project <新检出> --python <E盘既有3.11.16>`，并显式复用E:/GraphRisk-Agent/.uv-cache及.uv-python。安装成功不表示可以删除这些Python文件。

预期fixture输出`fake=50/50 real_samples=0`；合成数据5000行。它们验证公开机制/流水线，没有网页与真实模型质量结论。新检出不要复制旧.venv、.env或私有报告来通过这一步。dev重跑更新自身合成产物，不能指向真实目录。

CPU回归入口可在新输出目录运行：

```powershell
uv run --offline python scripts/stage09_ci.py cpu --source . --output-root artifacts/route6-my-check
```

它为pytest创建自己的项目内临时目录，输出junit/public-summary/source-inputs。已有输出不覆盖；新一轮换目录。数据库另用隔离库，不能为了去掉skip把测试指向工作台数据。

## 真实模式：明确额外输入，先只读检查

| 输入组 | 来源/准备要求 |
|---|---|
| 原始授权数据 | 自行获取IEEE-CIS；遵守许可；[数据卡](stage-09-5b-data-card.md)记录适配/身份启发式/时间限制 |
| 已生成交易/历史特征/评分与图历史 | [覆盖合同](stage-09-coverage.md)，profile绑定的35个文件决定精确输入；仅有CSV不够 |
| 冻结模型/预处理/决定 | 三折XGB、图三种子、编码/归一化及calendar-v2覆盖profile；[模型卡](stage-09-5b-model-card.md)；不修改hash救启动 |
| 政策与生成配置 | configs/stage09_coverage.yaml、已绑定政策索引、qwen3:4b固定digest；不是任意同名模型 |
| 只在历史结果核验/回放需要的输入 | 路线2manifest/execution/逐案原始结果/源快照及Stage09/10账本；它们不是公开fixture前提 |

本轮35文件清单及每个binding存real-inputs.json；profile JSON的content_sha256与文件原始字节SHA是两种口径，不互换。数据/模型准备步骤沿用Stage01～09合同，本轮不自动重训、下载或覆盖旧工件。完整原始数据重训所需的资源/预算与结果仍未在此新环境验证。

若源码和工件在同一根目录，runtime取该根；本机工件仍是E:/GraphRisk-Agent。下面只读检查不创建调查：

```powershell
. .\scripts\enter_project_env.ps1 | Out-Null
$source=(Get-Location).Path
$runtime='E:\GraphRisk-Agent'
$env:UV_PROJECT_ENVIRONMENT="$source\.venv"
uv run --offline --no-sync --project $source python scripts/check_stage09_coverage.py --root $runtime
uv run --offline --no-sync --project $source python -c "from pathlib import Path; from graphrisk.serving.investigation_service import Stage06Service; s=Stage06Service.from_config(Path(r'$runtime')/'configs/stage09_coverage.yaml'); print(s.readiness().model_dump()); s.close()"
```

check输出中的173测试/5旧新事件/3重复是历史验收字段，**不是本轮新跑的数量**。本轮实际重新跑五个工具与一笔冻结权重推理；没有读标签或写回历史。Ollama未开启/版本不符时ready会如实not_ready，公共机制仍可用。

准备好真实模式后，若要启动自己的memory API，直接指明新源码和新环境，避免旧启动器强制改回runtime/.venv：

```powershell
$env:GRAPHRISK_STAGE06_CONFIG="$runtime\configs\stage09_coverage.yaml"
$env:GRAPHRISK_STAGE07_STORAGE='memory'
uv run --offline --no-sync --project $source uvicorn api.main:app --app-dir $source --host 127.0.0.1 --port 8000 --workers 1
```

在自己端口可用时运行，Ctrl+C停止自己启动的服务；UI按通用指南。health/ready/coverage只读；创建调查会产生真实模型请求，不能把它当免费检查。研究账本仍142/144剩2；本轮不消费它。memory重启丢状态，数据库/Compose不在本轮重建范围。

## 换行格式：历史实验核验与当前运行分开

首次`read_results(新检出, runtime, 冻结目录)`报registered_experiment_source_changed。92个登记文件只因LF/CRLF不同；Git检出内容和程序行为未改变，但旧实验按原始字节冻结。没有改旧manifest、结果或放宽hash门槛，也没有复制作者工作区源码来伪造通过。

本轮将封存source-snapshot的396文件逐个验证旧SHA，再逐个与新检出比较，允许的差异只有CRLF→LF；任何其他差异都停止。另附三个已追踪的后处理分析文件。历史只读核验使用这份显式快照；五个工具、真权重、API/AppTest的实际执行仍来自新检出/新环境。

快照目录放在新检出旁的artifacts目录，**不能位于reports/generated下**：source_inventory会排除路径中含generated的文件。一次辅助脚本放错目录也已保留失败记录，发生在任何权重推理前。此处理只是历史核验，不更改新检出的文件或Git状态；旧read_stage10_results.py和图片启动器直接对任意换行的新检出仍可能拒绝，不能称该历史CLI已普适修复。

本机本轮快照：artifacts/stage-10/route6/20261018-reproduction-v1/historical-validation-source；准备/校验代码、来源差异和回放适配记录存私有证据。读者若无这批原始实验快照，应仅运行公开机制或已授权的真实工具，不声称核验了作者私有历史成绩。

## 独立GraphRAG的最小环境

在检出根运行，新PowerShell仍先初始化；给独立项目自己的环境，避免继承主项目UV_PROJECT_ENVIRONMENT：

```powershell
. .\scripts\enter_project_env.ps1 | Out-Null
$project=(Resolve-Path projects/enterprise-graphrag).Path
$env:UV_PROJECT_ENVIRONMENT="$project\.venv"
uv sync --locked --project $project
uv run --offline --project $project python -m enterprise_graphrag.corpus --dataset projects/enterprise-graphrag/data/corpus-v1
uv run --offline --project $project python -m unittest discover -s projects/enterprise-graphrag/tests -p 'test_*.py' -v
```

语料24文档/58块/22节点/40边/30问；91核心测试通过，3个可选页面测试skip。标准库语料验证不是完整GraphRAG真实问答/检索质量复测。完整工作台另需demo/绑定索引/模型，不能将本轮结果改写成18/22的新实测。

## 本人L6：90分钟，分三次完成

1. **30分钟看环境**：先预测解释器与graphrisk/api位置，再执行只读导入检查；解释为何新.venv仍可复用下载缓存、Python本体为何不能删。记录commit、命令、路径。
2. **30分钟跑公开模式**：在自己的新目录运行50案与合成流水线；写明哪些数据来自Git，哪些本轮生成，为什么50/50不能说明LLM质量好。
3. **30分钟读真实证据和失败**：对比real-result/real-prospective/资源采样，解释零分差与1.09e-8差为何是重放一致性；解释换行导致哈希失败、工件复用与完整重训、memory与数据库的区别。

本人记录放reports/generated/stage10/learning/，写预测、命令、实际结果、自己的解释和疑问；代理不代填。真实生成/付费/训练保持0，学习不要求重复模型请求。下一路线7为求职表达与至少10案本人复核；阶段整体仍In Progress。
