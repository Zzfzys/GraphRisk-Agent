# Stage 09 跨聊天交接与后续 GNN 检索方向

> 更新：2026-10-17。此文件保存跨聊天约定，不是新的验收报告。后续执行者应先核对当时的工作区和最新证据，再更新这里的当前状态。

## 1. 工作区与交接方式

- 当前开发源码：`C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent`。
- 运行环境、数据、缓存、模型与生成证据：`E:\GraphRisk-Agent`。
- E 盘可能存在旧源码，不能仅因为在那里运行命令，就认为加载了当前 C 盘源码。先核对实际导入路径。
- 顺序接力时，新聊天应使用同一个当前源码工作区，并读取本文件和对应路线。不同聊天不应同时修改同一批文件。
- 若新聊天使用另一个 worktree/分支，不要假设当前未提交、未跟踪的代码和文档已经存在；必须先核对并明确交接这些变更。不要重置、清理或覆盖现有大量本地变更。
- 每次交接记录：已完成事项、实际验证与证据路径、未完成项、下一步、遗留进程/容器与停止方式。完成后同步阶段总纲、实现/学习路线；跨阶段变化同步 PROJECT_PLAN。
- 本文件的当前状态是 2026-10-17 的快照，不能覆盖另一个聊天此后产生的新证据。发生冲突时核对日期、实际文件与验收记录，不依赖单独一句旧状态。

## 2. 当前状态与下一步

| 项目 | 当前结论 | 证据/执行入口 |
|---|---|---|
| 5B 路线 0～7 | 工程完成；增强 XGBoost 基线通过；GNN 增量失败、不晋级 | [5B 总验收](../../reports/stage-09-route-5b-7-acceptance.md) |
| 原 Stage 09 路线 6 | 本地及真实GitHub CI工程验收通过；L6本人独立Pending | [路线6验收](../../reports/stage-09-route-6-ci.md) |
| 原 Stage 09 路线 7 | Planned，可观测性、漂移与安全恢复 | [实现路线](stage-09-implementation-roadmap.md) |
| 原 Stage 09 路线 8 | Planned，端到端验收、个人复现与复盘 | [实现路线](stage-09-implementation-roadmap.md) |
| 本人学习/人工复核 | 独立 Pending，不能由代理代签 | [学习路线](stage-09-learning-roadmap.md)、[5B 学习路线](stage-09-route-5b-learning.md) |
| 后续关系模式检索专项 | 用户已确认方向，尚未开始，不加入 Stage 09 当前验收条件 | 本文件第 4 节 |

路线编号必须写清楚：**5B 路线 6、7 已完成，不代表原 Stage 09 路线 6、7 已完成。** Stage 09 整体仍为 In Progress。

5B 最近一次主项目完整回归为 806 passed / 1 skipped / 10 warnings；其中 24 项真实数据库测试无跳过。随后新增总验收器的 11 项测试单独通过，不能合并宣称某次全量测试有 817 项通过。独立 GraphRAG 包/UI 本轮没有重跑。这些都是历史证据，后续代码变化应做适当的新验证。

执行原路线 6～8 的边界：

- 路线 6：分开 CPU/fake、隔离真库、独立 GraphRAG 环境与包/镜像检查；真实本地 LLM、GPU 训练、受限数据、付费调用不进入 PR 自动任务。远程 CI 未实际运行不能标记远程通过，提交/推送/触发仍按当时授权处理。
- 路线 7：复用追踪，区分漂移与质量；备份恢复到另一个新库，不能用容器或卷重启代替。故障注入限于本轮隔离环境。
- 路线 8：核对当期证据，工程、Agent 质量、GNN 增量、本人学习分别记状态；不因阶段关闭而抹去质量失败或本人 Pending。

## 3. 冻结结论与运行约束

- 增强 XGBoost 三折外层平均 ROC-AUC 0.905089、AP 0.544367；三折简单融合均选择 XGBoost 权重 1，图增量 0、正收益折 0/3。保留 5B 的不晋级决定，不在已观察的外层/历史测试上继续救分。
- 当前确定性图查询提供关系线索，其作用不能算成 GNN 的已证实收益。共享实体不证明同一人或欺诈团伙。
- 不覆盖旧权重、预测、首次失败、审批/报告、账本和封存清单。新增运行用独立目录，不重置训练或生成预算。
- 先遵循 AGENTS.md。Python 3.11，已有 uv.lock；每个 PowerShell 先加载 E 盘环境脚本，缓存、临时目录、大工件继续留 E 盘。
- CPU 只读推理的独立合同为系统可用内存至少 1.5 GiB、单进程 RSS 上限 1280 MiB；原真实训练门禁仍为 3 GiB。6 GiB 是历史建议余量，不能当硬门槛；也不能将这些单进程数当整机峰值保证。
- 5B 验收专用 Compose 栈在验收后已停止、卷保留；未来运行前先检查真实状态。不要自动关闭用户应用、删除卷、清理 C 盘或修改 WSL 系统设置。
- 复用既有证据时检查是否仍适用；不要为了“重新接手”而自动重训、重放真实 LLM 或调用付费 API。

