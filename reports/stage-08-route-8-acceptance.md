# Stage 08 路线 8：独立演示、总验收与个人复盘

最新状态：2026-10-07，**路线 0～8 工程验收完成，Stage 08 为 Done（工程）**。DeepSeek 小时汇总账单已与 40 次请求的原始 usage 精确核对，保留逐请求实际金额未知的限制。L0～L8 个人复现和口述需学习者亲自完成，本文不代签；下文此前故障与未完成状态保留为历史。

## 逐项验收

| 门禁 | 预期 | 本轮实测和可复查证据 | 判定 |
|---|---|---|---|
| 核心隔离安装 | Python 3.11、从独立锁/源码构建 wheel，干净环境不含 GraphRisk/Torch/Streamlit | 核心 `uv.lock` SHA256 `ea4d79b994cffea963492cc2d244845cb740c598f2e4f4f32e6ce7e766a836d9` 与路线 7 一致；`uv build --offline` 得 sdist/wheel；新建 clean-core venv 安装 wheel，`python -I` 验证版本 0.1.0，三模块均不可导入。 | 通过 |
| UI 隔离安装 | 固定 Streamlit 版本，独立只读工作台能启动 | `demo/uv.lock` 单独锁定 Streamlit 1.64.0；演示 env 离线同步，源码工作台启动于 `127.0.0.1:8502`，首页与 `/_stcore/health` 都为 HTTP 200。headless AppTest 三项通过：已核验答案展示路径/来源；未知服务 0 次模型调用；缺索引先报错。 | 通过 |
| 索引与 CLI | 同语料两次索引字节一致；两跳与无答案可复现 | 干净安装环境重建两次均为 58 chunks、版本 `84d09b81aec41cbebdbeed7b6e634a9147b786b5c6c2efb41595e37839bd8c2e`、文件 SHA256 `02a134f896825b17087e93f376044ea617bd211aba31876311be54bd3c54f75e`。首次本地两跳返回 team-security，完整两条边及两篇来源；未知服务 `abstained/unknown_entity`、生成调用 0。 | 通过 |
| 冻结 30 问 | 不改冻结题和首次结果，重读旧 90 条确认指标 | 语料冻结 SHA256 `66d716823d71db9f78452c361ef89d04b90fa5810bd32380ffa77219de32d764`。只读 rescore 验证原 90 条哈希并得到 text/graph/hybrid 16/22、18/22、18/22；实际生成分别 19/20/21 次。新增模型调用 0。 | 通过 |
| 主系统回归 | 根测试、Stage06 50、Stage07 15 与显式持久路径 | 隔离数据库恢复后，根 `uv run --offline pytest -q` 为 **470 passed、0 skipped、1 warning**；18 项真库测试均运行；Stage07 冻结 **15/15 passed、0 not_run**。Stage06 机制案例先前 **50/50**，另 3 条真实本地样本仍来自同一笔交易，只算旧链可运行。 | 通过 |
| 账单与费用 | 官方小时汇总与原始 usage 精确一致；内部预算上界差额可解释（2026-10-07 修订粒度） | 官方 40 次请求，缓存命中/未命中/输出 Token 为 7680/11206/704，Paid/CNY **0.0283512 元**；内部估算 **0.028383 元**，差额 **0.0000318 元**由 40 次逐笔向上舍入完全解释。逐请求实扣金额未知。 | 通过（小时汇总） |
| 数据/密钥边界 | 不将受限数据、密钥、生成记录打包或提交 | `git check-ignore` 命中 `reports/generated/`、两个环境、`.env.stage08-deepseek` 与 `data/raw/`；核心 wheel 10 项内容扫描无 `.env`、IEEE、GraphRisk、secret、生成报告或锁文件。Git `diff --check` 无空白错误，只有既有换行提示。 | 通过（有限扫描） |
| 个人学习 | 本人从环境到演示复现并录制 90 秒讲解 | [L8 指南](../docs/data/stage-08-demo-and-interview.md)已提供 60 分钟任务、命令、预期、四段面试稿与自测表；尚无本人操作或录音证据。 | 待本人完成 |

核心环境运行 94 个 unittest，**91 passed、3 skipped**（3 项 UI 测试需独立演示环境）；demo 环境单独运行 **3/3 UI 测试**。曾尝试在 demo wheel 环境运行全部 94 个测试，其中 2 项评分器测试因找不到核心源码旁的 `uv.lock` 报错；这说明冻结评分/复核必须在核心源码环境运行，不能把 UI 环境当作评分器环境。错误未记成模型质量失败，也未更改路线 7 原始产物。三项 UI 测试使用本地夹具核对呈现与门禁，不计入真实模型答对数。

生成的构建包、两份索引、首次本地 CLI 输出、只读复核和 Stage06 回归存于忽略目录 `reports/generated/stage08/route8/`；不会进入 Git。演示页面显示已核验答案或拒答、关系路径、原文片段、版本、有效期、来源文件和执行记录，且标注资料为原创模拟。它无工单、消息、权限修改入口。模型只能选择证据 ID，不能把模板句误述成开放式自由推理。

