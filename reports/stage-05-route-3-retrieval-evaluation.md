# Stage 05 路线 3 验收：政策检索评估

> 执行日期：2026-09-25；路线状态：**Done**。Stage 05 整体仍为 `In Progress`；检索结果不是 Agent 政策结论的语义核验。

## 评估设计与冻结规则

- [查询集 v1](../data/evaluation/stage05_policy_queries_v1.yaml)在运行检索前依据 16 个源政策片段人工标注。共 30 条，开发集和冻结集各 15 条，分别有 11 条可回答、4 条无答案；覆盖新旧版本、冲突、同义改写、图/速度/审批证据和语料外问题。每条可回答问题标注精确 `chunk_id`；无答案问题标注拒答原因。版本案例另列禁止引用的旧版 ID。
- 查询集原始字节 SHA-256 为 `ae0e508957ab4c3bed6cfa20069ecb56d7ce8180f8e1735f2efa8354c782ae83`，绑定语料 SHA-256 `5944138255497bb595d46513a7100045363d1f35d99fd2607954567e64145513`。加载器拒绝未知 ID、过期的期望片段、错误日期、重复 ID 和语料变更。冻结集在开发集门禁确定后各后端只运行一次，没有用冻结结果调参或改标签。
- 评估脚本固定取前 5 个候选。Recall@K 是每条可回答问题的期望片段在前 K 位的召回比例，再对问题取平均；MRR 使用第一个期望片段的倒数名次。无答案误召回率是无答案问题中返回至少一个片段的比例；旧版误引率在带 `version` 标签的问题上计算禁止或失效片段进入候选的比例。延迟是本机单次检索调用的墙钟时间，p95 用最近秩定义。错误请求单独计数，不当作正确拒答。

## 开发集诊断与唯一调整

首次 TF-IDF 开发集可回答 Recall@3 为 1.0，但 D12 差旅报销与 D14 身份证 OCR 两条无答案查询因“政策”“身份”“阈值”等少量通用字串出现误召回，无答案误召回率 0.50。加入与候选片段至少 1～3 个不同字符 bigram 重合的门禁（需求随查询长度上限为 3）后，两条均拒答，可回答案例仍全部在前 3 位。

首次 dense 开发集可回答 Recall@3 为 1.0，但 D14 的最高余弦分数 0.575 被 0.45 门槛误收。对低于 0.65 的 dense 候选也应用同一文本证据重合门禁；达到 0.65 的高相似度候选保留语义改写召回。调整后开发集 4 条无答案均拒答，可回答 Recall@3 不变。门槛仅由开发集确定，并非普遍有效的置信度校准；没有在冻结集后继续调参。

## 冻结结果

| 后端 / 划分 | Recall@1 | Recall@3 | Recall@5 | MRR | 无答案误召回 | 旧版误引 | 错误数 | 延迟 p50 / p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TF-IDF / 开发 | 1.000 | 1.000 | 1.000 | 1.000 | 0/4 | 0 | 0 | 6.9 / 30.5 ms |
| Dense / 开发 | 0.818 | 1.000 | 1.000 | 0.894 | 0/4 | 0 | 0 | 212.4 / 298.7 ms |
| TF-IDF / 冻结 | 0.727 | **1.000** | 1.000 | 0.848 | **0/4** | **0** | 0 | 6.6 / 26.6 ms |
| Dense / 冻结 | 0.909 | **1.000** | 1.000 | 0.955 | **0/4** | **0** | 0 | 216.0 / 295.2 ms |

冻结集每后端有 11 条可回答问题：两种实现的 11 条都在前三位找到精确标注片段，超过 Recall@3 ≥0.80 门禁。F03 询问旧版 8 账户门槛，TF-IDF 把替代规则排在第 3 位（MRR 1/3），dense 排在第 1 位；F06 冲突问题两者均把精确冲突段落排第 2 位。F07 速度异常的同义提问，TF-IDF 第 2 位、dense 第 1 位。所有版本案例的禁止旧版片段均未进入返回集合；F15 的查询日期早于所有政策，两种后端均为 `no_active_policy`。

两份索引仍对应路线 2 的版本：TF-IDF `9eade9b4c7f38df917267731ebe1d19a0aa8c270cbe1b8234a64d64b1605b3e7`；dense `e5daa423b9370888f82f92ee9250e73fecc0a401500464b63d991d66373530bd`。Dense 运行核对本地 `qwen3-embedding:0.6b` digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`，向量为 1024 维。首次 dense 尝试因 Ollama 服务未运行返回 `embedding_unavailable`，不计入上述模型结果；服务和本地模型恢复后，两组正式运行均无错误。原始逐题分数、ID、状态、延迟保存在 Git 忽略的 `reports/generated/stage05/policy-eval-*-{development,frozen}.json`。

## 结论与边界

保留 `configs/stage05.yaml` 的 TF-IDF 为默认离线后端，`configs/stage05_dense.yaml` 作为可选候选：两者在这组小规模模拟政策上的 Recall@3 都满足门禁，dense 的前一位排序更好，但中位检索延迟约为词法的 33 倍且依赖本地模型服务。30 条问题来自 8 份短模拟政策，不能推断真实企业规则、复杂长文档或未知问法的表现；尤其 0/4 无答案误召回只说明本冻结样本通过。下一路线的 Agent 仍必须校验引用 ID、版本和结论是否由原文支持，不能把“检索命中”当作“政策结论成立”。

## 复现与学习

```powershell
uv run --offline python scripts/build_policy_index.py --config configs/stage05.yaml
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage05.yaml --split development
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage05.yaml --split frozen
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage05_dense.yaml --split development
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage05_dense.yaml --split frozen
uv run --offline pytest tests/unit/test_stage05_policy_evaluation.py tests/unit/test_stage05_policy_retrieval.py -q
uv run --offline pytest -q
```

Dense 命令前需要本机 Ollama 服务和配置中 digest 对应的模型，`OLLAMA_MODELS` 可指向仓库内被 Git 忽略的 `artifacts/stage-05/ollama-models`。**不要为了改阈值重跑冻结集**；后续若语料或评估设计改变，应发布新版本查询集并说明差异。个人 L3 练习：F03 的 TF-IDF 相关片段在第 3 位，因此 Recall@1=0、Recall@3=1、MRR=1/3；F06 的 dense 相关片段在第 2 位，因此相应值为 0、1、1/2。解释为什么这仍不能保证生成报告的每句话有原文依据。

验证记录：聚焦路线 2/3 单元测试 9 项通过；全量 `uv run --offline pytest -q` **209 passed**，`uv lock --check --offline` 和 `uv build --offline` 均通过。构建产物检查没有模型、生成评估报告、环境或 uv 缓存。工作区中用户先前修改的 `configs/graph_ieee_cis.yaml`、`tests/test_smoke.py` 保持原状；路线 2 为适配可变训练轮数而修正的图配置测试仍通过。
