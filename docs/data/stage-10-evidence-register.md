# Stage 10 交付证据台账

2026-10-17 路线 0 建立；[交付合同](stage-10-delivery-contract.md)、[路线 0 验收](../../reports/stage-10-route-0-preflight.md)。状态区分“旧证据已核对”“本阶段待验证”“本人 Pending”，不因为有旧报告就将最终交付全部勾选。

## 总计划七项成功标准

| 编号 | 原成功标准 | 目前证据与缺口 | 本阶段关闭路线 |
|---|---|---|---|
| S1 | 新环境按 README 安装运行核心 Demo | 路线6新检出/新.venv有缓存安装、公开50案/合成流水线与真实工件通过；独立GraphRAG另建最小环境；冷缓存/原始重训未测，最终审计待路线8 | 4、6、8 |
| S2 | 时间切分/历史特征自动测试，无已知未来泄漏 | [全链复测](../../reports/stage-01-09-recheck-20261017.md)：真实切分、18 独立历史重算及图时间测试；基线工程通过，最终变更后需适用回归 | 3、8 |
| S3 | 同时间集合比较树/图/简单融合并给决策 | [三折确认](../../reports/stage-09-route-5b-5-confirmation.md)：XGB ROC/AP 0.905089/0.544367，图增量 0 不晋级；[模型卡](stage-09-5b-model-card.md)解释输入差异与限制 | 3、8 |
| S4 | 至少 50 Agent 案例 | [50 案](../../reports/stage-06-route-6-agent-evaluation.md)、[15 挑战](../../reports/stage-07-route-6-agent-evaluation.md)与历史多交易质量各自分母；50 为机制案例，不是 50 真模型 | 3、8 |
| S5 | 工具/参数/引用/完成/恢复/延迟/费用评估 | 工具是有界信任流程与参数 schema 校验，非开放 LLM 规划；已有历史评估/恢复/小时账单，当前增强质量6/10 Fail与分层性能已实测；路线3已统一汇总，个人实用性Pending | 1、2、3、8 |
| S6 | 现场演示模型、关系、政策、人工审批 | 路线5实际9图及路线6新环境HTTP/AppTest回放通过；用户以图片替代视频，来源/回放明确；本人操作和实用性仍Pending | 2、5、7、8 |
| S7 | 源码/配置/测试/文档可公开，秘密和受限数据不入 Git | 已有 Git/CI/镜像公开边界；最终英中文 README、演示及提交还需复核，不能以旧提交替代新提交 CI | 4、6、8 |

## 可复用证据及正确用法

