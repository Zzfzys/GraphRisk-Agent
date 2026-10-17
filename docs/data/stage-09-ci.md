# 原 Stage09 路线6：分层回归与 CI 使用合同

本轮只验证工程合同，不重训真实数据模型，不生成真实 LLM 报告，不增加付费调用。GraphSAGE 三折无增量、不晋级决定和本人学习 Pending 保留。路线5B-6覆盖已完成，不等于本文件的原路线6。

## 分层与判定

| 层 | 内容 | 依赖/失败条件 |
|---|---|---|
| static | Python AST、Python3.11声明、47冻结文件Git往返 | 没有引入 lint 工具；不把语法检查称为 lint |
| cpu | 根 tests 除三份真库文件，含时间/图CPU/API/50案例fakeAgent/Stage09评分器 | 禁GPU、剥离凭据、禁止真实服务连接；只有列明的平台skip可接受 |
| database | 旧24项真库 + 15机制探针 | 新随机容器/新库；旧测试fixture有迁移，使用隔离迁移账号；探针使用受限应用账号；任何skip/not_run失败 |
| graphrag-core | 独立核心91项 | 核心自己的解释器 + 单独锁定pytest运行器；断言没有torch、graphrisk、streamlit |
| graphrag-demo | 独立AppTest 3项 | 独立demo解释器 + 单独pytest运行器；断言没有torch、graphrisk；有Streamlit |
| package | 两个实际wheel的成员清单、隔离解压后导入 | 只允许应用Python包/metadata，拒绝数据、秘密、模型和生成输出；不是新机器完整安装验收 |
| image | 当前Dockerfile构建、无网络容器导入/边界 | 使用新构建镜像；不以旧镜像覆盖源码代替构建；没有受限数据/模型也能通过导入 |

pytest插件只在CI入口显式加载，不改变普通 `uv run --offline pytest`。禁用第三方自动插件发现，主项目显式加载anyio。Windows asyncio私有socketpair和本进程新建的回环假HTTP服务器允许；已有Ollama/API和外网Python socket连接拒绝。它是测试误调用防护，不是对恶意代码的操作系统沙箱。迁移/依赖下载在独立受控步骤发生。

15机制探针保留原合同的14项机制及显式synthetic T3空邻居变体。原IEEE真实空邻居观察不覆盖；1024维测试embedding仅验证日期过滤、哈希/维度核验与pgvector合同，不证明语义检索质量；fake生成不计入真实Agent质量分母。迁移后应用权限不扩大，所有外部处置0。

## Windows当前工作树复现

每个新PowerShell先执行以下环境设置。源码在C盘，环境/临时目录/缓存/证据在E盘。

```powershell
. E:\GraphRisk-Agent\scripts\enter_project_env.ps1
$stage09Source = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$env:PYTHONPATH = "$stage09Source\src;$stage09Source;$stage09Source\scripts"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\.venv'
Set-Location E:\GraphRisk-Agent
uv sync --offline --locked --group demo --project "$stage09Source"
$stage09CiOut = 'E:\GraphRisk-Agent\reports\generated\stage09\route6\ci-' + (Get-Date -Format yyyyMMdd-HHmmss)
uv run --offline --no-sync python "$stage09Source\scripts\stage09_ci.py" static --output-root "$stage09CiOut"
uv run --offline --no-sync python "$stage09Source\scripts\stage09_ci.py" cpu --output-root "$stage09CiOut"
uv run --offline --no-sync python "$stage09Source\scripts\stage09_ci.py" package --output-root "$stage09CiOut"
# Docker Desktop已经就绪后；仅新建并清理本轮随机容器，不操作旧栈/卷。
uv run --offline --no-sync python "$stage09Source\scripts\stage09_ci.py" database --output-root "$stage09CiOut"
```

