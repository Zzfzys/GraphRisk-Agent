# Stage09 路线2：GraphSAGE 实现、梯度与两跳传播审计

> 2026-10-07；工程审计通过，L2本人练习待完成。
> [实现路线](../docs/plans/stage-09-implementation-roadmap.md) · [学习路线](../docs/plans/stage-09-learning-roadmap.md) · [手算讲义](../docs/data/stage-09-graphsage-audit.md)
> 本轮只有微型CPU fixture训练与冻结权重推理；没有真实IEEE训练、调参、LLM调用或付费。

## 结论

当前模型确实是能利用历史信息的两层GraphSAGE，不是只预测根自身特征的空壳。手算传播、梯度/参数更新、小图过拟合、单图/batch、train/eval、最佳权重恢复和历史时间边界都有证据；真实冻结权重在20个验证目标上均对历史特征产生响应。

**发现并修复输入校验缺口**：原模型只确认batch有N个根，却没有确认第i个根属于第i个子图；错误根、跨图消息边和单图多个根可能被静默接收。新增归属/隔离/单根检查，并为边越界或非long类型提供明确错误。7个失败回归先复现，再通过修复。有效图的层、权重、激活、dropout、classifier、特征和采样算法都没有改变。

20笔路线1冻结的真实验证图，旧/新实现logit最大差异**0**；与冻结预测分数最大差异**5.960464477539063e-8**，单图/batch logit最大差异**1.1920928955078125e-7**。这些图的根归属/隔离本来正确，因此不能把本次校验修复当作旧GNN低PR-AUC的原因，也不需因该修复重新训练旧权重。

**尚未证明参数充分、训练最优或图能赢XGBoost**。下一路线3审计采样与同特征公平基线，路线4做关系/特征消融、有限调参和多种子；当前低分仍是需要诊断的真实负结果。

## 实际实现和版本

| 项目 | 当前确认 |
|---|---|
| 输入/隐藏 | 28 / 64 |
| 卷积 | SAGEConv两层，mean聚合、self分支，root_weight=True，normalize=False |
| 消息方向 | PyG source_to_target；ego导出补双向边，历史交易→实体→根可两跳传播 |
| 每层处理 | conv→ReLU→dropout；dropout0.2 |
| 分类 | 取每张图根embedding，Linear输出一个logit；forward不做sigmoid |
| 训练损失 | BCEWithLogitsLoss，float32目标，pos_weight=48,643/1,357=35.84598378776713 |
| 训练更新 | Adam，zero_grad→forward→backward→step；shuffle仅改变已固定train目标和同步labels的batch顺序 |
| 验证 | eval + no_grad；对logit做sigmoid后计算项目pr_auc（实际为average_precision_score/AP） |
| 早停 | 按validation AP，best_state.detach().cpu().clone()；恢复最佳state再评估/保存 |
| 当前历史metadata | best epoch6、stopped26、patience20；不与早期模型记录混合 |
| 加载 | 绑定dataset/manifest/feature names/model SHA，weights_only、map_location，strict state load，eval |
| 标签 | 图特征、ego构造、PyG Data无y；训练labels另按固定ID顺序加载 |
| 时间特征 | canonical写UTC无时区时间戳；节点导入恢复UTC语义，每节点自身历史窗口，不按目标时刻回填 |

依赖行为还核对了当前安装的torch_geometric SAGEConv/MessagePassing源码，不只凭类名判断默认聚合或方向。

权重SHA256保持b1e4696dfca80c617ddeadf2fb2ae310b39a6e445fe9127865ea56901d26e69f；
图manifest内容SHA256保持8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80。

模型源码：
- 修复前470db8e6002327934c0c7de4019549b3f99bd12cf0f7c594310d114dbb93394e；
- 修复后17fdd1eda40dff8ef1271a489eb7e9efabcc54bfc7884a15b8094875378456b0。

