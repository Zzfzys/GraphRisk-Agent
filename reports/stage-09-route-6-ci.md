# 原 Stage09 路线6：分层回归与 CI 验收

2026-10-17：**本地与真实 GitHub Actions 工程验收通过，原路线6工程完成。L6本人学习仍 Pending。** 原路线7/8及后续关系模式检索尚未启动，Stage09仍 In Progress。

通俗地说，这轮给项目建立了分层“体检”：修改代码后，分别检查时间边界、证据引用、图CPU机制、API/审批、数据库恢复、独立知识助手以及打包。检查发现的问题留有失败记录，修正后重新执行。它没有训练更强的模型，也没有增加真实Agent质量样本。

## 2026-10-16 本地验收证据（历史保留）

最终目录：E:/GraphRisk-Agent/reports/generated/stage09/route6/20261016-ci-final/。各层登记输入指纹a80fccc759a470fd1195c5d101ad1a9ddad372372b6729e8991dc5fdcef112b3；最终闭包另补根入口/README等文件和文档快照。源码及实际API导入均为用户指定71df工作树，环境/缓存/临时文件/报告在E盘。

| 层 | 实际结果 | 范围与限制 |
|---|---|---|
| 语法/工程声明 | 278份Python AST通过 | Python3.11；未引入lint，不声称lint通过 |
| Git冻结字节往返 | 47份文件逐字节一致 | 新临时仓库模拟autocrlf=true入库、false导出；用户仓库索引未修改 |
| Windows CPU/fake | **815 passed / 1 skipped / 10 warnings，70.75秒** | 根tests明确排除三份真库文件；包括旧50案机制、新评分器与新增CI合同 |
| Linux CPU/fake | **815 passed / 1 skipped / 10 warnings，76.14秒** | 新构建Linux镜像内，当前源码只读挂载；不是GitHub远程运行 |
| PostgreSQL/pgvector | **24 passed / 0 skipped** | 本轮新随机容器/新库；旧fixture自带迁移，因此使用独立迁移账号 |
| 受限应用账号探针 | **15/15，0 not_run** | 14项原机制+明确的synthetic T3空邻居变体；1024维测试embedding/生成夹具 |
| 独立GraphRAG核心 | **91 passed / 0 skipped** | 独立解释器+独立锁定pytest运行器；断言无torch/graphrisk/streamlit |
| 独立GraphRAG UI | **3 passed / 0 skipped** | 独立demo解释器；AppTest；无torch/graphrisk |
| 实际wheel | 两个通过 | 主包119成员、独立包10成员；拒绝秘密/数据/模型/生成输出；脱离源码目录的解压导入通过 |
| 新镜像构建/边界 | 通过 | 实际构建，非旧镜像覆盖源码；无网络导入/边界检查通过，149份应用/API/配置/政策/页面文件与当前源码逐字节一致 |
| 原5B只读核验 | 530份闭包通过 | 原三折确认、177,162笔覆盖、决定和两个预算账本保持冻结 |
| 新真实数据fit/真实LLM/付费调用 | **均0** | 合成夹具拟合和模拟生成不能加入真实质量分母 |
| 2026-10-16时的远程Actions | **当时 not_run** | 2026-10-17已补真实运行，见文末当期证据 |

Windows唯一skip是符号链接权限测试；Linux唯一skip是E盘专用生产目录保护，同一项在Windows执行通过。Linux符号链接测试执行通过。10条warning来自既有Starlette弃用别名与小夹具All-NaN统计。这里是分层运行记录，不能合并冒充某一次全量pytest结果。

镜像graphrisk-stage09-ci:20261016-r6，ID sha256:079aa274b2a9c51ab43ab8b033a0eb498aefe8a540480c1b1c79b911d5db6ad6；检查尺寸约6.74GB，包含现有ML依赖和构建缓存，本轮未宣称体积优化或生产部署就绪。镜像在Docker现有管理存储；本轮没有迁移WSL磁盘或修改系统设置。

## 实现了什么

- 新增[五类CI任务](../.github/workflows/stage09-regression.yml)：CPU、真库、GraphRAG矩阵、包、镜像；PR/main/master/codex push及手动触发，只读权限、超时、取消旧任务。
- [分层运行器](../scripts/stage09_ci.py)每次使用新输出目录，记录输入/源码指纹/JUnit/摘要；测试前依赖按锁同步，测试offline。远程缓存关闭，仅上传public-summary白名单。
- [显式测试插件](../scripts/stage09_ci_pytest.py)禁用自动插件发现、显式加载anyio，隐藏GPU、剥离API凭据，并拒绝真实服务/外网Python连接；保留内部socketpair和测试自己创建的回环假HTTP服务。它不是恶意代码沙箱，不改变普通pytest入口。
- [15项CI探针](../scripts/stage09_ci_probes.py)使用新库/受限账号；原真实IEEE/Ollama入口保留，不在PR调用。模拟embedding只证明日期/维度/哈希/索引合同，不证明真实检索质量。
- [CI合同测试（初版14项，本轮15项）](../tests/unit/test_stage09_ci.py)拒绝空测试、skip冒充通过、包内私有文件和错误网络连接；宽表失败场景新增LF/CRLF两种构造，共多8项检查。
- [.gitattributes](../.gitattributes)保护冻结政策、案例、fixture及独立GraphRAG语料的字节，避免Git自动换行破坏既有哈希；收紧.dockerignore排除开发状态、独立项目和本地数据库文件。

## 遇到的问题与处理

