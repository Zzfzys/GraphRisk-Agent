# Stage09 路线3：采样、时间边界与同特征基线

日期：2026-10-07。工程实测见[验收报告](../../reports/stage-09-route-3-sampling-baselines.md)；L3 本人学习待完成，建议两次各30分钟。

## 这次实现了什么

入口是 `scripts/run_stage09_root_baselines.py`，实现位于 `src/graphrisk/graph/stage09_baselines.py`。prepare 先核验路线2冻结清单，加载已哈希绑定的旧图/encoder/目标列表，按训练/验证顺序生成同一根特征矩阵，分别读取标签；统计两个历史上限，创建独立候选 manifest，冻结参数和矩阵。train 验证冻结，先持久化预算预约，再各训练一次 XGBoost 与 MLP；选 MLP 的最佳验证 AP 权重，保存模型、预测与分层结果。check 只重载保存的模型核验评分。

新文件均在 E 盘项目内。共享的旧数据、图、encoder、checkpoint 和 Agent 证据没有被覆盖。本路线新增真实训练是 XGBoost 与 MLP，GNN 新增训练0次，LLM/付费调用0次。

## 30分钟练习一：亲手追踪采样

先读 `src/graphrisk/graph/temporal_index.py` 的 `build`，再读 `schema.py` 的 `assemble_ego_graph` 和 `to_pyg`。

假设目标 T0 在12:00，共享实体的交易依次为 A 11:00、B 11:30、C 12:00、D 12:01。先自己写出合法历史：B、A；C 同时发生，D 属于未来，均排除。如果 A 同时共享卡片和设备，入选图只有一个 A 节点。每条“交易→实体”关系在 PyG 中补反向边，因此报告里的 directed_pyg_edges 是关系数的两倍。

`[10,5]` 中，第一项只约束 schema 至少容纳四种根实体；构建器没有标准 NeighborLoader 的逐层随机采样。实际最多四类根实体，每类取五笔严格更早交易，去重后历史最多20笔。历史交易自己的其他有效实体也会入图，所以总节点数不等于1+4+20。上限10时历史最多40笔，但共享实体会使实际数量更少。未知 `unknown-*` 不连边。

运行这两个小型测试，再核对自己的答案：

```powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest tests/unit/test_stage09_root_baselines.py -q -k "history_cap or unknown_entities"
```

从本机私有目录查看 `sampling-cap5-validation.parquet` 和 `sampling-cap10-validation.parquet`，按 transaction_id 一对一核对 history/nodes/directed_pyg_edges。原始 ID 和实体值不放进公开笔记。当前验证图平均历史13.9557→27.9106，但“有历史”的比例一直是100%；增加上限改善容量，未改善已经饱和的非空覆盖率。

区分三个概念：

| 概念 | 含义 |
|---|---|
| 有历史 | 至少一笔合法更早交易 |
| 冷启动目标 | 至少一种有效根实体没有更早记录；其他实体仍可能有历史 |
| 服务覆盖 | 目标是否在模型允许评分的固定 ID 清单；清单外不等于不存在历史 |

路线1的20覆盖/10不覆盖是服务资格口径；本路线10k验证图100%有历史是局部拓扑口径，分母和含义都不同。

## 30分钟练习二：解释公平比较

先看 `GraphFeatureEncoder.fit/transform_node` 与 `root_matrix`。同根特征28维是9项数值、9项缺失指示、5项 product_code one-hot、5项节点类型。交易根的类型 one-hot 固定为 transaction。没有把 card/device/email/address 原始 ID 编成根特征。

数值中位数、均值/标准差、product 词表只用50k训练目标拟合；验证的未知 product 不扩词表。根特征 XGBoost 与 MLP 接收完全同一份 float32 矩阵；GraphSAGE 除这份根向量还看到历史节点与关系。相同 ID 只是必要条件，不能保证特征公平。

| 控制项 | 本路线处理 |
|---|---|
| 目标和顺序 | 同50k train、同10k validation，两个清单有哈希 |
| 根特征/缺失处理 | 复用并重拟合核验 train-only encoder，矩阵/列有哈希 |
| 标签/类别权重 | 1357/50000训练正例；验证375/10000；正例权重35.845984 |
| 神经训练 | 同seed、hidden64、dropout0.2、lr0.001；MLP最多50轮/patience10 |
| 架构/上下文 | MLP是根向量两层网络；GNN是两层SAGEConv，有邻居和边，参数量不相同 |
| 历史GNN预算 | 原 checkpoint patience20，最佳第6轮；与本次MLP并非严格等预算 |
| 业务模型 | 旧50k XGBoost额外含实体类别；Stage02全量模型另表 |

问自己：GraphSAGE AP0.170545超过MLP0.160028，能说“图结构使AP提升0.010517”吗？还不能。两者架构/参数量、训练历史与早停预算不同；早期验证半集 GNN 还略低于 MLP，ROC-AUC 也没有胜出。路线4要重训完整/去边/合法扰乱/去历史特征/扩大采样，并统一预算再解释。

验证上下文允许使用已经发生的、更早的验证事件，且不读取其标签。历史特征保持每个节点自己时刻的截止，不能用目标时刻重算历史节点。这里模拟持续到达的在线事件；如果改成“验证全批次只能查训练期”，是另一套实验，必须换合同。相同时间不能用 ID 当先后顺序。

## 自己验收，避免重复训练

```powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest tests/unit/test_stage09_root_baselines.py tests/unit/test_stage09_graphsage_audit.py -q
uv run --offline python scripts/check_stage09_root_baselines.py --output-dir reports/generated/stage09/route3/20261007-baselines-v1
```

check 会核对保存模型 SHA、矩阵/ID/标签绑定、预算账本以及重载后10k验证分数；不会调用LLM、训练或读测试标签。不要重复执行 `--train` 来验收：XGBoost/MLP各一次的额度已用完；失败预约也不退还。要改变实验预算，需要先更新合同和计划，保留旧证据。

按日期/预测/读过代码/命令与版本/结果/我的解释六栏记录：一个合法历史例子、一张公平对照表、三个不能由当前结果推导出的结论。测试通过不等于本人完成L3。

## 路线4的明确交接

扩大采样配置是 `configs/graph_stage09_history10.yaml`，绑定新的 `history-cap-10/graph-manifest.json`。目标/encoder复制后字节一致，大型不可变的无标签 Parquet 共享原路径和SHA；不存在偷偷复用cap5图的问题。训练和服务都通过 manifest 的 fanout[1] 构图。候选尚未重训或用于服务。

旧 checkpoint 不能当作 cap10 的新模型加载：权重绑定旧 manifest，直接更换输入图只能做敏感性测量。路线4先接入13次GNN/单次1h/总6h预算和停止线，再全10k前向复现原checkpoint，随后正式重训与消融。既有 `compare_graphsage.py` 会读取测试标签，不能用于候选搜索；本路线入口与核验均只用 train/validation 标签。历史测试已观察，最终也不能称全新盲测。

