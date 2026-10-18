# 路线5：图片演示脚本与个人彩排

2026-10-18，用户明确要求“用图片演示，不用录屏”。本路线交付改为真实页面截图、演示脚本、实际审批/刷新证明和图片出处清单；视频不再是本路线必需项。个人L5依旧Pending，模型质量门槛不变。

## 先看已交付图片

本机完整图册：E:/GraphRisk-Agent/reports/generated/stage10/route5/20261018-pictures-v1/picture-gallery.html；Markdown版本同目录picture-walkthrough.md。9张原始JPEG在images/下，images-manifest.json登记尺寸、字节SHA256、说明和来源会话。截图来自实际运行的Streamlit网页，没有画图重建UI、拼接或改写像素。

图册/原图含本地数据派生报告，留忽略目录，不自动上传或随Git发布。公开源码包含本页脚本和启动入口，不携带私有冻结报告或图片。

## 用3～5分钟讲完的顺序

以下为建议讲述时长，不是视频时长或测量延迟。本人尚未完成口述/练习，不代签。

| 顺序/建议时段 | 图片 | 要讲什么/实际证明 |
|---|---|---|
| 0:00～0:25 | 01-overview.jpg | 历史真实报告回放，当前增强profile的已存结果，本次零模型推理；memory审批是新演示记录 |
| 0:25～0:55 | 02-model-and-relations.jpg | XGBoost主分数0.0545；GraphSAGE实验分数0.0961，不参与主评分，二者未校准 |
| 0:55～1:20 | 08-relation-only-neighbors.jpg | 严格历史邻居；无独立分数显示为缺分，不能补0或直接断言欺诈团伙 |
| 1:20～1:55 | 03-policy-citation.jpg、09-policy-source.jpg | 模拟政策原文、版本、chunk和来源文件；引用核验不等于适用性已证实 |
| 1:55～2:25 | 04-pending-approval.jpg | 绑定report_sha256、演示reviewer_id和备注，说明无认证身份和无真实封卡 |
| 2:25～2:55 | 05-rejected.jpg | 实际浏览器点击拒绝；审批审计从1到2，外部处置仍0 |
| 2:55～3:25 | 06-refresh-preserved.jpg | 刷新同run_id，拒绝状态/备注和报告hash保留，不重新推理 |
| 3:25～4:00 | 07-blocked-case.jpg | 原case04错误输出仍被拦截，无合格报告/审批；当前首次质量仍6/10 Fail |

失败页统一显示generation_unavailable。这个原案例实际是模型语义矛盾，不能把截图文字解释成当前Ollama断线；本轮根本没有调用Ollama。该UI错误类别较粗，作为已知展示限制保留，后续质量专项再评审。

## 来源和版本

成功图来自路线2real-v2的case01，交易3400379，主模型x3-fold-2-historical-v1、实验图c4-fold-2-three-seed-mean-v1、本地生成模型qwen3:4b固定digest。失败图来自同版本case04。它们是路线2既有首次记录的展示选择，不是新增独立测试，不删除另外3笔失败，也不改变10笔首次/2重复的分母。

原报告SHA256为a86edecbf79d1c0574986e058fb718a57fa06e7d6376eb3e97464169af408de7，审批前后及刷新后均相同。原始生成/训练账本保持142/144剩2，无付费；页面中的旧Token/耗时属于当时推理，不能当成本次回放性能。

## 在本机重复彩排

先按[本机初始化](stage-10-author-workspace.md#初始化)设置C源码/E环境。启动新唯一目录；不要覆盖本次browser-v1/rehearsal-v1：

```powershell
$source='C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$demoRoot='E:\GraphRisk-Agent\reports\generated\stage10\route5'
$demoOutput=Join-Path $demoRoot ('personal-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& "$source\scripts\run_stage10_picture_demo.ps1" -Action serve -ExecutionRoot E:\GraphRisk-Agent -OutputDirectory $demoOutput -UiPort 8531
```

脚本只复用已存ReliableRun与既有ReplayRunner/Stage06Service，不创建模型provider、不加载真实权重、不启动数据库。先核对原始12条结果/来源绑定，再开随机loopback API和8531工作台；控制台输出带run_id的完整URL。session.json记录成功/被拦截run_id、版本及模式。若8531被占用，另选空闲端口，不停止其他服务。

打开输出的成功URL，确认“历史真实报告回放”横幅。读证据，填演示复核员与备注，点“拒绝复核建议”，再点刷新；使用同一个run_id。要看失败案，将URL的run_id换成session.json的blocked_run_id。不要点击“开始新调查”；回放仅接受两个冻结请求，输入其他交易会拒绝，不会退回真实模型。ready预期503，因为没有装载新调查依赖，这不影响读取已存报告。

演示是memory，API关闭后本轮状态消失；它不是PostgreSQL恢复演练。停止方式：在运行窗口Ctrl+C，或在另一个初始化窗口给自己的$demoOutput写stop.request；脚本仅停止自己新开的API/UI，不操作其他服务、Docker或旧记录。最长运行30分钟，审批仍用应用的原TTL。

只做自动彩排、不持续开网页时，换新目录执行：

```powershell
$checkOutput=Join-Path $demoRoot ('check-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& "$source\scripts\run_stage10_picture_demo.ps1" -Action check -OutputDirectory $checkOutput
```

check真实HTTP及AppTest覆盖9项行为：冻结请求拒绝边界、结果深拷贝、原报告hash、增强主分数展示、回放标识、人工拒绝/刷新、同决定幂等、失败无审批、诚实not_ready；另记录HTTP/AppTest执行方式。自动彩排不代表本人完成，也不代替真实浏览器截图，本路线两类证据分别保存。

## L5：90分钟学习

分三次30分钟：先按图用自己的话讲交易/主分数/关系/政策/未知；再用新会话实践拒绝与刷新，记录自己操作及遇到的问题；最后以原始失败为例解释6/10、安全拦截与可用性的区别，练一次3～5分钟讲述。写下“主分数未校准”“共享实体不证明同人”“引文不证明适用”“审批不执行封卡”的各一句解释。

当前图片操作由代理完成，本人L5/至少10案实用性复核仍Pending。下一路线6做干净检出与公开/真实工件模式复现；Agent质量专项等工程总审计后另行提醒。
