# Stage 04 路线 3：GraphSAGE 训练与版本化验收

> 日期：2026-09-23
> 状态：PASS；GraphSAGE 训练、最佳权重选择、测试一次评估与 CPU 重新加载完成。Stage 04 整体仍为 In Progress。

## 实验契约

输入为路线 2 的 IEEE-CIS `graph-manifest.json`，内容 SHA-256 为 `8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80`。训练、验证、测试目标数分别为 50,000 / 10,000 / 10,000。训练集正例 1,357 / 50,000（2.714%），负例 48,643；据此得到 `pos_weight = 35.84598`。验证正例 375 / 10,000（3.75%），测试正例 307 / 10,000（3.07%）。标签由时间分割文件单独读取，不加入 PyG 节点特征；测试标签在最佳权重冻结后才做来源核对和读取。

模型为两层 GraphSAGE，输入 28 维、隐藏 64 维、dropout 0.20、Adam 学习率 0.001、batch 128，最多 20 轮、验证无提升耐心 4 轮。每个目标图按 manifest 的严格历史规则构建，根节点单独分类；训练 mini-batch 按 epoch 的确定性种子打乱。具体契约见[训练说明](../docs/data/stage-04-graphsage-training.md)。

## 正式运行结果

运行命令：

```powershell
uv run --offline python scripts\train_graphsage.py --config configs\graph_ieee_cis.yaml
```

| 项目 | 结果 |
|---|---:|
| 实际设备 | NVIDIA GeForce RTX 2060（CUDA） |
| CUDA 张量峰值分配 | 77.93 MiB；不等于进程显存总占用 |
| 训练循环耗时 | 340.67 秒 |
| 最佳 epoch / 停止 epoch | 2 / 6 |
| 验证 PR-AUC / ROC-AUC | 0.178343 / 0.766533 |
| 冻结模型测试 PR-AUC / ROC-AUC | 0.103733 / 0.714879 |
| 测试 Precision@Top 1% / Recall@Top 1% | 0.080 / 0.0261 |
| 模型权重文件 | `artifacts/stage-04/ieee-cis/models/graphsage-v1/model.pt`，51,355 字节 |

| epoch | 训练加权 BCE loss | 验证 PR-AUC | 验证 ROC-AUC |
|---:|---:|---:|---:|
| 1 | 1.25325 | 0.15745 | 0.77388 |
| **2** | **1.19974** | **0.17834** | **0.76653** |
| 3 | 1.17938 | 0.16220 | 0.76259 |
| 4 | 1.16631 | 0.15314 | 0.74286 |
| 5 | 1.15341 | 0.14408 | 0.73594 |
| 6 | 1.14448 | 0.17720 | 0.73251 |

第 2～5 轮训练 loss 持续下降、验证 PR-AUC 下滑；第 6 轮虽回升，但未超过第 2 轮，因而触发早停并恢复第 2 轮权重。验证与测试之间的差距需要在路线 4 结合时间段、冷启动与同 ID XGBoost 基线分析，不能凭一次结果断言图模型增益。当前 sigmoid 输出是未校准分数，不是欺诈概率。Stage 02 既有 XGBoost 使用不同训练预算，本报告不把两者当作公平主对比。

训练结果保存在被 Git 忽略的 `artifacts/stage-04/ieee-cis/models/graphsage-v1/`：`model.pt`、`metadata.json`、`training-history.json`。metadata 包含数据源、模型版本、manifest 哈希、特征 schema、训练规模、正例权重、设备与显存、训练时间、最佳轮数、验证与测试指标、权重及历史文件 SHA-256。真实 CUDA 权重通过 `map_location=cpu`、`weights_only=True` 重新加载，验证目标的 39 节点局部图 CPU 推理成功。

## 自动验证与学习复盘

```powershell
uv run --offline pytest tests\unit\test_graphsage_model.py tests\unit\test_graph_config.py tests\integration\test_graph_artifacts.py -q
uv run --offline pytest -q
```

两笔交易的极小 fixture 可以显著降低 loss，参数确实更新；单图与合并 batch 的根节点输出一致。合成数据端到端训练、重新加载和同种子重复运行通过；数据源、模型版本、图 manifest、特征维度、缺失或损坏 checkpoint 均有拒绝路径。CUDA 正式训练重复两次的每轮 loss 与验证指标一致；修正后的运行把测试标签来源核对放在选模之后。

聚焦模型、配置和小型端到端测试通过；全仓库 156 项测试通过。模型与测试在 PyTorch/PyG 锁定依赖下运行，后续代码变动需重新核对 checkpoint 的适用版本。

学习重点：`BCEWithLogitsLoss` 接收未 sigmoid 的 logit，正例权重只改变训练损失；PR-AUC 用于从多个 epoch 中选择模型；两层 GraphSAGE 的根表示只聚合严格历史局部图。训练 loss 下降并不保证较晚时间段的排序质量提升。下一步路线 4 才导出逐交易分数、完成同 ID 的 XGBoost/融合评估和误差分析。

后续自助实验可用 `--validation-only --run-name <名称>`，将候选写入独立目录并保持测试标签未读取。该模式通过合成源端到端测试；上表真实 IEEE-CIS 正式训练指标不因新增入口改变。操作步骤见[自助训练与调参指南](../docs/guides/graphsage-self-training.md)。