## 验收过程与剩余动作

### 2026-10-02 状态重测

按用户要求重测 Stage 08，未重新调用本地模型或付费 API。根项目 `uv run --offline pytest -q` 为 **451 passed、18 skipped、1 warning**；独立核心 `uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -q` 为 **94 tests，91 passed、3 skipped**；演示环境单独执行 `test_streamlit_app.py` 为 **3/3 passed**。`rescore_answers` 逐一核对首次 90 条记录的哈希，新的忽略产物 `reports/generated/stage08/route8/status-recheck-20261002.json` 再次得到 text/graph/hybrid **16/22、18/22、18/22**；hybrid 两跳 **7/8**、无答案 **4/4** 拒答、注入 **4/4** 无动作，新增生成/付费调用均为 0。

在沙箱外只读运行 `docker info` 仍无法连接 `dockerDesktopLinuxEngine` 命名管道；本机未配置隔离 `GRAPHRISK_STAGE07_DSN` / `GRAPHRISK_STAGE07_MIGRATION_DSN`，因此本次 18 项真库测试仍为 skipped、15 个冻结 v2 探针仍为 not_run。预算账本脚本可核对 40 次内部记录与估算 **0.028383 元**，但“settled”只表示内部记账完整，正式逐请求账单仍未提供，不能据此关闭费用门禁。Stage 08 仍为 **In Progress**。

1. **已完成（见下方最新回归）**：隔离 PostgreSQL/pgvector 环境、15 个冻结 v2 探针及 18 项真库测试。此前 Docker 未运行的记录保留为排障历史。
2. 从 DeepSeek 正式账单获得每个尝试的账单编号和实付金额，按[路线 3 账本工具](../docs/data/stage-08-provider-comparison.md)逐项对账。账号余额显示差额为 0.00 不能证明账单为零；不伪造凭证。
3. 用户按[L8 指南](../docs/data/stage-08-demo-and-interview.md)亲自完成两跳、拒答、UI 与 90 秒讲述，记录自己的输出、错误码和不足。工程测试不会代替个人学习。

正式账单门禁有证据后，才把 Stage08 工程状态改为 Done，并按计划提醒：Stage09 开头先补**多笔不同交易的真实本地 Agent 基线**，再做 GraphSAGE 实现、采样、特征消融和调参诊断；目前不启动训练。GraphRAG 18/22 恰好过线且与 graph 模式持平；30 问此前用于检索诊断，不能外推生产质量。注入组主要由确定性门禁挡住，也不能证明模型自身抗注入。

## 2026-10-02 状态重测

按用户要求重测 Stage 08，未重新调用本地模型或付费 API。根项目 `uv run --offline pytest -q` 为 **451 passed、18 skipped、1 warning**；独立核心 94 项 unittest 为 **91 passed、3 skipped**；演示环境 3 项 UI 测试为 **3/3 passed**。只读复评逐一核对首次 90 条记录的哈希，新产物 `reports/generated/stage08/route8/status-recheck-20261002.json` 再次得到 text/graph/hybrid **16/22、18/22、18/22**；hybrid 两跳 **7/8**、无答案 **4/4** 拒答、注入 **4/4** 无动作，新增生成/付费调用为 0。

沙箱外只读 `docker info` 仍无法连接 `dockerDesktopLinuxEngine` 命名管道；隔离 Stage07 DSN 未配置，因此 18 项真库测试仍为 skipped，15 个冻结 v2 探针仍为 not_run。预算账本核对 40 次内部尝试与估算 **0.028383 元**；内部 settled 不等于正式逐请求账单。Stage 08 仍为 **In Progress**。

## 2026-10-02 Docker 恢复后的状态

本次只读复查：Docker Engine 返回 29.8.0；独立 Compose 项目 `graphrisk-stage08-test` 的 db 容器为 healthy。容器检查显示 `5432/tcp` 的宿主端口映射为空，因而先前约定的 `127.0.0.1:15433` 目前不可用于宿主 DSN。当前 Codex 执行进程中 `GRAPHRISK_STAGE07_DSN`、`GRAPHRISK_STAGE07_MIGRATION_DSN`、`GRAPHRISK_STAGE07_APP_DSN` 和 `GRAPHRISK_STAGE07_EVAL_ISOLATED` 均未设置。未发现正式账单或收据产物。Docker 可用是前置条件恢复，并不构成 18 项真库测试或 15 个冻结探针通过；Stage08 仍为 In Progress。

## 2026-10-02 隔离数据库回归完成

