# Stage 04 路线 4：同 ID 模型比较契约

使用 `configs/graph_ieee_cis.yaml` 对应的已校验图 manifest 和冻结的 GraphSAGE checkpoint。运行：

```powershell
uv run --offline python scripts\compare_graphsage.py --config configs\graph_ieee_cis.yaml --run-name my-first-comparison
```

每次运行使用唯一的 `--run-name`，输出到 Git 忽略的 `artifacts/stage-04/ieee-cis/comparisons/<run-name>/`；已完成的同名运行直接拒绝覆盖。可以用 `--graph-checkpoint-dir` 指向同一图产物目录下经过哈希校验的 GraphSAGE 试验权重。

执行顺序：

1. 校验图 manifest、来源哈希、模型哈希、模型版本、特征 schema 和目标数。
2. 读取图的 train/validation 目标 ID，以一对一 inner join 按图 ID 顺序对齐表格特征；任何目标缺失、重复或非法标签直接失败。XGBoost 仅在这 50,000 个训练目标上重新拟合预处理器和模型。
3. 只在 10,000 个验证目标上计算两个模型的分数。固定候选 `alpha = 0, 0.25, 0.5, 0.75, 1`，以验证 PR-AUC（实现为 sklearn average precision）选择融合权重；并列时取较大的 alpha。
4. alpha 冻结后才验证并读取 10,000 个测试目标的标签，再对完全相同的测试 ID 评估 XGBoost、GraphSAGE 和融合。
5. 保存完整分数表、64 维根节点 embedding 表、三笔交易的前八维示例、受控 XGBoost、指标与哈希 metadata、Markdown 报告。

预测表包含 `transaction_id`、`split`、标签、三种分数、模型版本和 `embedding_file`。embedding 表以同一 `transaction_id`/split 作键；维度是机器表示，不是业务原因。metadata 记录训练/验证/测试 ID 的顺序 SHA-256、正例数、输入和输出哈希、模型大小、训练时长、构图耗时、批量与单样本延迟。冷启动比例指根交易**至少一类有效实体**在目标时刻之前未出现；截断比例指至少一类实体的历史交易超过 fanout 上限；零历史比例则表示根交易完全没有历史邻居。

主指标 PR-AUC；辅助指标 ROC-AUC、Precision/Recall/F1、Top 1% 和 Top 5% 的 precision/recall。Precision/Recall/F1 使用固定的 0.5 演示阈值；两个原始分数及其融合都未经校准，不可解释为欺诈概率。Stage 02 全量训练 champion 与此处同 50k 训练预算的 XGBoost 不是同一实验，不能放在主对比表中直接排名。

实际结果与限制见[路线 4 验收记录](../../reports/stage-04-route-4-comparison.md)。
