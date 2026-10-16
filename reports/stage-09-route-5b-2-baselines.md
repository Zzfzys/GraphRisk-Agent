# Stage09 路线5B-2：增强XGBoost与公平对照验收

**工程完成，2026-10-09；LB2本人学习待完成，Stage09仍In Progress。** 七个模型全部完成并保存，按预定内层AP规则选择宽表x3：AP **0.726914**、ROC-AUC **0.934365**。最终回归90 passed、1 skipped；文件/输入/预算只读核验通过。基础质量的三折确认和GraphSAGE增量判断尚未完成。

用简单的话说：本路线给树模型补齐信息，再让它和“只看GraphSAGE根节点信息”的两个模型在同一批交易上比较。这样下一步检查图模型时，有一个足够强的参照，也能分清改进来自新增字段还是图关系。

## 已实现的比较条件

第一折训练250,979笔，含8,174笔欺诈（约3.257%）；随后内层早停25,098笔，含1,061笔欺诈（4.2274%）。七个模型使用完全相同的训练/评估ID和时间截止，seed为20260921。预处理只在训练期拟合；标签只读train与early-stop。校准、外层三折与旧test尚未评分。

| 对照/候选 | 输入 | 用途 |
|---|---|---|
| legacy14 XGBoost | 14个旧业务字段，经数值缺失标记/原生类别处理为23列 | 简化业务参照，重新训练；不是旧Stage02权重复现 |
| core XGBoost | 38个字段，含缺失标记63列 | 来源可用性有明确假设的输入对照 |
| root28 XGBoost | 与本折GraphSAGE根节点相同的28维 | 检查根输入本身的信息量 |
| root28 MLP | 同28维，hidden64、dropout0.2 | 无关系的神经网络对照 |
| x1 | source_as_received 871列；depth6/lr0.05/min_child1 | 固定候选1 |
| x2 | 同871列；depth8/lr0.05/min_child5 | 固定候选2 |
| x3 | 同871列；depth6/lr0.025/min_child5 | 固定候选3 |

XGBoost固定CPU hist、4线程、max_bin256、最多2000轮、patience50，使用精确sklearn AP早停并保存最佳轮。三个业务/根输入树对照使用x1参数。MLP最多50轮、patience10，按训练正类比例加权。不同模型的容量和损失处理不同，同输入诊断不等于架构优劣的最终结论。

旧14字段现在使用训练期词表和原生类别；严格匹配本交易人工unknown占位键的类别转成模型缺失值，不学习海量人工唯一字符串，也不把这些缺失交易合并成共享图关系。旧one-hot管线和50k历史结果只能作历史参照，不能直接相减。

871列包含417个数值缺失标记，不是871个独立业务信息。C/D/V/id等供应方匿名字段的生成时点仍未知；source_as_received成绩不能当成已证明严格在线可用的成绩。详见[特征合同](../docs/data/stage-09-model-enhancement-features.md)与[本轮训练合同](../docs/data/stage-09-enhanced-baselines.md)。

## 完整内层成绩

AP是average_precision_score，ROC-AUC是排序指标，不是分类准确率。Top1%选251笔、Top5%选1,255笔；分数相同按字符串交易ID升序。

| 模型 | AP | ROC-AUC | Precision@1% | Recall@1% | Precision@5% | Recall@5% |
|---|---:|---:|---:|---:|---:|---:|
| legacy14 XGB | 0.447768 | 0.843083 | 0.868526 | 0.205467 | 0.396813 | 0.469369 |
| core XGB | 0.499383 | 0.866039 | 0.888446 | 0.210179 | 0.437450 | 0.517436 |
| root28 XGB | 0.164530 | 0.780715 | 0.294821 | 0.069746 | 0.203187 | 0.240339 |
| root28 MLP | 0.150673 | 0.754283 | 0.298805 | 0.070688 | 0.180876 | 0.213949 |
| x1 宽表XGB | 0.707178 | 0.924941 | 0.988048 | 0.233742 | 0.571315 | 0.675778 |
| x2 宽表XGB | 0.726767 | 0.933926 | 0.988048 | 0.233742 | 0.589641 | 0.697455 |
| **x3 宽表XGB，入选** | **0.726914** | **0.934365** | **0.992032** | **0.234684** | **0.582470** | **0.688973** |

core与x1保持相同树参数，AP差约0.208，显示新增字段在这个开发窗口内有明显作用。root28对照的信息范围更窄，其低分不能直接归因为GraphSAGE实现有错，也不能说明关系没有价值；本路线没有训练新的GNN。

三个候选全部完成后，按预定“内层AP优先→耗时→登记顺序”选择x3；不重新按ROC或Top-K挑选。x3比x2的AP只高0.000147，不能据此宣称稳定优势。x2的Top5%召回反而更高（0.697455 vs 0.688973），完整保留这一差异。

在这个内层窗口，x3把251笔排在最前面的交易中249笔欺诈找出来：人工核查精度约99.20%，但只覆盖全部1,061笔欺诈的约23.47%；查前5%时找到731笔，覆盖约68.90%。这说明“前排很准”和“找全欺诈”是不同要求；并非模型整体准确率99.2%。

