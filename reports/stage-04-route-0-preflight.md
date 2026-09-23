# Stage 04 路线 0：环境与实验契约验证记录

> 日期：2026-09-23
> 状态：PASS，路线 0 环境与实验契约门禁完成

## 目标与本次产物

路线 0 固定 Stage 04 图实验的输入来源、规模、输出位置、设备策略和工具错误语义。配置文件为 [`configs/graph_ieee_cis.yaml`](../configs/graph_ieee_cis.yaml)，校验实现为 [`src/graphrisk/graph/config.py`](../src/graphrisk/graph/config.py)。

默认设置：IEEE-CIS；两层 fanout 为 `[10, 5]`；训练、验证、测试最多分别 50,000、10,000、10,000 笔；各 split 按已生成的时间顺序选前 N 笔；随机种子 `20260921`；GraphSAGE 版本 `graphsage-v1`；batch size 128、hidden channels 64、最多 20 epoch、patience 4；设备 `auto`。这些是预算与实验契约，不代表 GraphSAGE 已训练，也不代表上述规模必然能在本机按时完成。

训练/验证/测试交易 ID 清单、预测表和 graph manifest 均写入 `artifacts/stage-04/ieee-cis/`，受 Git 忽略。配置校验拒绝输出路径越出 `artifacts/`、同一个输出目标被重复使用、非法 fanout、空模型版本、无效设备和交叉数据源。

## 输入同源门禁

加载 graph 配置时，必须同时读取 Stage 01 data config 与 Stage 02 baseline training config，并检查：

1. graph、data 和 baseline 的 `project_name` 与 `dataset_source` 相同；
2. Stage 01 `interim_features` 与 Stage 02 `serving_features` 指向同一解析后路径；
3. Stage 02 train/validation/test 特征都位于 Stage 01 的 processed 目录下。

环境 smoke 还检查七个已有产物非空：canonical transaction、全量 point-in-time features、train/validation/test 特征，以及 Stage 02 champion 的模型和 metadata。缺失时提示先重跑对应 Stage 01/02 管线。该检查只确认产物存在、非空与配置路径一致，不读取受限原始 CSV 或声称内容哈希已验证。

本机实际检查结果：transactions 22,799,673 bytes、all_features 25,579,037 bytes、train_features 18,030,024 bytes、validation_features 3,758,768 bytes、test_features 3,814,425 bytes、baseline champion model 2,075,021 bytes、metadata 1,638 bytes。七项均存在且非空。

这是一道**配置/路径绑定**门禁，不等于输入文件内容哈希验证。路线 2 的 graph manifest 还需记录实际输入文件哈希和 ID 集合哈希；路线 5 加载模型时还需核对 manifest。

## 评估集合与公平比较契约

- 每个 split 先依据 Stage 01 固定时间切分选定交易 ID，再按配置的 `chronological_prefix` 与数量上限固定子集。相同时间的排序必须按 transaction ID 稳定裁决；是否选定交易不依赖标签或模型分数。
- 实际 ID 清单及其顺序、数量、SHA-256、时间范围和正例数均入 manifest/报告。配置变更意味着新的实验版本，不覆盖旧结果。
- GraphSAGE 与**受控主对比 XGBoost**使用同一训练 ID，以及完全相同的验证和测试 ID；两者预处理都只在训练数据上拟合。
- Stage 02 既有全量训练 champion 可以在相同评估 ID 上列为单独参考，但其训练预算不同，不能与受控主对比混写。
- 验证集选择 epoch、超参数、融合权重与任何阈值；测试集在方案冻结后评估，不允许为提升结果修改 ID 集合。
- 如果最终只完成 50k 受控实验，报告必须注明规模，并与 Stage 02 全量指标分开呈现。

## 工具错误与输出契约

Stage 03 的 `find_risky_neighbors` 在真实图实现前继续返回 `unavailable / capability_unavailable`。Stage 04 预先固定新的结构化错误码：

| 情况 | 预定状态 / 错误码 | 解释 |
|---|---|---|
| 目标交易不存在 | `not_found / transaction_not_found` | 无调查对象 |
| 目标存在，但不在合格图预测覆盖集 | `unavailable / graph_prediction_unavailable` | 不能把无分数伪装成低风险 |
| 模型权重、图索引或 manifest 缺失/损坏 | `dependency_error / graph_artifact_unavailable` | 图依赖不可用，保留其他调查证据 |
| 图产物与请求或数据源不一致 | `dependency_error / data_source_mismatch` | 禁止跨来源混用 |
| 图能力尚未接入 | `unavailable / capability_unavailable` | Stage 03 既有占位行为 |
| 图查询成功但无合格历史邻居 | `ok`，空列表 | 这不是错误，也不表示目标低风险 |

