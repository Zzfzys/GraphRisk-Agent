# Stage 04 路线 4：同 ID 比较与融合验收

> 日期：2026-09-23。状态：PASS；路线 4 完成，Stage 04 整体仍在进行。
> 命令：`uv run --offline python scripts\compare_graphsage.py --config configs\graph_ieee_cis.yaml --run-name same-id-v4`。
> 机器可读证据：`artifacts/stage-04/ieee-cis/comparisons/same-id-v4/metadata.json`、`report.md`、验证/测试预测及 embedding Parquet、`embedding-sample.json`、受控 XGBoost 模型；这些产物被 Git 忽略。

结论：验证集上融合 PR-AUC 0.249762 高于同预算 XGBoost 的 0.213027 和 GraphSAGE 的 0.178343；**测试集上融合 0.128175 略低于 XGBoost 0.129566**，没有证据证明当前融合改善未来时间段的 PR-AUC。测试 Top 1% 精度中，XGBoost 0.28、GraphSAGE 0.08、融合 0.23。保留失败结果，不依据测试集重新选择 alpha。GraphSAGE 验证 PR-AUC 较弱、测试进一步下降，可能与特征覆盖、时间漂移或每实体最多 5 笔历史的截断有关；单次实验不能确定因果。

受控 XGBoost 只使用 GraphSAGE 的 50,000 个训练目标；Stage 02 已有 XGBoost 使用更大训练集合，所以未混入下表。两者虽然训练 ID 一致，但输入特征表示不同，解释时仍需区分“同样本预算”和“同特征预算”。

Dataset: `ieee_cis`; graph manifest SHA-256: `8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80`.
Graph checkpoint SHA-256: `d7198494cebeb487f87a8f8df67dc7ae46fa811b101bdb5cbce8dabf8c6bf114`. XGBoost trained on exactly the graph train IDs.

| Split | Count | Positive | Ordered ID SHA-256 | Zero-history | Cold-start | Truncated |
|---|---:|---:|---|---:|---:|---:|
| train | 50000 | 1357 | `312605fb6857158a12063602dc9299b1790cf0c7a565468d07e60ae684e85307` | 0.0006 | 0.1254 | 0.9970 |
| validation | 10000 | 375 | `999236ec9ef3f82d8566b165818d53ebddc806543b8dbf8cca807bc1cf72123a` | 0.0000 | 0.0098 | 1.0000 |
| test | 10000 | 307 | `313d26ed7ec84e55668f8ad08723bd8c12c3d75f3ea52a19dd5b1f3806af52c4` | 0.0000 | 0.0072 | 1.0000 |

Fusion: `alpha * XGBoost + (1 - alpha) * GraphSAGE`; validation-selected alpha = **0.5**.
Candidate alphas and validation PR-AUC: 0.0: 0.178343, 0.25: 0.231787, 0.5: 0.249762, 0.75: 0.233770, 1.0: 0.213027.

| Split | Model | PR-AUC | ROC-AUC | Precision | Recall | F1 | P@1% | R@1% | P@5% | R@5% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| validation | xgboost | 0.213027 | 0.756863 | 0.103369 | 0.597333 | 0.176239 | 0.360000 | 0.096000 | 0.284000 | 0.378667 |
| validation | graphsage | 0.178343 | 0.766533 | 0.090941 | 0.682667 | 0.160502 | 0.420000 | 0.112000 | 0.244000 | 0.325333 |
| validation | fusion | 0.249762 | 0.776069 | 0.100840 | 0.640000 | 0.174229 | 0.520000 | 0.138667 | 0.278000 | 0.370667 |
| test | xgboost | 0.129566 | 0.700534 | 0.077162 | 0.485342 | 0.133155 | 0.280000 | 0.091205 | 0.202000 | 0.328990 |
| test | graphsage | 0.103733 | 0.714879 | 0.067637 | 0.560261 | 0.120702 | 0.080000 | 0.026059 | 0.186000 | 0.302932 |
| test | fusion | 0.128175 | 0.718317 | 0.079578 | 0.540717 | 0.138738 | 0.230000 | 0.074919 | 0.212000 | 0.345277 |

XGBoost training: 2.86s; model size: 341738 bytes. GraphSAGE training: 331.71s; model size: 51355 bytes.
验证集构图 6.67 秒、图批量推理 0.41 秒，测试集构图 6.47 秒、图批量推理 0.40 秒；图单样本含构图 P95 约 2.10/2.97 毫秒，XGBoost 单行含预处理 P95 约 105/110 毫秒。XGBoost 的整批推理分别为 0.15/0.12 秒。单样本基于前 20 笔，设备和测量路径不同，不能把这些数字当作一般部署延迟保证。模型大小见上文，Parquet 与 embedding 大小及 SHA-256 见 metadata。

冷启动指任一有效根实体此前未出现；train/validation/test 比例约为 12.54%/0.98%/0.72%。验证和测试均有 100% 目标至少一类关系被 fanout 截断，说明当前局部图采样丢弃了较多历史候选，但不直接证明加大 fanout 就会改善指标。64 维 embedding 以同交易 ID 保存在独立 Parquet；`embedding-sample.json` 仅展示三笔验证交易的前八维，不将任一维命名为业务解释。工程定义、复跑方法见[比较契约](../docs/data/stage-04-model-comparison.md)。
Decision metrics use a fixed 0.5 threshold for illustration; scores and their fusion are uncalibrated, not fraud probabilities.
Cold-start means at least one valid root entity has no strictly earlier occurrence; truncation means at least one root entity exceeds the fanout cap.
All model and alpha selection used training/validation only; test labels were accessed after alpha was frozen.
Stage 02 full-train XGBoost is not a row in this controlled comparison.

验收：真实 IEEE-CIS 50k/10k/10k 运行成功；三组测试指标由同一有序 ID 表产生；GraphSAGE 测试 PR-AUC 与路线 3 冻结 checkpoint 复算值一致。集成测试覆盖一对一 ID 对齐、缺失/重复拒绝、测试标签在 alpha 选定后才读取、embedding 与预测行键对齐、相同运行名防覆盖。全仓库 `uv run --offline pytest -q` 为 **158 passed**；`uv build --offline` 与 Python 编译检查通过。