默认只读复核命令（不启动 Docker、不训练、不预测、不生成报告）：

```powershell
. 'E:\GraphRisk-Agent\scripts\enter_project_env.ps1' | Out-Null
$stage09Source = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$env:PYTHONPATH = "$stage09Source\src;$stage09Source"
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location 'E:\GraphRisk-Agent'
uv run --offline --no-sync python "$stage09Source\scripts\check_stage09_5b.py"
```

## 4. 已确认的后续 GNN 检索方向（尚未实施）

用户于 2026-10-16 经 grilling 访谈确认以下取舍：

| 决策 | 已确认内容 |
|---|---|
| 职责 | 为 Agent 寻找关系模式相似的历史交易，提供调查候选，不改变现有风险主评分 |
| 时序 | 先完成原 Stage 09 路线 6～8，再按用户启动指令开展；当前不抢先实现，不给它另起 Stage 编号 |
| 时间 | 最多一周，约 20～24 小时，先做可行性验证 |
| 相似性 | 同时考虑关系结构、时间和交易属性；实体标识可以不同，不推断同一团伙 |
| 技术起点 | 先核验现有冻结 GraphSAGE 表示能否用于检索，不一开始训练专用新模型；已有 embedding 导出不等于已有有效检索 |
| 对照 | 与关系统计、交易属性距离等简单检索方法公平比较 |
| 人工审核 | 先试标 20～30 对；用户累计约 2～3 小时。隐藏方法名，候选去重、统一展示；允许无法判断，区分结构相似与调查参考价值 |
| 首轮交付 | 独立只读检索工具、可复现实验与评测报告；证明检索价值后再安排 Agent 自动调用和界面接入 |
| 不足时处理 | GNN 无增益可停止；基线有用可保留基线工具；两者均无用则保留负实验，不将工程完成包装为模型收益 |

下一次专项规划仍需完成的技术工作：核对可用模型/向量版本及时间资格，定义候选历史范围、简单基线、试标准则和独立检索评测。试标后、正式评测前冻结数值门槛；不能看到正式结果后改标准。检索分数不是欺诈概率，候选必须可核对事实与来源；小样本结果只作可行性证据，不宣称真实业务或欺诈识别收益。

这里记录的是已确认方向，不是已实施路线，也不自动扩大 Stage 09 的 112 小时历史预算。后续专项若启动，最多追加上述一周；Stage 10 的具体衔接在 Stage 09 关闭时明确。

## 5. 可直接发给另一个聊天的接手说明

```text
请接手 GraphRisk-Agent，先阅读当前源码工作区的 AGENTS.md、
docs/plans/stage-09-cross-chat-handoff.md、PROJECT_PLAN.md、
docs/plans/stage-09-quality-operations.md、
docs/plans/stage-09-implementation-roadmap.md、
docs/plans/stage-09-learning-roadmap.md，
以及 reports/stage-09-route-5b-7-acceptance.md。

先核对当前源码目录、git状态、已推送分支与最新证据，再核对原路线6真实GitHub运行。
当前源码在 C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent，
运行环境/数据/缓存/大工件在 E:\GraphRisk-Agent；不要误用 E 盘旧源码。
5B 路线 0～7工程完成不等于原路线 6～8完成；若另一个聊天已推进，以核验后的新证据更新状态。
原路线6本地及真实GitHub CI工程验收已通过，L6本人独立Pending；在我要求开始时顺序推进原路线7、8，每步同步报告与计划。
后续“GNN 关系模式检索”方向已确认，但要等原路线 6～8完成后另行启动。
保留 5B 图增量失败、不晋级结论和全部旧工件/账本；不要自动重训或新增真实/付费模型调用。
不要与另一个聊天同时修改同一工作区。每次结束记录实际验证、未完成项与下一步。
```


## 6. 2026-10-16 原路线6历史接手状态

本地工程通过，远程Actions not_run（当前git remote为空，已询问目标仓库链接）；路线6整体未关闭。最终证据E:/GraphRisk-Agent/reports/generated/stage09/route6/20261016-ci-final/；Windows/Linux各815/1平台skip，24真库零skip、15受限账号机制探针、独立GraphRAG91+3、两wheel/新镜像通过。149份镜像应用文件与当前源码匹配，47冻结文件Git往返通过；旧530闭包和两账本保持原值。新真实fit/LLM/付费0，L6本人Pending，原路线7/8与检索专项未启动。

