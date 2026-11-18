# GraphRisk-Agent 当前项目入口

更新时间：2026-11-18（Asia/Hong_Kong）。本文件是当前状态与下一步工作的唯一摘要入口；详细计划、实现和证据仍以链接中的原件核验。用户最新指令和AGENTS.md中的工程规则继续适用。

## 1. 从哪里开始

先读所在目录的AGENTS.md，再读本文件；随后只按当前任务读取下表对应的计划或证据。README用于项目介绍，PROJECT_PLAN和阶段索引用于总体规划及历史记录，不根据旧段落的“当前／已完成”字样选择运行版本。

默认开发阅读范围是AGENTS → 本入口 → 当前任务的活跃计划与对应源码。历史验收、旧实验和交接快照只在具体问题需要追溯时定向读取；测试夹具输出和压缩恢复包不作为当前进度依据。旧文档中的“下一步”、命令或额度描述不覆盖本入口及用户最新要求。

首次接手、代码审查或准备工程收敛时，继续读[项目结构与Agent接手说明](docs/项目结构与Agent接手说明.md)：说明实际主结构、三个服务的分工、C／E源码来源、成果与验收边界，以及按任务定位代码的方法。本文仍是实时状态的唯一摘要入口；说明书不另外维护余额或进度表，提出的源码／后端收敛方案尚未实施。

| 位置 | 当前职责 |
| --- | --- |
| `C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent` | 最新应用源码基线；当前网页从这里导入graphrisk/API代码。 |
| `E:\GraphRisk-Agent` | 当前工作目录；保存本入口、活跃计划、Python环境、数据、模型和运行证据。不能把E盘同名源码目录自动视为最新应用源码。 |
| `E:\GraphRisk-Agent\artifacts\agent-trial-20261115-2u0xpeep` | 原调查后端8002及报告适配依赖继续保留；8501网页已切换web扩展，不能删除旧后端依赖。 |
| `E:\GraphRisk-Agent\extensions\case_qa_v1` | 自由问答独立扩展源码；第1～5步离线实现通过，含独立Streamlit预览与fixture预算执行器；独立预览未启动；现由web扩展间接复用其冻结会话／证据／预算基线，不在本目录或C冻结源码内改写。 |
| `E:\GraphRisk-Agent\extensions\case_qa_live_v1` | 第6步原正式适配；已获两案最多6次／5元批准并注册，但第一问在发送前因新旧身份兼容缺陷停止，实际请求／新增费用0。原源码、批准、grant及空会话保留，不能再用旧入口尝试发送。 |
| `E:\GraphRisk-Agent\extensions\case_qa_live_v2` | 身份兼容修正版；333项合并离线检查通过，已获修正版接续批准。实际执行M1首问1次，身份核验与usage结算通过，但回答合同未通过，已按约停批；不再运行剩余5问。新服务未启动，第6步尚未完成。 |
| `E:\GraphRisk-Agent\extensions\case_qa_quality_v1` | 新问答合同与表达候选；414项离线检查通过，含具体诊断、完整格式提示、短答优先界面及fixture预算整合。仅合成预览／离线回放，无正式付费适配或外发许可；未接当前网页，真实生成质量待验证。 |
| `E:\GraphRisk-Agent\extensions\case_qa_live_v3` | 新提示正式适配；450项离线检查通过，已获具体接续批准。实际执行M1首问1次，身份及结算通过，但总项22超过12而未通过；已按约停批，剩4问未发送，不自动继续。真实多轮、页面与用户阅读仍未验收。 |
| `E:\GraphRisk-Agent\extensions\case_qa_short_v1` | 短答提示接续版；501项离线检查通过并获具体接续批准，实际仅执行L1首问1次，18项超过12项而invalid_response，已停批，余3问未发。不再运行此版新发送；原文、回执及费用保留，页面与多轮仍未验收。 |
| `E:\GraphRisk-Agent\extensions\case_qa_compact_v1` | 紧凑返回结构接续版；624项离线检查通过并获具体批准，实际L1两问：首问answered、追问review_required，按约停批，M1未发。追问为否定句误报，写作仍偏长／重复；余1次不自动续，原文和判决保留。 |
| `E:\GraphRisk-Agent\extensions\case_qa_web_v1` | 原两分区网页和六次试用门禁；已被新单交易工作台替代，作为冻结依赖、失败及回退证据保留，不再作为8501启动入口。 |
| `E:\GraphRisk-Agent\extensions\transaction_workbench` | 当前8501单交易页面及8003问答服务：风险与证据 → 报告 → 自由提问。累计100次／5元，计入原六次和原费用；原冻结会话、证据合同和单次发送运输复用。 |