独立环境准备（首次缓存空时显式去掉 `--offline` 联网同步；安装成功后测试仍offline）：

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\artifacts\stage-09\route6\test-runner-env'
uv sync --offline --locked --project "$stage09Source\ci\test-runner"
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\projects\enterprise-graphrag\.venv'
uv sync --offline --locked --project "$stage09Source\projects\enterprise-graphrag"
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\projects\enterprise-graphrag\demo\.venv'
uv sync --offline --locked --project "$stage09Source\projects\enterprise-graphrag\demo"
& E:\GraphRisk-Agent\artifacts\stage-09\route6\test-runner-env\Scripts\python.exe "$stage09Source\scripts\stage09_ci.py" graphrag-core --python E:\GraphRisk-Agent\projects\enterprise-graphrag\.venv\Scripts\python.exe --output-root "$stage09CiOut"
& E:\GraphRisk-Agent\artifacts\stage-09\route6\test-runner-env\Scripts\python.exe "$stage09Source\scripts\stage09_ci.py" graphrag-demo --python E:\GraphRisk-Agent\projects\enterprise-graphrag\demo\.venv\Scripts\python.exe --output-root "$stage09CiOut"
$env:UV_PROJECT_ENVIRONMENT = 'E:\GraphRisk-Agent\.venv'
```

每层保存source-inputs、源码指纹、pytest文本/JUnit与public-summary。目录已存在则拒绝覆盖；失败换新目录，保留原失败。database用密码文件初始化、DSN仅入子进程环境，结束删除本轮密码文件并清理本轮容器/匿名卷。没有接受用户已有DSN的参数。

## GitHub任务与尚未执行的远程验收

workflow为 `.github/workflows/stage09-regression.yml`：PR、main/master/codex分支push及手动触发；五类job，GraphRAG核心/demo分矩阵；只读contents权限、超时15～35分钟、同分支取消旧任务。首次固定uv0.12.19、锁定联网同步，再offline测试；不上传uv缓存、原始日志、JUnit、wheel或私有目录，只上传明确选择的public-summary。测试/模拟政策本来可公开，真实数据/模型/Ollama/付费密钥不是PR输入。

真实外网、GPU、大训练、真实30交易质量和生产负载留本地独立证据；CPU fixture通过不替代它们。当前仓库无远端，远程run URL/commit仍Pending；工作流YAML及本地容器执行不等于GitHub Actions运行成功。远端首次需要完整当前源码提交，现有未跟踪文件不会自动出现在runner。

参考：[GitHub工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[uv与GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)。

## L6：三次30分钟

1. 列CPU/DB/GraphRAG/镜像/真实LLM/GPU各自的数据、服务、网络和密钥依赖；解释模拟生成不是真实模型质量。
2. 运行新14项CI合同测试与静态层，读source-inputs和JUnit；用临时目录构造一份含skip的最小JUnit，观察 `require_tests` 拒绝它。比较Windows内部socketpair与真实11434调用，解释为何只禁所有连接会误伤异步API测试。
3. 运行CPU层；Docker就绪时运行真库层，核对24和15各自分母/账号、零not_run与容器清理；检查workflow上传白名单，解释没有远程run URL为什么不能填写远程Pass。

本人交付依赖表、一条失败/修复记录和自己的解释；材料就绪不代表L6学习完成。

当前实际验收见[原路线6报告](../../reports/stage-09-route-6-ci.md)：Windows/Linux各815 passed/1平台skip，真库24/0，15机制探针、核心91/UI3通过。Git自动换行保护冻结文件；宽表失败场景同时覆盖LF与CRLF，业务数据管线/模型权重未改。远程Actions与L6本人学习仍Pending。


## 2026-10-17：云端相对解释器路径

首轮真实 Actions 的 CPU、隔离数据库、wheel 和镜像通过，两个独立 GraphRAG 任务在启动解释器前失败。`--python projects/.../.venv/bin/python` 必须先相对调用者当前目录转成绝对路径，再让子进程切换到报告目录。使用 abspath，不能 resolve 掉 Linux virtualenv 的 Python 符号链接，否则会脱离所选虚拟环境。新增实际创建无 pip 虚拟环境、从另一个目录启动并核对 sys.prefix 的回归；无需网络或真实模型。

首轮失败证据保留：[Actions 39014968083](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39014968083)。修复后的完整远程结果以路线6验收最新记录为准，不将首次失败改写成通过。


## 2026-10-17：原路线 6 真实 GitHub CI 工程验收通过

公开仓库为 [Zzfzys/GraphRisk-Agent](https://github.com/Zzfzys/GraphRisk-Agent)，分支 `codex/stage09-version-closeout`。[成功运行 39015632664](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39015632664) 对应提交 `72c5078ac09849b1ca73b3ae3101389f6d99bea9`，六个任务全部 success：静态 278/冻结字节 47、CPU **816 passed / 1 平台 skip**、隔离真库 **24 passed / 0 skip** 与 **15/15** 受限账号机制探针、独立 GraphRAG **91+3**、两个 wheel 和实际无私有输入镜像构建/无网络 smoke。CPU 的 Linux skip 是 E 盘专用目录保护，不是缺 DSN；不同层分开计数，不冒充一次全量 pytest。

[首轮 39014968083](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39014968083) 的两个 GraphRAG 启动失败保留。原因是相对解释器路径在子进程换目录后失效；已修复为相对调用者定位的绝对路径，并保留 virtualenv 符号链接。15 项 CI 契约及 91+3 独立本地回归通过，云端再次全绿。网站域名连接重置/超时后，通过 GitHub Git Data API 上传相同 Git 对象，逐 blob/tree/commit 核对 SHA，非强制更新分支；没有改写历史或系统网络。

收尾进一步显式配置 setup-uv 的 `cache-local-path`，避免 action 用 runner-temp 默认值覆盖仓库内缓存要求，并在现有 workflow 契约中断言。收尾提交也触发完整 CI；其最终 HEAD、run URL、任务日志和摘要哈希另存 `E:/GraphRisk-Agent/reports/generated/stage09/route6/20261017-github-ci-v1/acceptance.json`，不能把上面的 72c5078 运行误记到另一提交。

原路线6工程 Done，L6本人学习及人工复核独立 Pending；Stage09整体仍 In Progress，下一步等用户启动原路线7可观测/漂移/新库备份恢复，原路线8与后续GNN检索未启动。新真实数据fit/真实LLM/付费调用均0，两个旧账本和GNN不晋级决定保留；CI不是新增真实Agent质量成绩，也不代表生产部署验收。

setup-uv显式输入cache-local-path=${{ github.workspace }}/.uv-cache；enable-cache=false继续关闭GitHub缓存恢复/保存。只设全局UV_CACHE_DIR不足以覆盖action默认缓存目录。本地仍逐PowerShell加载E盘enter_project_env.ps1，环境/缓存/私有产物留E盘，不把云端临时工作区与用户C盘混为一谈。
