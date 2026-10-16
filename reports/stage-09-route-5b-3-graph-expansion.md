# Stage09 路线5B-3：当前 GraphSAGE 扩量与有限调参

**工程 Done，2026-10-09。** 三个规模与六组预注册参数共九次真实训练全部完成；另有两次资源失败完整保留，共11次GNN尝试。当前候选g3：内层 **AP 0.179596、ROC-AUC 0.768844**。92项相关回归通过、新进程只读核验通过，完整权重重载与独立指标复算差异均0。三折质量、融合增量、服务覆盖和LB3本人学习仍待后续完成，Stage09保持In Progress。

用简单的话说：把现有模型的训练量从5万扩到第一折允许的约25万，再试六组有限参数。扩量后表现提高了，但调参没有弥补与增强XGBoost的明显差距。下一路线已触发条件增强，先做消融查明信息与关系的价值；本轮结果不能证明GraphSAGE已经达到最优，也不能证明图关系没有价值。

## 做了什么

1. 保留现有两层mean GraphSAGE、28维输入和每实体最近5笔严格更早历史，分别构建50k、150k、250,979根的训练缓存与独立训练期encoder。
2. 六组参数均使用完整第一折训练期，有限改变学习率、dropout、隐藏宽度或正例权重。每轮记录加权loss、固定10k训练探针与完整内层指标。
3. 每组恢复内层AP最好的权重，对全部25,098笔重新加载评分；独立复算AP、ROC-AUC、Top1%/Top5%，封存图、源版本、权重、失败与资源证据。
4. 沿用最初5B共享账本，失败耗时和次数不退；使用E盘缓存和临时目录，训练进程临时防自动睡眠，退出后释放。