两处目录的代码不自动相互覆盖。运行配置或源码位置改变时，先核验绑定，再更新本文件中的指向。

2026-11-18按用户要求补齐修改前的本地Git检查点：C核心分支`codex/stage09-version-closeout`提交`8ec3dad`，E工作台分支`codex/workbench-checkpoint-20261118`提交`ecaa982`；未推送。E提交包含当前实际依赖的12份试用适配／启动源码，生成材料、数据、模型、环境和密钥不入库，未审查的`deliverables/`、`projects/`仍留在提交范围外。两分支共用Git元数据，检查点不表示源码已合并。精确提交、范围和绑定核验见[检查点回执](artifacts/git-checkpoint-20261118/CHECKPOINTS.json)。

## 2. 当前怎么启动

网页：[http://127.0.0.1:8501/](http://127.0.0.1:8501/)。8003和8501采用[start_transaction_workbench.ps1](scripts/start_transaction_workbench.ps1)；8002仍使用原本地调查后端。已有对应服务在线时不要重复启动。

分别在三个PowerShell窗口执行并保持开启：

```powershell
# 原本地调查与封批证据后端8002
& "E:\GraphRisk-Agent\artifacts\agent-trial-20261115-2u0xpeep\start-recovery.ps1" -Component api
```

```powershell
# 单交易工作台问答服务8003
& "E:\GraphRisk-Agent\scripts\start_transaction_workbench.ps1" -Component api
```

```powershell
# 单交易页面8501
& "E:\GraphRisk-Agent\scripts\start_transaction_workbench.ps1" -Component workbench
```

页面只有一个交易选择入口：上方风险和证据，中间报告，下方DeepSeek问答。默认日终XGBoost 216+49，已存M1／L1会话按交易恢复，不再展示这些测试别名或试用轮次。刷新、查看报告与启动均不调用模型；只有主动发送问题才外发本案脱敏证据及最近三轮成功问答。没有对应旧首稿的交易显示程序证据报告，不自动生成新报告。

用户明确要求网页问答累计上限100次、费用上限5元。[具体设置与授权](reports/transaction-workbench/OUTBOUND-APPROVED.json)将原6次／0.098108元计入，整合验收时可用94次／4.901892元估算余额；后续主动使用会改变余额，实时值查8003状态接口与账本。次数跨会话、跨重启共享；未知请求保留预约并暂停，不自动重发、修复或付费评审。沿用单请求2.105344元最坏预约，因此余额不足预约时会提前停止；额度是上限，不保证一定能发送100次。单会话存储上限仍12条，可在同页新建同案会话，累计预算不重置。每天正式发送仍需当日价格核验，过期时明确阻断。

```powershell
Invoke-RestMethod http://127.0.0.1:8002/health
Invoke-RestMethod http://127.0.0.1:8003/health
Invoke-RestMethod http://127.0.0.1:8003/api/v1/workbench/status
Invoke-RestMethod http://127.0.0.1:8501/_stcore/health
```

[本轮核验](reports/transaction-workbench/VERIFICATION.json)记录实际进程、读取延迟与调用计数。旧启动器及旧六次试用原件保留回退，不与新服务同时占用8003／8501。

## 3. 已采用什么，哪些尚未完成

| 项目 | 当前采用／结论 | 按需读取 |
| --- | --- | --- |
| 风险模型 | 网页默认日终XGBoost 216+49，属于当前主模型候选／试用分档；即时216+49保留扩展，原x3保留回退。高／中／低是调查优先级。 | [日批接入记录](docs/data/agent-investigation-web-live-trial-20261115.md) |
| 主报告 | 沿用用户已认可的日终v12 quotes-v2与精简呈现。认可旧批报告不等于所有新首稿自动通过。 | [认可记录](reports/generated/agent-investigation-upgrade/route5/report-v3/20261112-daily-quotes-six-v2/USER-READING-ACCEPTANCE.json) |
| 网页能力 | 同笔交易的风险、证据、报告、问答已整合；服务端累计100次／5元已启用。整合验收快照为6次／0.098108元，整合操作新增真实模型请求0；实时余额查状态接口。原问答质量问题保留，未称写作验收通过。 | [整合时核验](reports/transaction-workbench/VERIFICATION.json) |
| 本批网页首稿 | 本次只读核验已占2/6、余4次；H1响应未知并保留2.12992元预约、禁止重发；H2真实返回但两项待复核，未发布正式报告。费用预约不等于确认扣费。 | [执行记录](reports/generated/agent-investigation-upgrade/route5/report-v3/20261116-web-live-proxy-recovery-v1/LIVE-EXECUTION-RESULT-20261116.json)、[H2原稿](reports/generated/agent-investigation-upgrade/route5/report-v3/20261116-web-live-proxy-recovery-v1/H2-FIRST-DRAFT-REVIEW.md) |
| 路线七 | 调查参考资料工程已收尾，采用精简附录；不表示政策条件已在真实业务核实。 | [收尾记录](reports/generated/agent-investigation-upgrade/route7/20261113-boundary-closeout-v1/REPORT.md) |
| 路线八 | 评审辅助仍在评估，全文评委尚未合格，不能自动验收报告。 | [整稿评审结果](reports/generated/agent-investigation-upgrade/route8/20261114-daily-whole-judge-v1/REPORT.md) |
| 路线九 | 按用户要求暂停、未闭批。DeepSeek60案＋5重复已有结果；本地25/60首案、0/5重复，剩35首案＋5重复，最多200次；质量仍未完成验收。 | [暂停进度](reports/generated/agent-investigation-upgrade/route9/20261114-full-case-readiness-v1/CURRENT-PROGRESS.md) |

这张表区分已采用的版本、真实执行与质量验收，不把单项测试或用户阅读认可当作全项目完成。

## 4. 现在做什么，下一步是什么

自由问答六步中的离线合同、服务端会话、证据合同、界面和预算均已实现，第6步的真实链路已有历史验证；稳定多轮、写作及用户阅读验收仍未全部完成。[活跃计划](docs/plans/DeepSeek自由问答改造计划.md)记录各版原始判决与限制。

此前整合时按用户要求采用[单交易工作台](extensions/transaction_workbench/web.py)，并在独立[设置](reports/transaction-workbench/config.json)中延续原6次费用，不覆盖旧授权或账本。该次新增1份设置grant，真实模型请求0；当时全局1810＝DeepSeek1715＋本地95。原历史、原六条QA、旧5份grant和提供者身份记录均未变，H1及全局未知预约8.466432元保留。以上计数是整合时快照，不能代替后续实际使用记录。总次数2119和总费用100元／80元暂停线不扩大；在总上界内为网页94次余量使用DeepSeek子上界1813，并保留原报告4次与路线九本地200次及暂停状态。

报告沿用已有原文。不同查询生成的事实ID会变化，因此历史首稿只有在交易、评分封批上下文、每条事实的指标／值／缺失／时间范围／模型以及政策全部一致时才显示，并明确保留历史版本和原门禁结果；不把旧报告作为新问答证据，不重新判分。三份空会话的**新库导入副本**已排除，以免覆盖有内容的历史会话入口，原数据库逐字保留，副本行另存[恢复记录](reports/transaction-workbench/EMPTY-ARCHIVE-COPIES.json)。

**下一步：** 用户在单页选择交易并阅读、主动提问；再根据实际反馈改善短答、问题聚焦和排他表达。原M1长答及“只共享邮箱域”的支持缺口、L1否定句误报仍未修复，不将界面整合当成报告质量提升。无自动试用、自动生成报告、H1重发或本地4B接续。

修改前Git检查点已补齐；本次工作台回归改为17项API／合同检查＋1项AppTest提交冒烟，[最终整组](artifacts/transaction-regression-final.xml)18通过、2项原UI被明确替代。已修复合成测试对宿主空闲内存的依赖，生产保护不变；Work Buddy原偶发失败未独立复现，不能直接认定为AppTest丢提交。[验证边界与证据](docs/plans/DeepSeek自由问答改造计划.md#2026-11-18git检查点与工作台测试稳定性)及[当前测试命令](docs/项目结构与Agent接手说明.md#8-其他-agent-应怎样安全地开始审查或修改)供后续Agent使用；本次无真实调用或服务切换。后续功能修改应从这两个源码分支分别检查差异，源码与后端收敛仍是独立迁移任务。

## 5. 每次任务如何收尾

每次任务在最终汇报前执行 [AGENTS.md中的任务收尾与资料维护流程](AGENTS.md#task-closeout)。E盘与C源码入口均要求先读本文件，共用这一份流程；不在其他README、计划或日期文档重复清单。

本文件只更新经核验的当前摘要，活跃计划记录验收，资料索引记录材料用途；已有合同与历史回执继续保存原始执行事实。无相关变化时不为收尾强制写文档，也不自动扩大清理、重启暂停任务或新增模型调用。
