# Stage 04 总验收与复盘：GraphSAGE 与图风险调查

> 验收日期：2026-09-24
> 工程状态：PASS，路线 0～7 的必选门禁完成；个人学习自测仍按学习路线自行完成。

## 范围与可复现输入

本阶段使用 IEEE-CIS 的 canonical 交易和严格历史特征，按既有时间切分取各 split 的时间前缀：训练 50,000、验证 10,000、测试 10,000 笔。目标 ID 的选择只依赖时间和稳定的交易 ID 顺序，不依赖标签或预测结果。GraphSAGE 和受控 XGBoost 使用同一训练 ID、验证 ID 和测试 ID；Stage 02 全量训练的 `xgboost-v1` 只作为单独的调查服务模型，不参加本阶段的受控融合。

图 manifest 的逻辑 SHA-256 为 `8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80`。构建索引覆盖 511,959 笔交易；全局有 528,187 个节点、1,530,667 条交易—实体事实边，实体类型为 card、device、email domain 和 address。两层 fanout 为 `[10, 5]`；每个目标只看严格早于自身时间的交易。同时间交易视为同时发生，不能形成历史边。训练、验证、测试目标的时间范围分别为 2000-01-02～01-14、2000-04-30～05-03、2000-06-01～06-03；这些日期使用人工锚点，只能解释相对顺序。

| 目标集 | 正例 / 总数 | 正例率 | 至少一类历史被截断 | 有实体冷启动 |
|---|---:|---:|---:|---:|
| 训练 | 1,357 / 50,000 | 2.714% | 99.702% | 12.538% |
| 验证 | 375 / 10,000 | 3.750% | 100% | 0.980% |
| 测试 | 307 / 10,000 | 3.070% | 100% | 0.720% |

图规模与截断率来自当前 manifest 和 `same-id-v4/metadata.json`，不是全量 IEEE-CIS 模型的覆盖规模。图构建先后两次得到逐字节一致的 manifest；第二次文件 SHA-256 为 `d1a9d57661873194afa60df97055f2039f3b29379f41585b890085e4f3da99a9`。路线 1 的固定十笔交易图有 13 个节点、16 条逻辑边、32 条双向 PyG 消息边；未来交易、同时间交易、`unknown-*` 占位实体、标签与 `identity_key` 不改变较早目标图。参考构建器与时序索引结果一致。逐项证明见[图 schema](../docs/data/stage-04-graph-schema.md)、[路线 1 记录](stage-04-route-1-schema-time.md)和[路线 2 记录](stage-04-route-2-artifacts.md)。

## 环境、训练与模型对比

本机离线环境检查确认 Python 3.11.16、PyTorch 2.9.1+cu128、PyG 2.8.0.post1、CUDA 可用；CPU 和自动选择的 CUDA 均运行了最小 `SAGEConv`。正式训练设备是 NVIDIA GeForce RTX 2060。GraphSAGE 两层、隐藏维度 64、batch 128；验证 PR-AUC 选择第 2 轮权重，第 6 轮早停，训练 metadata 的计时为 331.71 秒。冻结权重在 CPU 上重新加载并推理成功；模型文件 51,355 字节。同 ID 比较中的验证/测试单目标图构建加推理 P95 分别约 2.24/1.86 毫秒，来自小样本测量路径；独立 CLI 冷启动还需约 24 秒校验产物并构建内存索引，不能把热推理值当作用户端延迟保证。

| 集合 | 模型 | PR-AUC | ROC-AUC | Precision@Top 1% |
|---|---|---:|---:|---:|
| 验证 | 受控 XGBoost | 0.213027 | 0.756863 | 0.36 |
| 验证 | GraphSAGE | 0.178343 | 0.766533 | 0.42 |
| 验证 | 融合 | 0.249762 | 0.776069 | 0.52 |
| 测试 | 受控 XGBoost | **0.129566** | 0.700534 | **0.28** |
| 测试 | GraphSAGE | 0.103733 | 0.714879 | 0.08 |
| 测试 | 融合 | 0.128175 | 0.718317 | 0.23 |

受控 XGBoost 的训练 ID SHA-256 与图训练 ID 完全一致；验证和测试的有序 ID SHA-256 分别为 `999236ec9ef3f82d8566b165818d53ebddc806543b8dbf8cca807bc1cf72123a`、`313d26ed7ec84e55668f8ad08723bd8c12c3d75f3ea52a19dd5b1f3806af52c4`，三个模型使用同一行顺序。融合公式是 `alpha × XGBoost + (1 − alpha) × GraphSAGE`；alpha 从固定五个候选中只按验证 PR-AUC 选择为 0.5，测试标签在选择后才评估。测试集融合 PR-AUC 略低于受控 XGBoost，**本阶段没有验证出融合优于表格模型的结论**。受控 XGBoost 的输入特征与图模型也不同，公平性限定为同训练样本预算和同评估 ID。详见[训练记录](stage-04-route-3-training.md)及[同 ID 比较](stage-04-route-4-comparison.md)。

## 真实调查、失败恢复与审计