内层ROC超过0.80或0.90只是开发观察，正式三时间折基础质量门禁等待路线5。尚未验证概率校准、未来时间稳定性或生产效果；原竞赛隐藏测试与此窗口不同，不能直接互相排名。

## 最佳轮次、重载与资源

| 模型 | 保存的最佳轮/epoch | 实际观测轮/epoch | 完成尝试耗时，秒 |
|---|---:|---:|---:|
| legacy14 XGB | 728 | 778 | 106.41 |
| core XGB | 557 | 607 | 119.97 |
| root28 XGB | 244 | 294 | 21.28 |
| root28 MLP | 42 | 50 | 54.65 |
| x1 | 329 | 379 | 296.24 |
| x2 | 508 | 558 | 180.49 |
| x3 | 1,153 | 1,203 | 531.58 |

XGB保存的树数等于最佳轮数；x3额外观察50轮未更好才停止，加载后保留1,153轮。七个模型都在完整25,098个内层ID上重新加载权重并预测，最大绝对差均为0（门槛1e-6）。选中JSON约56.18MiB，权重/词表仅留E盘忽略目录。

耗时包括各任务实际矩阵准备、训练、保存、预测和重载，不是纯boost时间；账本还计入结果封存/结束开销。x2复用同进程压缩矩阵，x1/x3恢复进程须重新构建，因此此表不能作为参数速度的严格公平比较。全局数据准备另耗5.18秒，准备进程峰值965.76MiB；内存迭代器压缩仍需RAM，不是外存训练。

准备5次、三段训练共679次资源采样：观测的最低可用RAM4.1405GiB，最低C/D/E余量18.3374/30.0052/49.2773GiB，都高于门禁。x3恢复进程峰值工作集2,748.33MiB（约2.68GiB）；前两段进程未封存同口径peak，不能把此数称为全部进程的最高峰值。睡眠空档另行记录，采样值不是连续每一毫秒的证明。

## 两次实际失败与恢复

1. **x1读取内存限额。** 首轮构建宽表矩阵时，DuckDB内部256MB限额触发OutOfMemoryException，尚未训练树。保留失败与额度，源码v2把读取限额改为768MB，再用failure位恢复相同x1。数据、SQL时间范围、参数、选择规则和系统RAM3GiB门禁不变；四个已完成对照不重训。
2. **x3主机自动睡眠。** Windows事件确认睡眠从13:44:36至15:02:29，约4,672.82秒。恢复执行后触发60分钟deadline，失败账本记约4,735.60秒，不扣除睡眠或退款。保留采样空档，不声称无采样期间资源始终通过。源码v3绑定对应睡眠事件与原失败，使用第二个failure位恢复原x3；前六个模型保留。

恢复训练使用进程范围的临时Windows防自动睡眠请求，结束/异常均释放；屏幕可熄灭，不修改全局电源计划，也不阻止手动睡眠。两次恢复均先测试、再登记新源码快照和原计划绑定；旧v1/v2源代码、原失败文件和同一共享账本保留。

共完成**7个模型、9个真实数据尝试**：XGB 8次（6完成、2失败），MLP 1次完成。XGB已用3control+3search+2failure，余3confirmation+1failure；MLP余2control；新GNN0/30，12h额度不动。账本XGB累计5,993.12秒、MLP54.67秒，含已超时的睡眠等待；不把9次都称为成功训练。新增LLM/付费0，旧生成130/144不变，外层/test访问0，最终GNN决策未冻结。

## 验证证据

最终冻结源码下，表格/恢复/原特征/时间/适配器/共享预算回归：**90 passed、1 skipped，14.03秒，0 errors**。其中本路线新增15项测试；另75项既有通过项和1项旧Windows符号链接fixture。跳过原因是OS不允许创建符号链接，与数据库或真实模型评估无关。

除哈希检查外，独立核验脚本重新读取七份落盘预测，确认各25,098个ID与内层标签逐项一致，重算AP/ROC/Top-K，差异全部0。再从train与inner各抽30笔真实交易，以原GraphFeatureEncoder逐节点编码，与保存的28维根矩阵比较，1,680个分量差异0。小fixture还检验训练期编码器拟合等价；真实60笔抽查不冒充全量逐行证明。9个新增/相关Python文件AST通过。

验证前后 `--check` 都返回passed、x3、7tasks、9shared_fits、outer/test0、decision_frozen=false，同一closure SHA。归档源码v1/v2/v3、真实旧输入、侧表、模型/预处理/预测/曲线、失败/恢复/资源记录进入只读检查；预算缺失不会自动重建。

本次没有运行数据库服务器、Docker/网页或真实Agent整体回归；调查接口/旧权重保持原绑定。工程机制fixture成绩与上述真实数据质量成绩分开，不能把本次90项测试称作整个Stage09验收。

## 实现、证据和后续

