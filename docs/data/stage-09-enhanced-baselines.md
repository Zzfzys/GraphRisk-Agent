# Stage09 5B-2：增强XGBoost与公平对照合同

本路线只做第一折开发比较，选择可重载的增强基线。外层三折ROC≥0.80、图增量与最终模型决策仍等待后续路线；不切换调查服务，不做付费或LLM调用。匿名字段的供应方生成时点限制沿用[特征合同](stage-09-model-enhancement-features.md)。

## 固定数据与七次训练

使用路线1侧表与预处理；第一折训练250,979笔、内层早停25,098笔。读取标签时按冻结的ID/time范围投影，只读这两段。calibration、outer及旧test不查询标签、不评分、不参与选型。预处理仍只拟合train，结果保存到E盘新目录。

| 固定顺序 | 模型/输入 | 额度 |
|---|---|---|
| 1 | 旧14个业务字段XGBoost，新训练期词表/填补 | XGB control 1 |
| 2 | core38字段XGBoost，实际63列含缺失标记 | XGB control 1 |
| 3 | GraphSAGE相同28维根输入XGBoost | XGB control 1 |
| 4 | 相同28维无图MLP | MLP control 1 |
| 5～7 | source_as_received宽表XGBoost，预定x1/x2/x3 | XGB search 3 |

XGB三次control用于第一折的输入诊断，不能补成三折质量成绩。剩余XGB confirmation 3与failure 3额度保留；MLP另两次control留给最终冻结后的后两折。GraphSAGE尚未训练，本路线根对照用于之后完整第一折允许训练集的公平比较；不能与更小50k/150k模型比较后归因为图结构。

修复路线0实现顺序错误：允许带固定control身份与附录哈希的fold-1对照在最终决策冻结前运行，后折仍须最终冻结；同族/同折/同输入不能换名字重复训练。没有增加额度，没有解锁outer/test，没有重建账本。旧源码与合同说明由已有路线1源码快照保留。

原14字段改用训练期缺失标记和原生类别处理；恰等于“unknown-实体类型-本交易ID”的人工占位键编码为缺失，不学习数十万个人工唯一字符串。此处理只用于模型缺失语义，不把未知交易合并为共享图关系。因而不完全复刻旧one-hot管线。旧Stage02全量champion只作历史参照，不在更早训练窗口加载并报告为公平对照。28维由本折训练期重新拟合GraphFeatureEncoder的等价数值过程产生：9数值+9缺失+5产品+5节点类型；与原实现逐元素一致测试。编码器、根矩阵顺序和ID哈希绑定供后续GNN使用。

## 固定训练、指标和选型

