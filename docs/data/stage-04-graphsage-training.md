# Stage 04 GraphSAGE 训练与 checkpoint 契约

> 状态：路线 3 的模型、训练和版本化入口已实现。
> 输入：[路线 2 图产物](stage-04-graph-artifacts.md)与 `configs/graph_ieee_cis.yaml`。
> 实现：[`model.py`](../../src/graphrisk/graph/model.py)、[`train_graphsage.py`](../../src/graphrisk/graph/train_graphsage.py)。

## 模型输入与输出

每个样本是一个以目标交易为根、只包含严格更早关系的局部图。`Data.x` 是训练集拟合的 28 维节点特征，`edge_index` 是双向消息边，`root_index` 指向当前交易。PyG `Batch` 合并局部图时自动偏移各样本根索引；两层 `SAGEConv` 生成节点表示后，仅取根节点表示进入线性分类头，输出**一个 logit/目标交易**。sigmoid 将 logit 映射到 0～1 的**未校准风险分数**，不能解释为已校准欺诈概率。

`is_fraud` 不在 `Data.x`、`edge_index` 或图 manifest 的特征清单中。训练时从与 manifest 哈希绑定的时间分割 Parquet 单独读取标签，先对 ID 与顺序逐一校验，再作为独立张量送入 `BCEWithLogitsLoss`。训练正例权重只由所选训练目标计算。训练前还检查 train/validation 标签与 canonical、全量特征来源一致；**测试标签的来源检查和读取都在最佳验证权重冻结之后**。

## 训练和版本选择

训练固定 Python、NumPy、PyTorch 随机种子，并设置 CUDA CuBLAS 确定性工作区。CPU 可独立运行；`auto` 在 CUDA 可用时选 GPU，否则记录回退原因。`cuda` 显式请求却不可用时失败。mini-batch 逐批从时序索引构图，不把 50,000 个 PyG 图对象全部缓存。当前参数位于图配置：两层、64 维隐藏层、dropout 0.20、学习率 0.001、batch 128、最多 20 轮、验证无提升 4 轮后早停。

每轮记录训练加权 BCE loss、验证 PR-AUC 与 ROC-AUC。仅当验证 PR-AUC **严格提升**时保存最佳权重；并列时保留较早 epoch。早停和超参数选择不看测试集。恢复最佳权重后才读取测试标签，测试集评估一次。当前路线只产出模型和总体指标；逐交易分数表、同 ID XGBoost 对比和融合留给路线 4。

## 版本化文件和加载门禁

运行：

```powershell
uv run --offline python scripts\train_graphsage.py --config configs\graph_ieee_cis.yaml
```

输出到被 Git 忽略的 `artifacts/stage-04/ieee-cis/models/graphsage-v1/`：

| 文件 | 内容 |
|---|---|
| `model.pt` | 最佳 epoch 的纯 `state_dict` 张量；保存为 CPU 权重以便跨设备加载 |
| `training-history.json` | 每轮 train loss、validation PR-AUC、validation ROC-AUC |
| `metadata.json` | 模型版本、数据源、图 manifest 哈希、特征清单、超参数、ID 规模、标签计数、最佳 epoch、设备/显存、验证与测试指标、文件 SHA-256 |

加载时先核验 checkpoint 与 metadata 文件、模型 SHA-256、数据源、期望模型版本、图 manifest 内容哈希和特征 schema，然后使用 `torch.load(..., map_location=明确设备, weights_only=True)` 并严格加载权重。缺失、损坏或不匹配时产生明确错误。真实训练证据见[路线 3 验证记录](../../reports/stage-04-route-3-training.md)。

面试复盘时应区分：训练 loss 衡量当前批次拟合；验证 PR-AUC 决定 checkpoint；测试 PR-AUC 只报告冻结模型在未参与选择的数据上的表现。验证分数高于测试分数并不自动证明泄漏，需结合时间漂移、实体冷启动和样本量在路线 4 分析。
