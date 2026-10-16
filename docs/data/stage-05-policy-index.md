# Stage 05 路线 2：政策切分与检索契约

> 路线 2 建立可重建的本地检索能力；检索质量的冻结集评估属于路线 3。政策均为原创模拟材料，不代表真实企业规定。

## 输入、切分和引用

`scripts/build_policy_index.py` 先使用路线 1 的校验导入器。每个 `##` 章节下的**完整段落**成为一个 chunk；目前 8 份文档产生 16 个 chunk。超过 2,000 字的段落会报错，必须编辑源政策并保持完整条件，构建器不会自动截断。`chunk_id` 为文档 ID、版本、章节名、段落序号和段落正文 SHA-256 的规范 JSON 摘要前 24 位，加 `policy-` 前缀。正文或版本变更会产生新 ID。

索引 JSON 保留文档 ID、政策 ID、标题、版本、来源引用、许可、章节、段落序号、源文件行号、正文哈希、生效区间和冲突声明。`index_version` 是不含自身的规范 JSON 内容 SHA-256；读入时重新检查索引摘要、每段正文哈希、ID、顺序及向量维度和单位长度。篡改或损坏时检索返回 `index_unavailable`，不提供片段。

## 两种本地 backend

`tfidf` 使用现有 scikit-learn 的中文字符 2～4 gram TF-IDF；索引持久化为带版本和内容校验的 JSON，查询时只对**当前有效**的 chunk 重建稀疏矩阵，避免旧版影响词频/排名。小语料下这种设计简单、离线且确定性；语料变大时应改为预计算每个时间切片的索引。

`local_embedding` 使用本机 Ollama `/api/embed`；构建前核对 `/api/tags` 中的模型完整 digest，存储模型 ID/digest、维度、单位归一化向量和每段引用。查询也必须匹配相同模型 ID/digest 和维度，然后按余弦相似度排序。本阶段的 embedding 候选是 [Ollama 的 qwen3-embedding:0.6b](https://ollama.com/library/qwen3-embedding%3A0.6b)，约 639 MB，支持中文等多语言；只有本机下载、摘要、推理和资源验证通过后才在配置与路线报告中宣称真实 dense 可用。

`retrieve_policy(query, policy_as_of, top_k)` 先验证输入，再读完整性受检索引，按半开区间 `[effective_from, effective_to)` 过滤，最后评分和排序。并列分数以 `chunk_id` 升序稳定打破。返回 `status`、`error_code`、`index_version`、`backend`、`hits`；每个 hit 有独立分数和完整引用，内部向量不进入结果。`no_match` 的 `hits` 必为空，不会填充一条看似可信的政策。路线 3 在开发集加入文本证据重合门禁：词法候选须有 1～3 个不同字符 bigram 与查询重合；dense 候选余弦相似度须严格大于 0.45，低于 0.65 时也要求相同的文本重合。冻结集结果见[路线 3 验收](../../reports/stage-05-route-3-retrieval-evaluation.md)。这些门槛不是已校准正确率，词法与 embedding 相似度均不是风险概率或政策正确概率。

错误码包括 `empty_query`、`invalid_query`、`invalid_policy_date`、`invalid_top_k`、`index_unavailable`、`embedding_model_mismatch`、`embedding_dimension_mismatch`、`embedding_unavailable`；无候选使用 `no_active_policy` 或 `no_match`。当前索引日期独立于 IEEE-CIS 的交易时间。

## 本地复现

```powershell
uv run --offline python scripts/build_policy_index.py --config configs/stage05.yaml
uv run --offline python scripts/retrieve_policy.py "共享设备 人工复核" --policy-as-of 2026-07-01 --top-k 3
uv run --offline pytest tests/unit/test_stage05_policy_retrieval.py -q
```

两次构建同一来源应获得相同 `index_version` 和索引文件 SHA-256。可用 `Get-FileHash artifacts/stage-05/policy/policy-index-tfidf.json -Algorithm SHA256` 对照。索引属于本地生成产物，不提交 Git。

## Stage 07 pgvector 映射

路线 5 已基于同一 `local_embedding` 固定索引实现真库导入和显式 pgvector Retriever。表以 `(index_version, chunk_id)` 为主键，查询先用半开日期区间过滤，再按余弦距离及 `chunk_id` 排序；保留本节的结构化结果和无答案/错误门禁。迁移、权限、故障及本地 dense 对照见[路线 5 数据合同](stage-07-pgvector-retrieval.md)和[验收报告](../../reports/stage-07-route-5-pgvector.md)。默认仍为 TF-IDF。