代码入口：`scripts/run_stage09_graph_expansion.py`；实现位于`stage09_expansion_data.py`、`stage09_expansion_training.py`、`stage09_expansion_execution.py`。契约见[扩量附录](../docs/data/stage-09-graph-expansion.md)、[实施路线](../docs/plans/stage-09-route-5b-model-enhancement.md)、[LB3学习路线](../docs/plans/stage-09-route-5b-learning.md#lb3扩大数据为什么不一定有用150分钟)。

## 数据与时间边界

仍是一个IEEE-CIS数据集，不是九份数据集。九个模型共用第一折25,098笔early-stop，其中1,061笔欺诈，正例率4.2274%；仅读取第一折train和early-stop标签。校准、外层、历史test尚未用于本路线评分或搜索。

| 训练根数 | 欺诈根数 | 训练截止离内层起点 | 缓存节点出现次数 | 双向消息边出现次数 |
|---|---:|---:|---:|---:|
| 50,000 | 1,357 | 4,784,075秒，约55.37天 | 1,895,656 | 4,754,106 |
| 150,000 | 3,970 | 2,880,003秒，约33.33天 | 5,865,839 | 15,044,978 |
| 250,979 | 8,174 | 28秒 | 9,372,426 | 23,865,314 |

节点/边计数是各ego graph的累计出现次数，重叠实体会重复计数，不能称为唯一实体数量。每份内层缓存为894,729节点、2,278,744消息边、350,449次历史交易出现。

全部缓存的未来/同时事件违规0，训练图含内层关系0。完整规模encoder统计与路线2一致，**276,077根 × 28维**与冻结root28矩阵逐项最大差异0；小规模分别拟合encoder。内层允许使用严格更早的无标签内层事件；这是在线历史协议。

**规模实验同时改变了样本量、时间覆盖与正例组成。** 三规模取最早前缀，方案在拟合前登记；没有另跑“相同截止、最近50k/150k”的匹配实验。完整训练与六参数、root28对照的截止一致，但不能用本表单独断言纯样本量收益。旧10k验证成绩也不能直接与本轮25,098内层成绩相减。

## 九次完整训练的结果

AP采用average precision口径，不是准确率或单笔交易风险概率。以下AP/ROC均来自同一个保存最佳轮次；耗时列只列成功尝试，失败单独计入预算。

| 规模，seed20260921 | AP | ROC-AUC | 最佳/停止轮次 | 成功拟合秒 |
|---|---:|---:|---:|---:|
| 50,000 | 0.111899 | 0.714680 | 10 / 20 | 93.13 |
| 150,000 | 0.155513 | 0.731517 | 15 / 25 | 452.40 |
| 250,979 | 0.176763 | 0.769461 | 14 / 24 | 703.26 |

六组搜索统一seed20260928、250,979训练根、最多50轮、连续10轮AP不改善早停，CUDA/batch256/CPU线程4：

| 候选 | lr | hidden | dropout | 正例权重 | AP | ROC-AUC | 最佳/停止轮次 | 成功拟合秒 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| g1 | 0.001 | 64 | 0.20 | 29.704551 | 0.168159 | 0.765244 | 10 / 20 | 597.79 |
| g2 | 0.0005 | 64 | 0.20 | 29.704551 | 0.170003 | 0.767199 | 10 / 20 | 583.51 |
| **g3** | **0.001** | **64** | **0.40** | **29.704551** | **0.179596** | **0.768844** | **18 / 28** | **846.65** |
| g4 | 0.0005 | 64 | 0.40 | 29.704551 | 0.172139 | 0.767005 | 18 / 28 | 855.56 |
| g5 | 0.001 | 128 | 0.20 | 29.704551 | 0.174868 | 0.756683 | 24 / 34 | 989.92 |
| g6 | 0.001 | 64 | 0.20 | 5.450188 | 0.179145 | 0.770115 | 18 / 28 | 835.78 |

g6实际使用负正比的平方根；其loss权重与其他组不同，不能直接比较loss绝对值。g5参数量40,321，其他组11,969，扩大hidden没有超过g3。按预定“内层AP → 耗时 → 登记顺序”选择g3，不能因g6的ROC更高临时改规则。

完整规模与搜索g1是相同参数、不同种子的两次真实训练：AP0.176763与0.168159，相差0.008603。g3与g6的AP差仅0.000451；这些观测提示波动，但不能代替三折三种子确认。

| 同集合比较 | 输入 | AP | ROC-AUC |
|---|---|---:|---:|
| 当前选择g3 | 28维 + 图关系 | 0.179596 | 0.768844 |
| root28 XGBoost | 28维根特征 | 0.164530 | 0.780715 |
| root28 MLP | 28维根特征 | 0.150673 | 0.754283 |
| 路线2选择x3 | 871列宽表输入 | 0.726914 | 0.934365 |

g3对root28树的AP较高，但ROC较低；模型结构、损失和搜索种子也不同，因此尚不能把分差全部归因于关系。宽表x3使用更多信息，匿名供应方字段时点的限制见[路线2报告](stage-09-route-5b-2-baselines.md)，不能据此宣称上线就绪。

g3的Top1%选择251笔，命中96笔欺诈：Precision38.2470%、Recall9.0481%；Top5%选择1,255笔，命中274笔：Precision21.8327%、Recall25.8247%。这些是核查排序指标，不是“模型正确率”。

## 曲线怎么看

- [九组训练探针/内层AP与保存轮次](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/all_learning_curves.png)：蓝线仅固定10k训练探针，橙线完整内层，黑虚线是保存轮次；各小图纵轴范围不同，数值对照以上表为准。
- [规模与加权loss](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/scale_and_loss.png)：左图同时改变规模与时间覆盖；右图各规模正例比例/权重不同，不直接按loss大小排名。
- [同集合PR/ROC](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/ranking_curves.png)：PR灰线为该内层正例率，宽表与28维输入的信息不同。

g5训练探针继续升高，而内层波动且未超过g3，符合泛化受限的表现；单张曲线不能唯一确定原因。当前证据支持继续做信息与关系消融，不支持无限加轮数或直接升级异构模型。

## 两次中断及恢复

| 失败尝试 | 停止原因 | 保留的训练曲线 | 已耗账本秒数 |
|---|---|---:|---:|
| 原150k | 可用RAM降至2.905GiB，低于3GiB | 2轮 | 30.26 |
| 原g2 | 相邻2秒RAM4.442 → 2.890GiB | 13轮 | 412.09 |

150k的v2修复将磁盘映射限定在单个batch：复制成独立PyG张量后关闭映射，下一批重新打开同一文件。等价性测试覆盖节点、边、根、关闭后的张量有效性与异常释放；不改模型、数据、窗口或种子。同参数恢复占failure额度。

g2的v3只新增该失败的恢复登记，读取器与训练算法不变。中断时只读观测Ollama桌面进程内存上升，不能据此断言原因；本实验没有LLM调用。随后可用RAM实际回升至7.136GiB，Ollama无驻留模型；未关闭应用、未把待回复的默认选项视为许可，按原训练授权继续。

独立对比：150k恢复前2轮、g2恢复前13轮的loss、训练探针AP/ROC与内层AP/ROC，五项最大绝对差异均0。失败目录、部分权重和资源记录仍保留，完整成果分别在`scale-150000-retry-1`与`g2-retry-1`。

另一次拟合前GPU空闲暂停保存在`start-gate-pauses.json`，发生在reserve前，不算模型拟合。空闲探针最多5次、间隔2秒，仅等待GPU利用率；RAM/磁盘/空闲显存不足立即停止，不降低门禁。图表阶段首次选用的捆绑Python缺少matplotlib，已改用E盘已有项目绘图库，未安装新依赖、未重跑模型。

## 验收、资源和预算

- 相关八文件回归：**92 passed, 1 skipped, 3 warnings in 12.61s**。skip为旧Windows符号链接权限案例；warnings为小fixture全部缺失列的median计算，既有填0输出通过验证。CUDA前后向探针的conv/head梯度有限且非零。
- 九份权重每份完整重载25,098笔，预测最大差异0；另外直接用sklearn与独立Top-K排序复算六个指标，九组差异均0。
- 完整缓存、源归档、原计划、恢复登记、预测、权重、失败、资源和共享账本核验通过；新进程`--check`也通过。最终当前源码/输入513项复核通过，公开报告八项SHA均与真实证据一致，见[最终源核验](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/final-source-check.json)。外层访问0、test访问0、最终决策未冻结。
- 成功尝试每2秒观测RAM/磁盘：可用RAM最低4.240GiB，C/D/E剩余最低16.792/30.005/46.723GiB；两个失败的越界另记。训练进程累计working-set峰值最高1,888.71MiB；Torch allocated显存峰值最高111.864MiB。GPU空闲显存只在阶段边界观测，最低5,784MiB，不能用Torch计数代替整机显存峰值。
- GNN **11/30尝试，9完成、2失败**；scale/search/failure通道占3/6/2，仍有conditional9、confirmation9、failure1额度。真实GNN总耗时**6,400.69秒（1小时46分41秒）**，单次完整成功最长989.92秒。
- 12小时总预算余36,799.31秒；保护九次确认的32,400秒后，**开发训练余4,399.31秒，约73.32分钟**。条件通道有九个数量额度，不代表时间足够跑满九组；下一路线必须据此冻结少量实验。
- 整个5B共享账本20尝试，16完成/4失败：XGB8、MLP1、GNN11。XGB仍余3confirmation+1failure，MLP余2；原Agent生成130/144，本轮新增LLM/付费0。

训练源有三份513项冻结：v1原计划、v2有界读取、v3资源恢复，分别保留原源快照；未覆盖旧模型或旧5A证据。

| 绑定 | SHA256 |
|---|---|
| 源v1 | `2f8664bdca2f408a9f0ca46cfd2c97b220c1f51dcf7bd59530ec0b145f057b5a` |
| 源v2 | `939821a31e85277ebc64ab11620c8a47a93eb6b526d10f16998daebf61366ed4` |
| 源v3 | `8da2668070cb7ed7b2e03e5ebdaa82dc740135cd74eb35c9ac9e52fe24d688c5` |
| 原图计划 | `08318fe2ede1c592ecc1dd4538cdfc26d7a70732c3e164d038194aa7efdd24aa` |
| 当前候选选择 | `6d3245695915dd50535a0e570c2267a09f55b196343d4955c95be704808bdf61` |
| 完成产物清单 | `ed77647e1592d8c01b4bcc71a9b22f6b8c3c5ad67af3977eddb3518ba5e3606c` |
| 本次共享账本 | `12f0a258e41f7f406cceda68c7fbb584b6ac97959f699340f639aef1b2331316` |
| g3权重 | `f6c0709c06325176aa52c0cc5a5041846eeee284ff3ae033da713315a442c7ee` |

## 自己如何验证

在E盘PowerShell进入项目环境，读取C盘工作树的当前源码：

```powershell
Set-Location E:/GraphRisk-Agent
. ./scripts/enter_project_env.ps1
$source = 'C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent'
$env:PYTHONPATH = "$source/src"
$env:PYTHONDONTWRITEBYTECODE = '1'
uv run --offline --no-sync python "$source/scripts/run_stage09_graph_expansion.py" --check E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-expansion-v1
```

应返回`status: passed`、`completed_real_fits: 9`、`selected_candidate: g3`、`conditional_v2_triggered: true`、共享20尝试及外层/test访问0。此入口核验现有证据，不重新训练。它同时检查本开发路线尚未释放外层/最终决策；后续路线正式释放时，应扩展历史验收兼容，不重置预算。

私有原始结果：`E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-expansion-v1/`；图、缓存和权重：`E:/GraphRisk-Agent/artifacts/stage-09/route5b/20261009-graph-expansion-v1/`。g3权重为其`g3/model.pt`。独立复算/资源/预算证据在[run-summary.json](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/run-summary.json)、[figures.json](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/figures.json)、[新进程核验](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-graph-figures-v1/artifact-check.json)；92项测试证据在`20261009-graph-verification-pre-v4/verification.json`。

`--prepare`用于登记后构建新产物，`--run`使用共享预算执行未完成目标，`--check`只读。重新拟合需要先核对剩余预算与新登记，不能覆盖旧目录或创建空账本规避已耗时间。

## 后续与本人学习

`conditional_v2_triggered=true`，依据为当前最高内层AP0.179596低于宽表x3的0.726914。该记录只触发开发诊断，尚未训练融合、未证明子群增益、未做三折三种子、未校准风险概率；模型仍为实验候选。

下一路线4在剩余开发预算内，先登记去边/扰乱关系/历史数值等有限重训消融，决定是否补更丰富的合法输入；加输入时必须有匹配无图对照。异构升级仍以真实关系价值证据为前提。路线5负责融合与正式确认，路线6负责完整覆盖/服务；网站当前默认模型未切换。

LB3按[学习路线](../docs/plans/stage-09-route-5b-learning.md#lb3扩大数据为什么不一定有用150分钟)分五天、每天30分钟：先写预测，再读规模时间线、encoder、学习曲线、g6实际权重及对照边界，最后用自己的话说明为何进入条件增强。**工程完成不代签LB3学习完成。**