旧源码保留在reports/generated/stage09/route2/20261007-audit-v1/model-before-route2.py，审计先核验它与路线1清单一致，再加载相同冻结权重比较。旧30笔Agent输出/清单不覆盖，GraphSAGE权重版本仍是graphsage-v1；源码校验修订单独记录。

路线1的163文件冻结现在会**明确失败一项model.py源码漂移**，这是已登记修复，不更新旧哈希掩盖变化；其它冻结输入全部一致。路线2另建165文件inputs-after-audit.json，内容SHA256：
3b4cc7eb0a0e205250f0160314d3b357a881214e74523bc2fe01be6809bd2e2f。
今后调用旧基线check会因这项源码变更失败；查看保存输出不需重跑LLM，新方案在后续路线另冻新版本。

## 可重复的正确性证据

| 检查 | 实际结果 | 能说明什么 |
|---|---|---|
| 非零固定self/neighbor/classifier权重手算 | 根logit3；两跳历史+2后4；四跳外+20后仍3 | 两层传播范围与mean聚合正确 |
| 小图输入autograd | 对两跳历史导数0.5，对四跳外0 | 可导路径与感受野正确 |
| 各层梯度和更新 | conv1、conv2、classifier有有限梯度/参数变化 | loss能更新各模块；不要求所有参数梯度都非零 |
| 仅历史不同、根相同的两图过拟合 | 160步fixture后BCE<0.08，负/正分数<0.1/>0.9 | 模型可以利用历史区分微型样例，不能当真实分类成绩 |
| batch隔离 | 改第二张图历史不影响第一张；根等于ptr偏移 | 正常batch没有串图 |
| 错误batch | 错根/根乱序/重复根/跨图边被拒绝 | 已修复输入契约缺口 |
| 原生_batches | shuffle目标与labels同步，末尾小batch，Data.y=None | 监督顺序匹配、不把label塞入图 |
| 原生_evaluate | hook确认eval/no_grad，dropout关闭，sigmoid结果一致 | 验证模式正确 |
| 原生早停恢复 | 脚本化fixture AP0.4→0.8→0.3→0.2，patience2停第4，恢复第2权重 | 保存的是clone的最佳权重而非最后epoch；脚本化AP不是质量成绩 |
| validation-only循环 | fixture禁止读取test label/provenance，调用未触及test标签 | 候选路径能仅用train/validation |
| 既有时间/标签边界 | 未来、同时间、未知占位、乱序、label/identity变化不影响较早图；encoder只fit train | 小fixture上的边界正确 |
| 真实验证图 | 20独立目标全部合法根/独立batch；旧新输出差异0 | 当前基线正常输入未受校验缺口影响 |
| 真实历史节点自身截止 | 独立canonical严格较早自连接重算295个历史节点、每个8统计字段；最大差异0 | 所抽真实历史节点没有按目标时刻回填统计 |
| 真实冻结权重历史梯度 | 20/20有非零历史数值梯度；各例最大梯度的最小值0.0276688 | 当前权重实际使用历史信息 |
| 冻结权重历史金额敏感性 | 每个历史节点的标准化金额+.25，20/20根logit变化，最大绝对变化0.1504298 | 局部数值敏感性；不是重训消融或业务因果证明 |

四跳外探针是对通用前向的扩展链测试，当前ego builder不声称采了四跳交易。真实特征扰动也不是有效业务交易重建，不能据此计算PR-AUC提升。历史统计复算不读取欺诈标签，不用标签给解释评分。

## 缺陷、未验证与风险

已修复的是**异常输入校验**。没发现这20笔正常图发生根错位、跨图边、历史特征回填，也没发现两层前向/梯度/最佳权重保存断链。有限审计不是对全项目“无bug”的证明。

仍需路线3～4验证：全部train/validation覆盖与度分布、启发式实体/大邮箱域关系质量、fanout实际容量、各模型同特征公平性、关系/特征消融、训练不足/过拟合/时间迁移、不同种子和候选调参。当前pr_auc采用AP，后续比较必须同公式，不能混入梯形PR面积。