路线 7 预先固定展示规则为**测试目标 ID 清单中的第一笔**，即 `3488959`。使用 `scripts/run_investigation.py` 和 `same-id-v4` 连续调查两次，五个工具均为 `ok`、报告均为 `completed`。目标 GraphSAGE 分数为 0.362866，受控融合分数为 0.221295；合格风险邻居为空，这是成功查询后的真实空结果，不是低风险结论。两份报告字节及 SHA-256 相同：`a7ffa071fc356f84a20d95c882b4d28ea4ec192ec2bd0a107d689a7efe27fa37`；审计调查 ID 和首事件时间不同。路线 6 预先固定的另一笔测试目标 `3489059` 返回三个历史风险邻居及共享实体路径，详情见[路线 6 记录](stage-04-route-6-workflow.md)。两笔目标均未按测试标签挑选。

失败恢复使用两个明确的输入：

1. 训练集首笔 `2987000`：图目标没有 held-out 分数，图工具返回 `unavailable/graph_prediction_unavailable`，报告为 `partial`、能力缺口 1 项；交易、历史特征、Stage 02 XGBoost 和直接关系四节仍为 `ok`。没有伪造训练集内图分数。
2. 测试集首笔 `3488959` 加上故意不存在的对比产物名 `stage04-route7-deliberately-missing`：图工具返回 `dependency_error/graph_artifact_unavailable`，报告为 `partial`、issue 1 项；其余四节仍为 `ok`。原有正式产物未修改。

路线 5/6 的测试还覆盖来源错配、模型版本错配、预测表损坏、空邻居、同时间/未来邻居排除、异常隔离和报告/审计分离。审计只记录工具、状态、模型版本、时间和耗时，不含标签或完整实体表。图分数和共享匿名实体只是调查线索，不能证明真实用户身份或确认欺诈。

## 验证命令与文件门禁

```powershell
uv run --offline python scripts/check_graph_environment.py --config configs/graph_ieee_cis.yaml
uv run --offline pytest tests/unit/test_temporal_graph_schema.py tests/unit/test_temporal_entity_index.py tests/integration/test_graph_artifacts.py tests/unit/test_graphsage_model.py tests/unit/test_graph_comparison.py tests/unit/test_graph_service_neighbors.py tests/integration/test_relation_tools.py tests/integration/test_investigation_workflow.py -q
uv run --offline pytest -q
uv lock --check --offline
uv build --offline
git diff --check
```

结果：环境门禁 PASS；聚焦测试 **54 passed**；全量测试 **166 passed**；锁文件核对 128 个包；sdist/wheel 构建成功；差异空白检查通过。构建时 uv 提示缓存目录位于源码目录内，因此额外检查了分发包内容：wheel 46 个成员、sdist 148 个成员，均未包含缓存、原始 CSV、图/模型产物、embedding 或生成报告；sdist 中仅含版本控制的 `data/raw/.gitkeep` 占位文件。

`git check-ignore -v` 确认 `data/raw/ieee-cis/train_transaction.csv`、graph manifest、GraphSAGE checkpoint、测试 embedding 和 `reports/generated/stage04-route7-report-a.json` 均被忽略；`git ls-files` 在这些受限目录下只显示公开的 `data/raw/.gitkeep`。演示 JSON、审计和本地实验仍留在忽略目录中。工作区另有用户修改的 `tests/test_smoke.py`，本阶段不修改或纳入提交。

## 计划偏差、限制与后续输入

- 按资源预算使用 50k/10k/10k 目标，没有扩展到 100k 或全量交易图；`[10, 5]` fanout 导致验证和测试目标均至少有一类实体历史被截断。加大 fanout 的收益尚未通过实验验证。
- GraphSAGE 与融合均未在未来测试集 PR-AUC 上超过受控 XGBoost。验证集的融合优势不能外推为测试期或生产效果；分数均未校准。
- IEEE-CIS 的实体是匿名启发式映射，时间为人工锚点。当前本地图索引适于受控演示，尚无在线更新、并发压力验证或图数据库部署。
- Stage 04 的五工具工作流仍是无 LLM 的确定性调查。Stage 05 接收版本化模型/图工具及其结构化失败语义，加入有出处的政策文档检索、引用约束、Agent 编排与人工审批；不能把模型线索写成欺诈事实。
- 本报告提供学习材料：`T3` 严格历史手算图、PyG 根索引说明、训练曲线、同 ID 对比、风险邻居来源、三类失败分支和下述面试讲解。学习路线中的“本人能独立解释”需要学习者亲自完成，工程测试无法代替该自测。

可用于 90 秒讲解的骨架：本项目从一笔交易出发，先读取严格历史特征和 XGBoost 分数，再用交易—实体图查询 GraphSAGE 风险邻居。每笔目标图只含更早关系，同时间和未来边都被测试排除；50k/10k/10k 的同 ID 对比中，验证集融合 PR-AUC 为 0.249762，但测试集仅 0.128175，低于受控 XGBoost 的 0.129566。图目标未覆盖或模型产物缺失时，调查保留其余证据并明确标为 partial。当前分数未校准、实体不是核验身份，下一阶段才加入可引用的政策 RAG 与 Agent。
