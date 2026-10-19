# Stage10最终工程交接与90分钟学习

2026-10-19。[总验收](../../reports/stage-10-route-8-acceptance.md)列出实际验证和缺口。工程版本门禁以本提交的CI及本地最终acceptance为准；当前Agent质量仍6/10 Fail，个人学习和实用性未全部完成。

## 接手时先核对

1. 阅读仓库AGENTS.md、[跨聊天交接](../plans/stage-09-cross-chat-handoff.md)、[总验收](../../reports/stage-10-route-8-acceptance.md)与[限制](stage-10-known-limitations.md)。
2. 本机源码是C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent；E:/GraphRisk-Agent存环境、数据、权重与证据，E盘旧源码不能替代当前版本。
3. 精确最终提交、分支、非强制推送、六项CI run和恢复bundle在E:/GraphRisk-Agent/reports/generated/stage10/route8/20261019-final-v1/acceptance.json。先核对该文件、HEAD、run及工件hash，再引用完成结论。没有成功记录时保持版本验收未完成。
4. 检查真实运行状态再启动服务；本轮仅创建短时回放API并已关闭，没有替用户启动Docker或Ollama。Docker引擎在本地前置探测时未连接，不能据云端CI断言本机Docker已修复。

## 安装、运行与验证

新读者从[启动指南](stage-10-startup.md)的公开fixture模式开始；真实模式自行取得IEEE-CIS授权、配置冻结模型/政策工件和固定本地模型。参考[本机路径](stage-10-author-workspace.md)与[干净检出](stage-10-clean-reproduction.md)。公开50案例是机制测试，不是50真实模型回答。已有报告回放不需要新增模型生成。

每个PowerShell先加载本项目enter_project_env.ps1，使用Python3.11/uv.lock和仓库内缓存/TEMP。不要直接删除C盘程序目录、旧环境、容器卷或备份。memory模式重启不保留；PostgreSQL须独立准备迁移和受限账号。停止服务只停止自己本轮启动的进程，不做全局进程终止。

当前源代码的默认全量pytest可能要求真库配置；受支持的[分层回归](stage-09-ci.md)在有Docker时创建自身隔离库，不借用生产DSN。主CPU、真库、独立核心/界面、包和镜像分别计数；核心可选UI跳过不能当UI已经测试。

## 当前结论

- 工程：本地CPU915/1平台skip，独立核心91/3可选UI skip，302解析/47往返/两wheel，356旧证据绑定及当前HTTP/AppTest回放通过；最终CI另看当前提交。
- 模型：XGB三折AP0.544367/ROC0.905089；三折融合alpha均1，GNN不晋级。分数未校准，不能当欺诈概率；共享匿名实体不能证明同一个人。
- Agent：首次6/10 Fail，2重复单列，4原错误被拦截；工程安全机制有效不等于回答质量达标。
- 本人：十案反馈已接收，但引用/未知均无法判断、实用性未获通过；面试演练延期，其余学习逐项保留。
- 预算：142/144剩2，三旧账本不重置；本轮模型生成/训练/付费0。后续专项要先定新合同和预算，不自动消费旧余量。

## L8：90分钟练习（本人尚未完成）

先用25分钟不看对话解释公开演示、真实调查、历史回放的区别；写下各自所需数据/服务和来源。再用25分钟打开当前提交的六项CI和七项标准，指出哪些是真库、哪些只是机制，以及为什么不能把重复运行相加。最后用25分钟解释一次完整调查到审批的证据链、6/10失败和GNN负结果；用15分钟写自己的完成/失败/未完成/下一步清单。

个人笔记另存E盘，不要求录像，也不要求运行新真实模型。记录实际用时、自己的操作/解释、仍不懂的项，不把本安排写成已执行。路线7讲述、至少六题、三个薄弱点与AI辅助/个人贡献仍按原模板补齐。

2026-10-19更新：[本机Docker补验](../../reports/stage-10-route-8-local-docker.md)已通过，本机Docker已连接；隔离真库26/26、权限探针15/15、当前只读源码的Compose启动/连通/审批/故障恢复/命名卷重建通过。旧容器/卷未变，本轮临时资源已清理。仅新增本机Docker层；原6758280的其他通过证据复用，文档提交skip-ci不冒称新CI。原6/10 Fail、GNN不晋级与个人Pending保持；测试报告生成器为fixture，真实生成/训练/付费0。