真实训练预算/deadline尚未接入旧train_graphsage全数据入口；本轮没有启动该入口跑IEEE。后续正式训练必须先接入预注册13次/单次1h/总6h停止线与账本，不能以本次fixture成功声称已有训练预算保障。CPU检查不证明CUDA bitwise确定性、GPU梯度一致或延迟；新增guard可能增加CUDA同步检查成本，后续性能测量明确版本。

路线1至少10例人类语义复核/L1仍待完成，用户授权推进路线2按“基线输入/真实调用/失败归因完整即可诊断图”的门禁执行，没有代签人工复核。本轮也不解决LLM raw uncertainty20/30错误或政策多样性不足。

## 文件、测试和复现

- [模型校验](../src/graphrisk/graph/model.py)；
- [审计模块](../src/graphrisk/graph/stage09_audit.py)与[CLI](../scripts/audit_stage09_graphsage.py)；
- [20项新增测试](../tests/unit/test_stage09_graphsage_audit.py)；
- 本机忽略目录：reports/generated/stage09/route2/20261007-audit-v1/；
- audit.json：手算、20真实图、295历史节点、版本差异；
- checkpoint-sensitivity.json + probe_checkpoint_sensitivity.py：真实冻结权重局部响应；
- before-fix.txt：7 failed/8 passed；after-fix.txt：首15项+旧模型4项，19 passed；
- focused-tests.txt：追加早停fixture后的42 passed；
- focused-tests-verified.txt：最终新增20项通过；
- full-regression.txt：首次514 passed/1 failed/18 skipped；人工Parquet fixture带时区，与项目canonical UTC无时区格式不同；
- full-regression-verified.txt：修正fixture后515 passed/18 skipped/1 warning，44.05s；0 failed；
- Docker沿路线1保持停止，本轮不依赖数据库；18项缺隔离DSN的skipped不是数据库验收成功。

先复核已有证据和新冻结（不训练/不调用LLM）：

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest tests/unit/test_stage09_graphsage_audit.py tests/unit/test_graphsage_model.py tests/unit/test_temporal_graph_schema.py tests/unit/test_temporal_entity_index.py tests/unit/test_temporal_features.py tests/integration/test_graph_artifacts.py -q
uv run --offline python scripts/freeze_stage09.py --check reports/generated/stage09/route2/20261007-audit-v1/inputs-after-audit.json
~~~

重新计算真实图审计时使用新目录，保留原audit.json：

~~~powershell
uv run --offline python scripts/audit_stage09_graphsage.py --output-dir reports/generated/stage09/route2/<新的唯一目录>
~~~

该CLI只从验证期选20笔冻结图目标，只投影validation预测分数，不读test标签、重新构图产物或训练。校验前后来源假定仅已登记的model.py变更；后续代码再变化会停止，需写新审计合同，不放宽检查静默补哈希。

本轮主真实图审计约11.089s，所有新增文件/微型模型/缓存留E盘。真实训练次数、LLM/embedding/付费新增均0；微型fixture训练另记，不混入真实13-run预算或求职模型成绩。

## 验收与后续

- [x] 两跳传播、超感受野不变、可导路径、各层参数更新与微型过拟合通过。
- [x] 单图/batch、正确根与隔离；校验缺口先失败复现再最小修复。
- [x] 原生训练/验证模式、logit/BCE、早停最佳clone与加载核验。
- [x] 时间/标签/占位/乱序回归；20真实图与295历史节点复算证据。
- [x] 修复前后真实有效图输出不变、旧基线/权重/清单保留，新源码冻结。
- [ ] L2本人完成手算、运行测试和口述解释；独立于工程完工。
- [ ] 参数/采样/特征对效果的诊断，属于下一路线3～4。

路线2工程Done。下一步路线3“采样、时间边界与同特征基线”，先建立公平对照，再正式做消融/调参；Stage09整体仍In Progress。
