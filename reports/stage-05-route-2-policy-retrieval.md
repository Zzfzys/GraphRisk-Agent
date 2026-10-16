# Stage 05 路线 2 验收：切分、索引与检索

> 执行日期：2026-09-24；路线状态：**Done**。Stage 05 整体仍为 `In Progress`；检索质量的冻结集验收属于路线 3。

## 产物与契约

- 从 8 份原创模拟政策生成 16 个完整段落 chunk。稳定 ID 绑定文档/版本/章节/位置/正文哈希；出处可定位到源文件行号。索引 JSON 包含语料哈希和自身 `index_version`，加载时复验内容、顺序与向量结构。
- `PolicyRetriever.retrieve_policy(query, policy_as_of, top_k)` 返回片段、分数、出处、版本、有效区间、索引版本和结构化错误。先过滤半开生效区间，再评分；并列按稳定 ID 排序。`no_match` 的 `hits` 为空，检索输出不包含内部向量。
- 本地 `tfidf` 保持默认、完全离线；独立的 `configs/stage05_dense.yaml` 固定 Ollama embedding 候选，不改变默认词法配置。pgvector 字段与查询门禁映射见[索引契约](../docs/data/stage-05-policy-index.md)，实现留待 Stage 07。

## 可复现记录

| 后端 | 两次构建的 `index_version` | 两次文件 SHA-256 | 本机结果 |
|---|---|---|---|
| TF-IDF | `9eade9b4c7f38df917267731ebe1d19a0aa8c270cbe1b8234a64d64b1605b3e7` | `1b685e4984c4e21da9ffc1e00a726e58e81e521e6044d26b57b7eb3379ec9818` | 16 chunk，相同字节与 ID 顺序 |
| 本地 embedding | `e5daa423b9370888f82f92ee9250e73fecc0a401500464b63d991d66373530bd` | `2615900c257e73909b137ba6f5a9ffc79cf2632c2caa3f78929186bf9f9ae6f3` | 16 chunk、1024 维、单位归一化；两次构建字节一致 |

真实 embedding 候选是 [Ollama 官方模型页](https://ollama.com/library/qwen3-embedding%3A0.6b)的 `qwen3-embedding:0.6b`；[Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)标为 Apache-2.0。本机 `/api/tags` 完整 digest 为 `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`，本地模型大小 639,150,858 字节。`ollama show` 报 595.78M 参数、Q8_0、32,768 上下文和 1,024 维；[官方 `/api/embed` 文档](https://docs.ollama.com/api/embed)支持批量输入及 `truncate=false`，实现选择超长时报错，不静默截断。`ollama ps` 在查询时显示 100% GPU、约 2.4 GB 模型占用；RTX 2060 6 GB 同时抽样为 2,567 MiB 已用、3,389 MiB 可用。这是抽样值，不是峰值。首次真实查询约 14.69 秒；后续热查询成功，但未做延迟分布统计。API 费用为 0 元。

查询 `共享设备 人工复核` 在 `policy_as_of=2026-07-01` 下，dense 前三名依次包含“可信设备例外”片段、“共享设备复核”新版片段、例外冲突片段；旧版未返回。词法同一日期也只返回有效版本。`zzzxxyyqv` 在词法与 dense 均返回 `no_match` 和空证据；dense 的临时相似度门槛 0.45 也使“员工差旅报销政策”返回空证据。**这三例不是质量评估**：阈值可能误拒同义改写，也可能放进其他无答案问题。路线 3 必须冻结至少 24 个案例，报告 Recall@1/3/5、MRR、无答案误召回、旧版误引和延迟，才能决定默认后端/阈值。

结构化失败测试覆盖空查询、非法日期、非法 `top_k`、无命中、过期版本、损坏索引、模型 digest 不符、维度错配及本地 provider 异常。测试使用确定性 fake embedding；真实本地模型另以上述 smoke 验证，二者没有混用为同一质量证据。

## 复现命令和检查

```powershell
uv lock --check --offline
uv run --offline python scripts/build_policy_index.py --config configs/stage05.yaml
uv run --offline python scripts/build_policy_index.py --config configs/stage05_dense.yaml
uv run --offline python scripts/retrieve_policy.py "共享设备 人工复核" --policy-as-of 2026-07-01 --config configs/stage05.yaml
uv run --offline python scripts/retrieve_policy.py "共享设备 人工复核" --policy-as-of 2026-07-01 --config configs/stage05_dense.yaml
uv run --offline pytest tests/unit/test_stage05_policy_retrieval.py tests/unit/test_stage05_config.py -q
uv run --offline pytest -q
uv build --offline
```

聚焦检索/配置测试 **24 passed**；全量回归 **206 passed**；锁文件核对及 sdist/wheel 构建成功。打包检查无原始 CSV、模型、缓存或生成报告；sdist 仍只含既有的 `data/raw/.gitkeep`。全量测试初次有一项图配置旧断言失败：用户把 `max_epochs` 调到 50 后，测试仍固定把 patience 21 当非法。现已将该断言改为“当前 `max_epochs + 1`”，保留用户的训练配置和 `tests/test_smoke.py` 未提交修改；重跑 206 项通过。

本路线只证明检索接口、版本/时间门禁和本地模型运行。检索分数不代表政策结论可信，路线 3 需测量误召回；路线 4～8 才接入 Agent、引用核验和审批。