现有 `RiskyNeighborsData.graph_model_version` 已可表明列表使用的模型版本。现有 `RiskyNeighbor` 缺少时间差和预测来源；路线 5 在成功工具实现前先写契约测试，再加 `seconds_before_target` 和 `score_provenance`（只允许 held-out 或正式推理分数）。训练样本的 in-sample 分数不可直接包装为独立邻居证据。路线 0 不提前改变成功 payload，因此 Stage 03 的占位输出仍保持兼容。

## 依赖、设备与来源

- Python：本机 3.11.16；
- GPU：NVIDIA GeForce RTX 2060，6,144 MiB；当前驱动 610.74；
- E 盘：安装前约 45GB 可用；
- 依赖锁：`torch==2.9.1+cu128` 来自 PyTorch CUDA 12.8 官方索引，`torch-geometric==2.8.0.post1` 来自 PyPI；`uv.lock` 解析为 128 个包；
- 只引入 torch 与 torch-geometric；PyG 基础功能不要求额外 `torch-scatter`/`torch-sparse` 扩展；
- `uv.toml` 的仓库内缓存配置移入 `pyproject.toml [tool.uv]`，避免 uv 忽略相邻的 PyTorch 索引设置。缓存仍在 `.uv-cache/`。

版本选择依据：

- [PyTorch Windows 安装说明](https://docs.pytorch.org/get-started/locally/)；
- [PyTorch 2.9.1 官方 CUDA 12.8 安装命令](https://pytorch.org/get-started/previous-versions/)；
- [PyG 安装与可选扩展说明](https://pytorch-geometric.readthedocs.io/en/latest/notes/installation.html)；
- [PyG 2.8.0.post1 的 PyPI 发行页](https://pypi.org/project/torch-geometric/)；
- [uv 的 PyTorch 索引配置说明](https://docs.astral.sh/uv/guides/integration/pytorch/)。

注意 PyG 官网 latest 文档展示了 2.9.0 内容，但 2026-09-23 的 PyPI 发行页最高为 2.8.0.post1。第一次 `uv lock` 因此拒绝不存在的 2.9.0；改为 2.8.0.post1 后成功。文档应以实际锁定/安装版本和 smoke 结果为准。

## 验证命令与结果

实际运行命令：

```powershell
uv sync --locked
uv run --offline python scripts/check_graph_environment.py --config configs/graph_ieee_cis.yaml
uv run --offline pytest tests/unit/test_graph_config.py tests/unit/test_graph_smoke.py tests/unit/test_investigation_contracts.py -q
uv run --offline pytest -q
uv lock --check --offline
```

实际结果：

| 门禁 | 结果 |
|---|---|
| `uv sync --locked` | PASS；安装 torch 2.9.1+cu128、PyG 2.8.0.post1 |
| 离线 graph environment smoke | PASS；Python 3.11.16，PyG 导入成功 |
| CPU `SAGEConv` | PASS；输出 shape `[3, 4]` 且值有限 |
| `auto` 设备选择 | 选择 `cuda`，同一 `SAGEConv` 输出 shape `[3, 4]` |
| `torch.cuda.is_available()` | `true` |
| 七项 Stage 01/02 输入产物 | 全部存在且非空 |
| 配置/设备/调查契约聚焦测试 | 45 passed in 9.46s |
| 全仓库回归 | 127 passed in 20.42s |
| `uv lock --check --offline` | PASS；128 packages |
| `uv build --offline` | PASS；sdist 与 wheel 均生成 |

测试命令中 `--basetemp=reports/generated/stage04-route0-...` 使 pytest 的临时目录留在仓库内受 Git 忽略的位置。设备 smoke 必须在 CPU 上实际运行 `SAGEConv`；`auto` 在本机选择 CUDA，其他机器 CUDA 不可用时使用 CPU。显式指定 `cuda` 且不可用时返回清楚的运行错误。

本次没有构图、训练、模型指标或风险邻居输出。Stage 03 的图工具仍按原契约返回 `unavailable / capability_unavailable`，Stage 04 总状态保持 `In Progress`。下一步路线 1 需要通过手算 fixture 与未来/同时间/标签泄漏测试。

## 学习复盘提示

本路线需要亲自解释：`torch.version.cuda` 是 wheel 内置 CUDA runtime 版本，不是驱动显示的最高 CUDA 版本；`cuda_available` 需要实际运行验证；锁文件固定依赖解析，但不能替代模型权重、特征 schema 和 graph manifest。PyG 导入成功也尚未证明时间语义或模型效果，那些属于后续路线的验收。
