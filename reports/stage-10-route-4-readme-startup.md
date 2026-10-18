# Stage10路线4：英中文README、架构与启动说明

2026-10-18：**工程Done；L4本人Pending。Stage10整体In Progress，当前真实Agent质量Fail6/10、GNN不晋级。**

## 本次交付

- [English README](../README.md)改为项目用途、成绩、公开机制入口、模式与证据；历史逐周日志移出首页，原字节私有归档，历史报告与阶段索引保留。
- [中文README](../README.zh-CN.md)提供本机用户入口、页面含义和当前限制。
- [系统架构](../docs/architecture/stage-10-system.md)包含组件图、调查/人工审批时序、PostgreSQL重启与幂等时序，以及源码符号映射。
- [通用启动](../docs/data/stage-10-startup.md)与[作者本机分离路径](../docs/data/stage-10-author-workspace.md)分别描述安装前置、公开CLI、当前真实模式、持久化、历史回放、GraphRAG和排障。
- [90分钟学习](../docs/data/stage-10-architecture-learning.md)与总计划/实现/学习/交接/证据索引同步。

## 验证与范围

已有E盘Python3.11锁定环境、C盘71df源码，新建本路线E盘私有输出。实际运行合成5000笔生成/特征CLI和50案fake评估（real_samples=0）；核对启动/回放/Streamlit/uvicorn帮助、源码导入，解析PowerShell脚本与文档块，检查相对链接、源码符号及图节点。无新真实模型调用、真实数据训练或付费，三个既有账本前后hash不变。命令日志、源代码绑定、文档归档、检查数量与Git快照见私有acceptance.json。

本轮没有同步新环境、启动真实API/UI、数据库/Compose或回放容器，也没有重跑完整pytest。Linux安装命令按锁文件/入口核对，非本轮Linux实测。当前真实调查/恢复沿用已有工程证据，现场彩排与干净检出安装分别留路线5/6，不能用本轮CLI核对代替。

## 发现并澄清

本机旧coverage脚本缺少Uvicorn app-dir，当前目录E的旧api可能覆盖C源码；本轮本机命令显式传--app-dir并校验导入，不改冻结运行代码。旧README混入多阶段历史数字与旧启动配置；新入口只列当前成绩和12项claim来源。coverage启动脚本强制memory，不能用它声称开启postgres；工作台不启动API；GRAPHRISK_HISTORY_REPLAY仅页面标识，真实回放必须连serve输出的API；旧compose.stage07.yaml不是默认增强coverage部署。补充health/ready/生成拒绝、旧run410、DSN/迁移、端口和临时目录故障说明。

模型生成在ReliableRunner的报告阶段，证据图里的draft_stub/verify_stub只有结构作用；架构未将其误写成LLM已完成。真实/合成/fixture/历史回放、主政策RAG/独立GraphRAG及memory/postgres均标明能力与数据边界。

## 后续

下一路线5：端到端Demo脚本、彩排与真实录制。优先复用已有真实结果，现场新生成仍须先登记同一预算；视频和本人操作按实际证据记录。工程收口后再提醒独立Agent质量专项，当前余额142/144剩2不自动消费。新README随本轮提交保存；远程推送与该提交CI状态独立记录，不借旧CI冒充本轮新CI。
