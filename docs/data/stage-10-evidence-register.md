# Stage 10 交付证据台账

2026-10-17 路线 0 建立；[交付合同](stage-10-delivery-contract.md)、[路线 0 验收](../../reports/stage-10-route-0-preflight.md)。状态区分“旧证据已核对”“本阶段待验证”“本人 Pending”，不因为有旧报告就将最终交付全部勾选。

## 总计划七项成功标准

| 编号 | 原成功标准 | 目前证据与缺口 | 本阶段关闭路线 |
|---|---|---|---|
| S1 | 新环境按 README 安装运行核心 Demo | 已有干净检出、两 wheel 与镜像烟测；完整新读者公开演示/冷缓存口径仍待本阶段核对，不能只用 wheel 代替 | 4、6、8 |
| S2 | 时间切分/历史特征自动测试，无已知未来泄漏 | [全链复测](../../reports/stage-01-09-recheck-20261017.md)：真实切分、18 独立历史重算及图时间测试；基线工程通过，最终变更后需适用回归 | 3、8 |
| S3 | 同时间集合比较树/图/简单融合并给决策 | [三折确认](../../reports/stage-09-route-5b-5-confirmation.md)：XGB ROC/AP 0.905089/0.544367，图增量 0 不晋级；[模型卡](stage-09-5b-model-card.md)解释输入差异与限制 | 3、8 |
| S4 | 至少 50 Agent 案例 | [50 案](../../reports/stage-06-route-6-agent-evaluation.md)、[15 挑战](../../reports/stage-07-route-6-agent-evaluation.md)与历史多交易质量各自分母；50 为机制案例，不是 50 真模型 | 3、8 |
| S5 | 工具/参数/引用/完成/恢复/延迟/费用评估 | 工具是有界信任流程与参数 schema 校验，非开放 LLM 规划；已有历史评估/恢复/小时账单，当前增强质量及统一分层性能待新增，最终汇总未完成 | 1、2、3、8 |
| S6 | 现场演示模型、关系、政策、人工审批 | 历史真实案例及当前工具/fixture/回放通过；当前增强真实流程彩排/录像和本人操作仍待完成 | 2、5、7、8 |
| S7 | 源码/配置/测试/文档可公开，秘密和受限数据不入 Git | 已有 Git/CI/镜像公开边界；最终英中文 README、演示及提交还需复核，不能以旧提交替代新提交 CI | 4、6、8 |

## 可复用证据及正确用法

| 主张 | 精确证据 | 分母/版本/限制 |
|---|---|---|
| 工程自动测试 971 通过/1 skip | [跨阶段复测](../../reports/stage-01-09-recheck-20261017.md)；私有 stage01-09-recheck/20261017-v1/acceptance.json | 主项目 877（已含 26 真库）+GraphRAG 91+3；0 失败/0 错误，Windows 符号链接权限 skip；本路线未重跑 |
| 六项远程 CI 成功 | [e443ed5 的 CI](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39031393216) | 对应已冻结基线，Stage 10 新提交须核对自己的 run |
| 真实覆盖 177,162 | [5B 覆盖](../../reports/stage-09-route-5b-6-coverage.md)、复测 real-coverage/acceptance.json | validation/test 各 88,581；不是全体 590,540 独立评分；5 新事件/3 重复是真权重一致性，不是准确率 |
| 历史 5A 受限质量 30/30、10/10 | [原路线 8](../../reports/stage-09-route-8-acceptance.md)、复测 audit-evidence-v1 | 开发/重复另列，45 不同交易/63 生成的组成保留；当前增强 profile 未新增真实质量 |
| GNN 不晋级 | [三折确认](../../reports/stage-09-route-5b-5-confirmation.md) | 三折 alpha=1、AP 增量 0/正收益 0/3；不说所有 GNN 无效或已穷尽调参 |
| GraphRAG hybrid 18/22、两跳 7/8 | [Stage 08 总验收](../../reports/stage-08-route-8-acceptance.md)、复测 90 条 rescore | 30 问×3 模式的首次保存结果；text 16/22 未达门槛，graph 同为 18/22；原创模拟语料 |
| DeepSeek 0.0283512 元小时对账 | [DeepSeek 验收](../../reports/stage-08-route-3-deepseek.md)、复测 stage08-billing.json | 20 案+20 校准=40 请求；非逐请求实际账单；本阶段不新增付费调用 |
| 备份恢复通过 | [原路线 7](../../reports/stage-09-route-7-observability-safety.md)、audit-check-v1 | 另一个新库 12 表 88 行、15 探针历史证据，经后端/备份 hash 核验复用；当前实例恢复与备份恢复不是一回事 |

## 本阶段待补项

- 当前增强 profile 的 10 笔首次真实小样本和固定重复，先冻结、后请求；标签/未知/失败分母保留。
- 分层性能逐请求记录；冷暖/加载/并发/错误率/资源；小样本 P99 未测不填 0。
- 最终评估/卡片/限制、清晰英中文 README、架构/时序与模式启动说明。
- 3～5 分钟真实录制、本人操作和至少 10 案实用性复核；现为 Pending。
- 新检出核心演示、真实数据/权重前置、冷缓存口径和最终提交自己的 CI。

## 追加证据的规则

新增条目记录日期、Git/配置、实验目录、数据/问题分母、计分方法、结果与限制。原始行级/Prompt/凭据留私有，公开只汇总。记录“不知道”“失败”“未运行”；后续修复保留首次结果。不把所有行都设 Pass 作为交付目标。