1. 首轮网络隔离误拦Windows asyncio内部socketpair和测试自建假HTTP服务；修正为明确例外，真实11434/API/外网仍禁止。禁用自动插件发现后六个async测试未运行，补显式anyio插件。v1/v2原失败保留。
2. 原真库fixture调用migrate，受限角色正确拒绝DDL。没有提升应用权限：24旧测试使用新库迁移账号，15探针另用受限应用账号。
3. 简单模拟向量造成非目标政策同分首位，CI夹具按明确政策类别给向量；原14/15失败保留，只作检索机制验证。
4. Linux发现CSV测试原文件CRLF、追加行LF，造成解析器在重复ID检查前拒绝文件。改用csv.writer统一构造并参数化LF/CRLF；业务CSV管线未改，旧测试源码另存E盘source-before。
5. core.autocrlf=true会改变已有CRLF冻结案例，新增字节保护与47文件真实Git往返验证，没有重写冻结哈希。
6. Windows共享挂载上pytest临时文件操作失败，改用Linux容器内部临时目录。一次复制整个pytest目录又遇到符号链接权限，外围失败记录保留，该轮不计通过；最终仅复制常规日志/JUnit/摘要/源清单，取证成功才清理容器。

早期输出在同父目录20261016-ci-v1～v5保留，最终结果不覆盖首次失败；没有自动关闭用户应用、清理C盘、删除旧卷或重置预算。

## 如何复现和学习

准确环境准备、分层命令和三次30分钟练习见[路线6使用/学习合同](../docs/data/stage-09-ci.md)。先读public-summary中的passed/skipped/not_run及对应层范围；排错再看该层pytest.txt/JUnit。新目录才能重跑，避免覆盖已有记录。

[实现路线](../docs/plans/stage-09-implementation-roadmap.md)、[学习路线](../docs/plans/stage-09-learning-roadmap.md)、[阶段总纲](../docs/plans/stage-09-quality-operations.md)、[总计划](../PROJECT_PLAN.md)及[跨聊天交接](../docs/plans/stage-09-cross-chat-handoff.md)已同步。L6学习仍需本人完成，代理不代签。

2026-10-16时远程最后一项尚缺仓库/提交/run URL；随后用户授权完成Git收口和真实远程运行，见文末2026-10-17记录。后续路线7的可观测/漂移/新库备份恢复仍Planned；本轮临时真库测试不等于完成备份恢复。路线8和GNN关系模式检索专项未启动。


## 2026-10-17：远程 CI 执行中，首次失败保留

用户指定公开仓库 Zzfzys/GraphRisk-Agent，本机 Git 设备登录和 origin 配置完成；将 0960719 分支原样推送。推送前检查31个提交/773个blob，29个已知本地秘密值无匹配，未发现受限数据/权重载荷。

[首轮远程运行](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39014968083)的CPU、数据库、package、image成功，独立GraphRAG core/demo因相对解释器路径在切换cwd后失效而报FileNotFoundError。修复为调用者目录下的绝对路径且保留virtualenv符号链接，并补真实子进程/sys.prefix回归。正在验证并重跑，尚不能标记远程整体成功；真实数据训练/LLM/付费0，原路线7/8未启动，本人学习Pending不变。私有证据E:/GraphRisk-Agent/reports/generated/stage09/route6/20261017-github-ci-v1/。


## 2026-10-17：原路线 6 真实 GitHub CI 工程验收通过

公开仓库为 [Zzfzys/GraphRisk-Agent](https://github.com/Zzfzys/GraphRisk-Agent)，分支 `codex/stage09-version-closeout`。[成功运行 39015632664](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39015632664) 对应提交 `72c5078ac09849b1ca73b3ae3101389f6d99bea9`，六个任务全部 success：静态 278/冻结字节 47、CPU **816 passed / 1 平台 skip**、隔离真库 **24 passed / 0 skip** 与 **15/15** 受限账号机制探针、独立 GraphRAG **91+3**、两个 wheel 和实际无私有输入镜像构建/无网络 smoke。CPU 的 Linux skip 是 E 盘专用目录保护，不是缺 DSN；不同层分开计数，不冒充一次全量 pytest。

[首轮 39014968083](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39014968083) 的两个 GraphRAG 启动失败保留。原因是相对解释器路径在子进程换目录后失效；已修复为相对调用者定位的绝对路径，并保留 virtualenv 符号链接。15 项 CI 契约及 91+3 独立本地回归通过，云端再次全绿。网站域名连接重置/超时后，通过 GitHub Git Data API 上传相同 Git 对象，逐 blob/tree/commit 核对 SHA，非强制更新分支；没有改写历史或系统网络。

收尾进一步显式配置 setup-uv 的 `cache-local-path`，避免 action 用 runner-temp 默认值覆盖仓库内缓存要求，并在现有 workflow 契约中断言。收尾提交也触发完整 CI；其最终 HEAD、run URL、任务日志和摘要哈希另存 `E:/GraphRisk-Agent/reports/generated/stage09/route6/20261017-github-ci-v1/acceptance.json`，不能把上面的 72c5078 运行误记到另一提交。

原路线6工程 Done，L6本人学习及人工复核独立 Pending；Stage09整体仍 In Progress，下一步等用户启动原路线7可观测/漂移/新库备份恢复，原路线8与后续GNN检索未启动。新真实数据fit/真实LLM/付费调用均0，两个旧账本和GNN不晋级决定保留；CI不是新增真实Agent质量成绩，也不代表生产部署验收。