| 主张 | 精确证据 | 分母/版本/限制 |
|---|---|---|
| 工程自动测试 971 通过/1 skip | [跨阶段复测](../../reports/stage-01-09-recheck-20261017.md)；私有 stage01-09-recheck/20261017-v1/acceptance.json | 主项目 877（已含 26 真库）+GraphRAG 91+3；0 失败/0 错误，Windows 符号链接权限 skip；本路线未重跑 |
| 六项远程 CI 成功 | [e443ed5 的 CI](https://github.com/Zzfzys/GraphRisk-Agent/actions/runs/39031393216) | 对应已冻结基线，Stage 10 新提交须核对自己的 run |
| 真实覆盖 177,162 | [5B 覆盖](../../reports/stage-09-route-5b-6-coverage.md)、复测 real-coverage/acceptance.json | validation/test 各 88,581；不是全体 590,540 独立评分；5 新事件/3 重复是真权重一致性，不是准确率 |
| 历史 5A 受限质量 30/30、10/10 | [原路线 8](../../reports/stage-09-route-8-acceptance.md)、复测 audit-evidence-v1 | 开发/重复另列，45 不同交易/63 生成的组成保留；旧profile；当前增强成绩另见E04，不合并 |
| GNN 不晋级 | [三折确认](../../reports/stage-09-route-5b-5-confirmation.md) | 三折 alpha=1、AP 增量 0/正收益 0/3；不说所有 GNN 无效或已穷尽调参 |
| GraphRAG hybrid 18/22、两跳 7/8 | [Stage 08 总验收](../../reports/stage-08-route-8-acceptance.md)、复测 90 条 rescore | 30 问×3 模式的首次保存结果；text 16/22 未达门槛，graph 同为 18/22；原创模拟语料 |
| DeepSeek 0.0283512 元小时对账 | [DeepSeek 验收](../../reports/stage-08-route-3-deepseek.md)、复测 stage08-billing.json | 20 案+20 校准=40 请求；非逐请求实际账单；本阶段不新增付费调用 |
| 备份恢复通过 | [原路线 7](../../reports/stage-09-route-7-observability-safety.md)、audit-check-v1 | 另一个新库 12 表 88 行、15 探针历史证据，经后端/备份 hash 核验复用；当前实例恢复与备份恢复不是一回事 |

## 本阶段交付与剩余项

- 当前增强profile已完成首次6/10 Fail与重复2/2；原始失败保留。
- 分层性能已完成，详见路线2；小样本P99保留null，不承诺生产吞吐。
- 最终评估/卡片/限制已由路线3完成；英中文README、架构/时序与模式启动已由路线4交付；路线5图片彩排与路线6新检出/新环境复现已完成，条件/范围各自记录。
- 按2026-10-18用户修订，路线5已交付9张实际页面图片和演示脚本；本人操作和至少10案实用性复核仍Pending。
- 新检出核心演示/真实工件前置与缓存口径已验收；冷缓存/原始重训未测，最终提交自己的CI留路线8。

## 追加证据的规则

新增条目记录日期、Git/配置、实验目录、数据/问题分母、计分方法、结果与限制。原始行级/Prompt/凭据留私有，公开只汇总。记录“不知道”“失败”“未运行”；后续修复保留首次结果。不把所有行都设 Pass 作为交付目标。

## 2026-10-17 路线1新增证据

[验收](../../reports/stage-10-route-1-measurement-contract.md)：当前增强profile的10+2输入已冻结、49匹配旧ID排除；S5的统计/预算/计时运行器已有47新增机制测试，纳入CPU898/1平台skip。12次模拟HTTP与假时钟演练不是新增真实质量或性能；S5实测仍待路线2，S6现场/视频和本人实用性仍Pending。原391源码+2计时附录分别绑定，路线2需登记实际执行；旧生成130/144不改，真实追加0。

## 2026-10-17 路线2实测交接

[正式报告](../../reports/stage-10-route-2-real-measurement.md)：10笔首次真实受限判断6/10，门槛9/10未达，质量Fail；2次重复通过不补分。四笔错误输出均被拦截，unsafe0。实测工程已完成，Stage10整体仍In Progress，路线3～8和本人学习/人工实用性待完成。唯一台账142/144、剩2，无新训练/付费。当前新质量不能用历史5A40/40代替。

S5新增真实证据：首次6/10 Fail，模型HTTP12/12正常返回、调查HTTP8/12正常成功，两个分母不同；完整HTTP P50/P95=11.36/13.21s，小样本P99=null。S6已有当前配置真实报告/审批记录，现场彩排、录像与本人实用性仍Pending。

## 2026-10-18 路线3最终主张索引

[12项公开主张](stage-10-final-claims.md)对应[最终评估](../../reports/stage-10-final-evaluation.md)。原始证据/版本/分母与新重算或历史复用分别列出。S3负实验结论保持；S4机制数量不冒充真模型；S5质量Fail与性能已测分开；S6录像/本人仍Pending；S1/S7最终交付待路线4～8。

## 2026-10-18 路线4交付

[验收](../../reports/stage-10-route-4-readme-startup.md)：新首页/中文说明、源码映射架构/调查审批恢复时序、通用/作者分离启动及L4材料已提供。S1已有当前命令检查，新环境仍待路线6；S6未新增现场/录像；S7文档检查与本地版本保存，不冒称新提交CI。新真实生成/fit/费用0，公开机制50案不增加模型质量分母。

## 2026-10-18 路线5图片交付验收

用户明确用图片代替录屏，路线5及总交付合同已据此修订；不再将视频缺失列为该路线阻塞。[图文脚本与L5](stage-10-picture-demo.md)、[验收](../../reports/stage-10-route-5-picture-demo.md)和scripts/run_stage10_picture_demo.ps1交付。9张真实浏览器JPEG/本地图册在E:/GraphRisk-Agent/reports/generated/stage10/route5/20261018-pictures-v1/。

来源是路线2当前增强profile的case01成功/case04失败，均为已存首次结果；新memory审批实际拒绝并刷新，报告hash保持、外部动作0、失败仍503。自动HTTP/AppTest彩排9项行为检查通过；本轮无新模型/训练/费用，142/144剩2。代理图片不代签本人L5/人工实用性；Agent质量仍6/10 Fail、GNN不晋级。

当前路线0～5工程Done，6～8 Planned，下一路线6干净检出与双模式复现。stage10-agent提醒仍待工程总审计完成后触发；后续质量/GNN专项未启动。新增PowerShell编排单独hash登记，旧测量源码/结果不改。

## 2026-10-18 路线6新检出复现验收

[指南与L6](stage-10-clean-reproduction.md)、[验收报告](../../reports/stage-10-route-6-reproduction.md)：从a3f1acc创建本地Git clone，610追踪文件核对；主项目及独立GraphRAG分别新建.venv，复用E盘下载缓存/managed Python，不复用旧环境。公开50案机制、合成5000行/特征通过；新环境CPU915通过/1平台symlink skip，独立核心91通过/3可选UI skip。

授权真实工件35输入/177162覆盖闭环、五工具、一笔冻结CPU权重通过，树差0/图差1.09e-8；实际HTTP/AppTest回放拒绝/刷新/幂等与原报告hash保持。新LLM/真实训练/付费0，三旧账本不变、142/144剩2。历史396文件严格字节校验因92文件LF/CRLF不同而改用显式封存快照，实际执行仍为新检出；未改旧结果/hash，辅助脚本失败保留。冷缓存、原始CSV重训、真库/镜像重跑未测，不冒称已完成。

当前路线0～6工程Done，7～8 Planned，下一路线7求职表达与至少10案本人复核。本人L0～L8/实用性Pending；Stage10整体In Progress、Agent6/10 Fail、GNN不晋级保持。stage10-agent提醒等工程总审计后触发；后续Agent质量/GNN检索专项不提前启动。应用代码/锁文件未改，本轮文档本地提交，不冒称新提交远程CI。