实际导入的graphrisk/API均来自本C盘71df工作树；E盘三个业务虚拟环境及独立pytest运行器按现有锁绑定当前源码。后续每个PowerShell仍先加载E盘环境脚本，并用源目录的src/root/scripts设置PYTHONPATH；不要默认E盘旧API是当前代码。

新镜像graphrisk-stage09-ci:20261016-r6留作复查；本轮随机DB/Linux容器在取证后清理，没有旧容器/卷操作，Docker Desktop引擎保留运行。停止未来本轮测试只操作运行器创建的随机资源，不能down -v旧项目。初次Linux取证复制符号链接失败只保留外围记录，最终只复制常规文件并核对成功；不把失败轮次算作通过。

原路线6验收当时未自动提交或推送；后续用户授权的本地Git收口见文末。远程仓库/run URL仍待核对。路线7须用户明确开始，不能把本轮DB测试当作备份恢复；所有验收/学习/总计划已同步，GNN专项等原路线6～8完成后另行启动。


## 2026-10-16：本地 Git 版本收口完成，远程 CI 仍 Pending

[版本收口报告](../../reports/stage-09-git-closeout.md)：已从 bfbdc40 建立 `codex/stage09-version-closeout`，集成提交 `cc1da7dc92779c5260ea64721b313c8a52d43120` 保存 553 份工程文件，最终文档另作提交，最终 HEAD 与可恢复 Git bundle 哈希见本轮私有 acceptance。排除了秘密、交易原始数据、权重、缓存和本地助手状态；C 盘原始字节快照与 E 盘旧源码另存，E 盘 main 的 317 行原工作区状态不变。

从集成提交独立干净检出后，278 Python AST、47 冻结文件字节往返、Windows CPU 815 passed/1 符号链接权限 skip、真库 24 零 skip 及 15 受限账号机制探针、GraphRAG 91+3、两个 wheel 通过。复用锁定的 E 盘环境，源码导入来自检出；不宣称冷缓存安装或本轮新镜像验证。Git 行尾规范化后的检出指纹 b1009e… 与原始字节 a80fccc… 分开记录；最终文档提交不改变已测运行代码树。

无 Git 远端、无推送、无远程 run URL；下一步是核对远端和公开边界并补路线 6 远程证据。原路线 7/8 及后续 GNN 检索仍未启动。两个账本不变，原路线 6 的 435 份清单核对通过；新增真实训练/LLM/费用均 0，GNN 不晋级和本人学习 Pending 不变，Stage 09 整体仍 In Progress。


## 2026-10-17：原路线 6 真实 GitHub CI 工程验收通过

公开仓库为 [Zzfzys/GraphRisk-Agent](https://github.com/Zzfzys/GraphRisk-Agent)，分支 `codex/stage09-version-closeout`。[成功运行 39015632664](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39015632664) 对应提交 `72c5078ac09849b1ca73b3ae3101389f6d99bea9`，六个任务全部 success：静态 278/冻结字节 47、CPU **816 passed / 1 平台 skip**、隔离真库 **24 passed / 0 skip** 与 **15/15** 受限账号机制探针、独立 GraphRAG **91+3**、两个 wheel 和实际无私有输入镜像构建/无网络 smoke。CPU 的 Linux skip 是 E 盘专用目录保护，不是缺 DSN；不同层分开计数，不冒充一次全量 pytest。

[首轮 39014968083](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39014968083) 的两个 GraphRAG 启动失败保留。原因是相对解释器路径在子进程换目录后失效；已修复为相对调用者定位的绝对路径，并保留 virtualenv 符号链接。15 项 CI 契约及 91+3 独立本地回归通过，云端再次全绿。网站域名连接重置/超时后，通过 GitHub Git Data API 上传相同 Git 对象，逐 blob/tree/commit 核对 SHA，非强制更新分支；没有改写历史或系统网络。

收尾进一步显式配置 setup-uv 的 `cache-local-path`，避免 action 用 runner-temp 默认值覆盖仓库内缓存要求，并在现有 workflow 契约中断言。收尾提交也触发完整 CI；其最终 HEAD、run URL、任务日志和摘要哈希另存 `E:/GraphRisk-Agent/reports/generated/stage09/route6/20261017-github-ci-v1/acceptance.json`，不能把上面的 72c5078 运行误记到另一提交。

原路线6工程 Done，L6本人学习及人工复核独立 Pending；Stage09整体仍 In Progress，下一步等用户启动原路线7可观测/漂移/新库备份恢复，原路线8与后续GNN检索未启动。新真实数据fit/真实LLM/付费调用均0，两个旧账本和GNN不晋级决定保留；CI不是新增真实Agent质量成绩，也不代表生产部署验收。