实现入口：[冻结配置](../configs/stage09_tabular_baselines.json)、[数据与根向量](../src/graphrisk/models/enhanced_data.py)、[精确早停/指标](../src/graphrisk/models/enhanced_training.py)、[执行/恢复/核验](../src/graphrisk/models/enhanced_execution.py)、[CLI](../scripts/run_stage09_enhanced_baselines.py)。新增行为有数据错配、未来标签、等价根输入、AP选型、类别重载、预算与恢复、防睡眠释放测试。

私有数据、权重、预测、源快照与完整日志留E盘：

- 运行报告：`E:\GraphRisk-Agent\reports\generated\stage09\route5b\20261009-tabular-v1\`。
- 模型/准备产物：`E:\GraphRisk-Agent\artifacts\stage-09\route5b\20261009-tabular-v1\`。
- 源登记：同报告父目录下的`20261009-tabular-source-v1/v2/v3`；原计划SHA为`016d86cd112caac8c4381afced56fe40da985bef73010a2279326baa5ac3d467`。
- 共享账本：`artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json`；不会因新目录或新源码版本重建额度。

| 绑定 | SHA-256 |
|---|---|
| 源v1，503输入 | `8409e8708116957d820129e5ea6fc1e36852932ba2bd5ce0563f005d16e7fa22` |
| 源v2，504输入 | `2af2000bf9f6b7b3990d01488f1fb102b4eb1aeaac094ea2506c07f59e22559a` |
| 源v3，504输入 | `6472e25258acb7d509bb410bf35e935dff69e82d57641972ec0065352f168b04` |
| x3模型 | `991e2d60d9fb69bfc9c8ce4f58526f129c51fab6970ed4975e647e8b2ee144b9` |
| 基线选择内容 | `5aba0142bf0af234e9e4ac140c24bf0bd279b985e62c4a84653862b4cdfdfa0b` |
| 完成封存内容 | `f45109852b9add9b5354c6653e03487fd221f56a7be3368e7b0a3e1fb0fedb2a` |

独立回归/实物核验保存在`reports/generated/stage09/route5b/20261009-tabular-verification-v1/{pytest.txt,verification.json,verify_materialized.py}`。三次控制台日志在其父目录`20261009-tabular-training-console-v1/v2/v3.txt`；首轮失败和睡眠中断日志不覆盖。全量预测不提交，C盘只编辑源码/文档。

## 自己怎样验证与学习

新的PowerShell执行只读核验，不需要再次训练：

```powershell
cd E:\GraphRisk-Agent
. .\scripts\enter_project_env.ps1
$env:PYTHONPATH = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
uv run --offline --no-sync python C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent/scripts/run_stage09_enhanced_baselines.py --execution-root E:/GraphRisk-Agent --check E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-tabular-v1
```

应看到`status: passed`、`baseline_candidate: x3`、`tasks: 7`、`shared_fits_used: 9`、`outer_access: 0`、`test_access: 0`、`decision_frozen: false`。这里冻结的是表格基线选择，最终GNN/融合决定仍开放给路线3～4。

再查看单个模型的结果，不展开全部训练历史：

```powershell
$taskResult = Get-Content E:\GraphRisk-Agent\reports\generated\stage09\route5b\20261009-tabular-v1\x3.json -Raw | ConvertFrom-Json
$taskResult.metrics
$taskResult.training | Select-Object best_round, rounds_observed, saved_rounds
```

本次完整回归的准确十文件命令在私有`verification.json.command`；自己重跑请换一个唯一的E盘basetemp。公开辅助入口`verify_stage09_enhanced_baselines.py --run <上述报告根> --name <新验证名>`只包含九文件，未包含恢复测试；再单独运行`tests/unit/test_stage09_tabular_recovery.py`的8项测试才覆盖本次全部范围。测试仅用小fixture验证机制，不新增真实IEEE fit。原始训练候选槽已消耗，不能为“复现”换目录偷偷重训或退款。

已生成并目视核对两张学习图：

- [训练与早停曲线](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-tabular-figures-v1/train_inner_curves.png)：蓝色train AP持续上升，橙色inner后期变平；虚线1,153轮是保存权重。logloss更低不直接替换AP早停规则。
- [同集PR与ROC曲线](E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-tabular-figures-v1/ranking_curves.png)：对照宽表、core和根输入；PR灰虚线是本窗口4.2274%正例率。曲线来自保存的真实内层预测，未用outer/test。

按[LB2四次30分钟练习](../docs/plans/stage-09-route-5b-learning.md#lb2什么叫强基线120分钟)完成：看输入表→手算Top-K→读最佳/停止轮次→执行只读核验并解释限制。工程完成不能代签本人学习。

下一步可以进入**5B路线3：当前同构GraphSAGE扩量与有限调参**，使用登记的训练窗口/编码器与公平根对照。50k/150k规模实验不能拿完整250,979笔根模型的差异全部归因图关系。三折确认、条件v2、融合晋级/退出、全时间覆盖及服务切换等待对应路线，不自动启动新的GNN fit。
