# Stage10路线8：本机Docker补充验收

2026-10-19。状态：通过。原[总验收](stage-10-route-8-acceptance.md)在6758280以本地适用回归+云端Docker/数据库为依据，本次单独补Windows Docker Desktop运行证据，原封存报告/账本/成绩不覆盖。

用户要求已通过的不重复，本轮未重跑CPU915、独立GraphRAG、302解析/47冻结、两wheel、镜像构建、模型训练、真实Agent质量与性能评估；精确复用6758280及其[成功CI](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39226289441)。只有Docker本机层是新增执行。

| 本机项目 | 结果 |
|---|---|
| Docker引擎与Compose | 已连接，Docker29.8.0/Compose5.5.1 |
| 隔离PostgreSQL/pgvector测试 | 26通过、0失败/错误/跳过 |
| 受限账号机制探针 | 15/15通过，另计 |
| 当前Compose栈 | 启动/就绪、API与工作台连通、审批绑定、API重启、DB停机恢复、命名卷保留重建均通过；测试资源清理且旧容器/卷未变。 |

本轮使用缓存graphrisk-stage09-ci:20261016-r6和pgvector镜像；Python3.11，缓存镜像uv.lock与当前源码相同。当前应用代码/配置从C盘71df只读挂载，真实数据/冻结模型来自E盘，测试报告生成器显式为fixture。测试专用Compose采用随机项目名、随机loopback端口、独立数据库和带租约标签的命名卷；不声称仓库旧默认Compose在C/E分离布局下无需挂载配置即可直接使用，也不把本轮称新镜像构建。

原脚本第一轮在3GiB通用门槛处停止，尚未创建应用栈；依据既有只读CPU合同改用1.5GiB可用内存/1280MiB进程RSS，并监测低内存时只停止本轮API/工作台。随后一次应用启动因宿主可用内存降至约1.48GiB而被资源守卫停止，退出137但OOMKilled=false；不是Docker引擎故障。私有控制器原先仅为JSON/hash工具重复导入训练库，改用等价标准库工具后继续通过，应用/模型代码和1.5GiB门槛未改。训练3GiB门槛和质量标准未改，两次阻塞记录保留，不算通过。

真实生成/真实训练/付费均0，旧账本142/144剩2；原6/10 Fail、GNN不晋级、实用性未证实和本人演练/学习Pending保持。Docker工程通过不改变模型质量。原始配置、运行身份、来源绑定、端口/挂载、测试输出和最终补验acceptance留在E:/GraphRisk-Agent/reports/generated/stage10/route8/20261019-local-docker-v1/；个人/行级资料与secret不入Git。


## 版本与复用说明

本轮代码、配置和依赖未改，仅追加本机证据与文档。按用户“已通过不重复”的要求，文档提交使用`[skip ci]`，不触发已通过的全量云端回归；[GitHub说明](https://docs.github.com/zh/actions/how-tos/manage-workflow-runs/skip-workflow-runs)。应用证据仍绑定6758280的成功CI，不宣称文档提交有新的CI成功。精确文档提交、非强制推送和恢复bundle另存本轮私有acceptance。

`/api/v1/ready`六项data/risk_model/graph/policy_index/ollama/postgres均true；工作台health/root均200，workbench→api的Docker DNS/HTTP可达；两份fixture报告在API重启、DB停机恢复和保留命名卷的整栈重建后保持报告hash、rejected决定和版本，不重新生成。观测到API进程RSS为558.97 MiB，低于1280MiB合同；这是观测值，不是整机或全程峰值保证。补验只覆盖缓存依赖镜像+当前只读源码的隔离栈，默认仓库旧Compose文件在C/E分离布局下的直接启动不由此背书。

验收结束后引擎保留运行，本轮测试应用容器/网络/新测试卷/临时凭据已清理；不能直接复用已删除凭据来重启此测试Compose。日常项目启动仍按既有[本机指南](../docs/data/stage-10-author-workspace.md)准备配置。两次内存阻塞和轻量控制器调整见私有资源记录，原验收/模型/账本均不覆盖。
