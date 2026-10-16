# Stage09 路线2：GraphSAGE两跳、梯度和训练手算讲义

> L2材料就绪，本人练习待完成；建议两次30分钟。
> [审计结果](../../reports/stage-09-route-2-graphsage-audit.md) · [测试](../../tests/unit/test_stage09_graphsage_audit.py)

## 先分清三件事

“网络能前向运行”“历史信息能参与预测”“真实泛化效果好”是不同问题。路线2验证前两者；路线3～4检查采样、特征、训练参数与公平效果。微型过拟合不是IEEE测试成绩。

当前输入28维、两层SAGEConv/hidden64，每层ReLU+dropout0.2，根embedding经过Linear成为logit。实体数值特征是0但有类型特征，邻居聚合可把交易信息传给实体，再传回根。GraphSAGE使用mean邻居分支和self分支，不需要把ID本身嵌入向量才能中转信息。

## 第一次30分钟：自己算两层

先画双向链：

~~~mermaid
graph LR
  T0[根交易 T0] <--> E0[实体 E0]
  E0 <--> T1[历史交易 T1]
  T1 <--> E1[实体 E1]
  E1 <--> T2[四跳外交易 T2]
~~~

此链用于测试通用两层前向，不代表当前ego sampler真的扩展到四跳。PyG每条source→target边沿方向送消息；只有T0→E0→T1而没有反向边，历史消息到不了T0。

设每个节点只有一个数值，x=[1,0,3,0,5]；self与neighbor权重均1、bias0、dropout0、classifier1。每层计算：

h_new(v)=ReLU(h_old(v)+mean(h_old(u), u为v的入邻居))

| 节点 | x | 第一层h1 |
|---|---:|---:|
| T0 | 1 | 1+0=1 |
| E0 | 0 | (1+3)/2=2 |
| T1 | 3 | (0+0)/2=3 |
| E1 | 0 | (3+5)/2=4 |
| T2 | 5 | 5+0=5 |

T0第二层=1+2=3，即根logit3。先遮住答案预测：
- T1从3变5，E0第一层变3，根logit变4。
- T2从5变25，E1第一层改变，但根第二层只读自身/E0第一层，因此仍3。
- 根对T1的导数0.5，对T2的导数0；这里中间激活非零，避免ReLU把合法路径切断造成误判。

亲手运行test_hand_calculated_two_hop_history_and_four_hop_exclusion和test_autograd_follows_legal_receptive_field。真实冻结权重20/20历史梯度非零说明这些样本用了历史信息，不能推出历史信息一定提高准确率。

## 第二次30分钟：batch、更新、时间

PyG把两张5节点图拼接，root_index应为[0,5]（ptr[:-1]），不是[0,0]或[0,1]。仅有两个根还不够：各根必须属于对应图，消息边不能跨图。新校验拒绝错误归属/跨图边；原生builder本来生成正确batch，20笔真实图修复前后logit差异0。

训练流程：model.train→zero_grad→logits→BCEWithLogitsLoss→backward→optimizer.step。不要先sigmoid再塞给BCEWithLogitsLoss；该损失内部处理logit。pos_weight35.846强调正类loss，不是校准真实概率。验证eval+no_grad关闭dropout和梯度，再sigmoid转分数。

best_state必须detach/cpu/clone保存，不能直接让它引用后续仍更新的参数。新增原生循环fixture设验证AP0.4/0.8/0.3/0.2，patience2停epoch4，但保存/恢复epoch2的state。这个AP序列是用来验证控制逻辑的脚本化指标，不是模型成绩。候选验证专用路径不读取test label。

历史节点T1的24h统计应截止T1自己的时间；不能因T1出现在T0的图中就截止到T0重新计算。审计从canonical独立按“同identity_key、严格较早”自连接复算295历史节点，每节点8项统计全一致；identity_key仍是启发式。canonical时间列是UTC无时区表示，GraphTransaction恢复UTC语义，别把宿主本地时区混入手写Parquet。

当前项目pr_auc=average_precision_score（AP），不等同所有论文的梯形PR曲线面积。后续模型比较先统一公式和数据ID。

## 自己填写的学习记录

- 日期/预计结果：________
- T1变化如何影响T0？手算图与结果：________
- 四跳外不变证明了什么，没证明什么？________
- 为什么batch根数量正确仍可能错？________
- 哪个参数发生更新？某个bias梯度为0是否必然是bug？________
- 能过拟合两个样例为什么还可能PR-AUC低？________
- 295历史节点用哪个时间截止，为什么不看欺诈标签？________
- 旧基线check现在只报model.py漂移，应怎样核对新旧版本？________
- 我的不同意见/仍不懂的点：________

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest tests/unit/test_stage09_graphsage_audit.py -q
uv run --offline python scripts/freeze_stage09.py --check reports/generated/stage09/route2/20261007-audit-v1/inputs-after-audit.json
~~~

工程审计完成不代替你完成L2。下一路线3先看真实图采样与同特征基线，路线4才做正式消融和调参。