机器附录：[configs/stage09_tabular_baselines.json](../../configs/stage09_tabular_baselines.json)。只使用现有XGBoost3.2/PyTorch。CPU hist、4线程、max_bin256，分块4,096行建立QuantileDMatrix；输入压缩后保存在内存，不在Python拼接完整871列矩阵。原生Categorical保持训练词表，包括缺失与未知值。此路径与外存DMatrix不同，仍须测量整个进程内存。[官方说明](https://xgboost.readthedocs.io/en/release_3.2.0/tutorials/external_memory.html)支持迭代器构建压缩矩阵。

XGB固定2000轮上限、patience50，精确计算sklearn average_precision_score作为早停指标，选择最佳轮并保存最佳模型。记录train/inner的logloss与AP曲线。内层AP也用于x1/x2/x3选型；同AP先选择实际fit耗时低者，再按预登记顺序。ROC不用于二次挑选，不依据外层或test更换候选。XGB类别/抽样/正则参数及不加正类权重的决定在看结果前固定。

MLP沿用已有两层RootMLP：hidden64、dropout0.2、lr0.001、batch1024、最多50轮/patience10、按训练正类比例加权。它与树模型的容量/训练方法不同，同输入比较用于排查信息是否只来自根字段，不能单次开发差异证明架构优劣。

内层报告AP、ROC-AUC、正例率、Precision/Recall@Top1%/5%；取ceil比例笔数，并列按交易字符串ID升序。每个模型在全部25,098内层ID上保存预测，模型JSON/PT重新加载后逐ID预测差异≤1e-6。无外层成绩的0.80称开发观察，不能标正式基础门禁通过。

所有训练先在共享账本占用额度，包括失败与超时。每次60分钟，候选按固定順序，callback/每个MLP batch检查deadline与资源；准备/压缩/重载时间单独说明。RAM/磁盘每2秒采样，起始GPU检查保留，CPU训练不代表GPU路径通过。资源不足停止，不自动关闭应用、降门槛或退款。

## 文件与运行

新源码与附录先用前置登记入口冻结，再 `scripts/run_stage09_enhanced_baselines.py --prepare <唯一名> --registration <新登记目录> --features <路线1报告根>`。只准备合法数据、根编码器和计划，不训练。

`--run <已有准备目录>` 按计划训练，仅续接从未尝试的任务；完成的任务不重复fit。已有失败、无结果但已消耗额度、未结算占位均停止并要求审计，不能悄悄重训。成功后 `--check <报告目录>` 为只读：核对源归档、输入、模型/预测/曲线、资源和活跃共享账本。预算缺失不能自动重建。

`baseline-choice.json` 只冻结增强XGB基线选择，不调用共享账本的最终 `freeze_decision`，让路线3～4仍能按合同做GNN实验。全部七次结果、源快照、准备与训练资源、模型、预处理/编码器、训练曲线与重载误差保留；当前服务和旧结果不改。入口使用E盘现有uv/Python，C盘只存源码与说明。

工程测试、真实内层成绩、最终三折质量与LB2本人学习分别记录。实际目录、成绩、资源、未完成项和准确命令见路线2验收报告。

## 2026-10-09运行修复附录

四个对照完成后，x1在首轮boost前构建宽表矩阵时触发DuckDB的256MB内部限额。保留原失败/额度与原503输入冻结；修复仅把训练读取引擎限额改为768MB，不改变SQL时间边界、数据、标签、特征、模型参数和指标。系统RAM3GiB门禁不降低。

新源码冻结后可通过 `--run <原目录> --recovery-registration <新源码冻结>` 登记一次runtime修复，在同计划/同task输入SHA/同seed/fold下，用XGB failure lane恢复x1。源版本差异白名单仅限运行器/CLI/恢复测试/本附录；已完成四个模型不重训，不重新占用search位，不覆盖原失败记录。旧/新源码快照与恢复绑定均保留并进入最终只读核验。预算多消耗一次XGB失败额度，不能退回首次占位。第二次失败不自动再试，原始计划和基线选择规则保持。

## 2026-10-09主机睡眠中断附录

x1恢复与x2成功后，Windows因空闲睡眠约78分钟，x3恢复执行时触发单次60分钟deadline。Windows Power-Troubleshooter事件1与Kernel-Power事件42核对实际睡眠/唤醒；原失败耗时与采样空档保留，不扣除睡眠、不退款、不把无采样时段说成连续资源通过。

用 `--interruption-registration <新的源码冻结>` 仅登记该x3的同参数/同输入/同fold/seed一次恢复，消耗第二个XGB failure位。校验失败父任务、无保存模型、时间对应的Windows事件并绑定SHA；保留原计划、前六个完成模型、v1/v2快照，追加v3快照与host-resume-recovery.json，不能借恢复重选或增加search额度。此分支不恢复无关超时，第二次失败仍停止。

训练进程使用Windows SetThreadExecutionState的临时ES_SYSTEM_REQUIRED请求避免自动睡眠，finally释放；不阻止手动睡眠、不要求屏幕保持亮、不修改全局电源方案。单次60分钟和RAM3GiB门禁保持；起始/周期采样与恢复训练单独留存。最终完成时预计7个模型、9个真实数据尝试（8XGB含2失败、1MLP），后续XGB仍保留3confirmation+1failure、MLP2control、GNN30。