上述端口映射故障已定位：默认 `backend` 为 Docker 内部网络，调试覆盖文件只增加端口时，容器的 `HostConfig.PortBindings` 有 `127.0.0.1:15433`，但实际 `NetworkSettings.Ports` 为空，宿主 TCP 连接失败。在仅用于调试的 `compose.stage07-debug.yaml` 为数据库增加独立 `db_debug` 非内部网络，并保留原 `backend`；独立项目 `graphrisk-stage08-test` 中仅有 db 容器，执行 `up -d --no-deps --force-recreate db` 后数据卷保留，实际端口映射出现，`Test-NetConnection 127.0.0.1 -Port 15433` 为 True。默认 Compose 的数据库仍只在内部网络且无宿主端口。

从已忽略的本地密码文件在单次 PowerShell 进程中构造测试 DSN，未打印或提交密码。管理员连接核对为 `graphrisk` 数据库和 `graphrisk` 用户；迁移 Stage07 与 pgvector schema 后给 `graphrisk_app` 授权。测试 DSN 使用管理员角色（测试会执行迁移），受限应用角色单独核验；固定政策索引导入 **16** 段，受限角色查询得到 16 段。

- `uv run --offline pytest -q tests/integration/test_stage07_postgres.py tests/integration/test_stage07_pgvector.py tests/unit/test_stage07_compose.py`：**22 passed、1 warning**，其中 18 项真库集成测试和 4 项 Compose 单元测试全部执行。新增单元测试锁定调试网络配置。
- 在同一隔离库用 `GRAPHRISK_STAGE07_DSN`=受限应用角色、`GRAPHRISK_STAGE07_MIGRATION_DSN`=管理员角色、`GRAPHRISK_STAGE07_EVAL_ISOLATED=1` 运行 `uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage07-v2-all --require-complete`：**15/15 passed、0 failed、0 not_run**。忽略产物：`reports/generated/stage07/agent-eval-v2-initial-20261002T151738Z.json`。
- 带隔离真库 DSN 重跑全量 `uv run --offline pytest -q`：**470 passed、0 skipped、1 warning**；另重跑 `uv run --offline python scripts/evaluate_stage06_agent.py` 为 fake **50/50**，真实本地样本此次 0，不将 fake 分数当成模型质量。

数据库回归门禁现已通过。剩余工程门禁是 DeepSeek **正式逐请求账单与内部预算账本对账**；L8 的亲自操作与讲解仍由学习者完成。Stage08 继续 In Progress，不因 Docker 修复或 fake 案例通过而提前标记 Done。

## 2026-10-07 最后费用门禁关闭与工程总验收

用户提供的用量及费用 CSV 已离线核对，结果 `aggregate_reconciled`：2026-09-30 北京时间 16:00～17:00，deepseek-flash，共 40 次请求；缓存命中/未命中/输出为 7680/11206/704 Token。官方 Paid/CNY **0.0283512 元**精确等于导出单价乘用量；内部上界 **0.028383 元**与实扣的 **0.0000318 元**差额由 40 次逐笔向上取整到 micro-CNY 全部解释。原始运行和预算库不改写，逐请求实际费用未知。核验脚本/JSON 与 CSV 都保存在忽略目录，公开证据见[路线 3 最终记录](stage-08-route-3-deepseek.md)。

明确修订本次费用验收粒度为完整小时汇总对账，不能宣称逐请求或逐 Key 实扣。用量导出仅一个脱敏 Key、可见字符与当前配置匹配；全部请求与 Token 已被原始运行解释。其他工程门禁沿用已记录的 2026-10-02 验收证据（根 470 passed/0 skipped、Stage07 15/15、Stage06 50/50，以及独立核心/演示测试与安装复现），本次没有修改业务代码或重新调用模型，也未冒称今天重跑全量测试。

**Stage08 路线 0～8 工程 Done。** 个人 L0～L8 自测另记。已提醒 Stage09 开头先冻结多笔不同交易的真实本地 Agent 基线，再诊断 GraphSAGE 两跳传播、实现/特征口径、采样、特征与关系消融、训练充分性和调参；保留旧 checkpoint，调参只用训练/验证数据。当前只做交接，不自动训练。

2026-10-07 完工后复测补充：Stage08 独立核心/UI、真实两跳/拒答和费用证据再次通过；根 452 passed/18 skipped。当前 Docker/WSL 资源不足，旧真库门禁无法本轮重跑；此前 470/15 仅是历史成绩，不作为今天数据库通过的证明。见[Stage01～08 本轮复测](stage-01-08-recheck-20261007.md)。

2026-10-07 Docker 恢复后的跨阶段验收：真实 Engine 29.8.0、磁盘 D:\docker2\DockerDesktopWSL；新隔离数据库中根 470 passed/0 skipped，Stage07 冻结 15/15、Stage05/06 机制 17/17 与 50/50，独立核心/UI、真实本地调查及 Stage06/07 容器重启/断连/卷恢复均通过。项目终端加入本地 NO_PROXY；首次引擎中断和验收脚本错误保留。测试栈已关闭释放内存，镜像/卷保留，新增付费与训练均为 0。详见[恢复完整复测](stage-01-08-docker-recheck-20261007.md)。单交易真实样例与 GraphSAGE 质量边界保持，Stage09 尚未开始。
