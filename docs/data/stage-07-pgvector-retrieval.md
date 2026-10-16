# Stage 07 路线 5：pgvector 政策检索操作与数据合同

本后端是显式候选；`configs/stage05.yaml` 的 TF-IDF 默认路径不变。16 段原创模拟政策仍由 Stage 05 导入器生成固定 `chunk_id`。`configs/stage07_pgvector.yaml` 复用已验证的 `policy-index-local_embedding.json`，不重新切分、标注或嵌入不同模型。

## 建表、导入、运行

在隔离 PostgreSQL 17 + pgvector 0.8.6 库中，先按[运行存储说明](stage-07-postgres-service.md)设置忽略的本地 DSN。迁移角色执行：

```powershell
uv run --offline python scripts/migrate_stage07.py
uv run --offline python scripts/migrate_policy_pgvector.py
uv run --offline python scripts/build_policy_index.py --config configs/stage07_pgvector.yaml
uv run --offline python scripts/import_policy_pgvector.py --config configs/stage07_pgvector.yaml
uv run --offline python scripts/grant_stage07_app.py
```

前四步使用 `GRAPHRISK_STAGE07_MIGRATION_DSN`；授权脚本还需 `GRAPHRISK_STAGE07_APP_DSN`。仅检索时将 `GRAPHRISK_STAGE07_DSN` 设为受限应用角色的连接串；不要把密码写进 YAML 或 Git。查询与两组固定案例评估：

```powershell
uv run --offline python scripts/retrieve_policy.py "共享设备 人工复核" --policy-as-of 2026-07-01 --config configs/stage07_pgvector.yaml
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage07_pgvector.yaml --split development
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage07_pgvector.yaml --split frozen
```

API 显式启用时设置 `GRAPHRISK_STAGE06_CONFIG=configs/stage07_pgvector.yaml`、`GRAPHRISK_STAGE07_STORAGE=postgres` 和受限 `GRAPHRISK_STAGE07_DSN`。启动前必须已建表、导入并授权；就绪检查会重新校验向量库与本地固定索引，不会自动迁移，也不会在数据库故障时退回本地检索。路线 7 已用独立 Compose 验证真容器拓扑、故障恢复与数据边界；见[操作指南](stage-07-compose.md)和[实测报告](../../reports/stage-07-route-7-compose-faults.md)。容器配置使用固定的 host.docker.internal:11434，本地配置仍用回环 Ollama。

## 表与查询语义

`policy_vector_indexes` 记录 `index_version`、语料 SHA-256、模型 ID/digest、维度及段数；`policy_vector_chunks` 以 `(index_version, chunk_id)` 为主键，保存政策 ID/版本、原文哈希、来源、生效区间、模型 digest/维度、完整引用 JSON 和 `vector`。导入同版可重复运行；若数据库中的任意元数据、正文引用或向量与本地校验索引不一致，拒绝重用；正文哈希或引用 JSON 漂移返回 `policy_source_mismatch`，其他索引/向量故障返回 `index_unavailable`。应用角色只对这两张表有 SELECT。16 段规模执行精确余弦查询，没有 ANN 索引。

每次查询在同一 PostgreSQL repeatable-read 只读快照中核对全部 16 行。SQL 先用 `effective_from <= policy_as_of AND (effective_to IS NULL OR policy_as_of < effective_to)` 筛选，再按余弦距离和 `chunk_id` 排序。返回的原文与出处来自本地已校验的固定索引，并与数据库各行逐项比对；向量不会进入 `RetrievalResult`。日期下没有有效政策返回 `no_active_policy`，有政策但达不到与本地 dense 相同的相似度/文本重合门禁则返回 `no_match`，两者都没有 hits。模型 digest、查询维度、来源哈希/引用 JSON、DB/其他索引损坏分别返回 `embedding_model_mismatch`、`embedding_dimension_mismatch`、`policy_source_mismatch`、`index_unavailable`；不生成替代引用。

由于逐请求全量核验，当前检索器适合 16 段演示规模。若政策量扩大，应设计不可变版本/校验清单与批量验签，再根据实际延迟决定索引方案。相似度不是风险概率，也不是政策适用概率；报告引用仍须由既有核验器核对。

## 验证与学习

设置隔离测试库的迁移 DSN 后运行 `uv run --offline pytest -q tests/integration/test_stage07_pgvector.py`。它覆盖重复导入、日期先筛、稳定并列、无答案、错误输入、错 digest/维度、DB 断连、元数据/向量篡改、缺段与受限角色权限。真实 embedding 的本地/真库质量对照、延迟及 held-out 调查见[路线 5 报告](../../reports/stage-07-route-5-pgvector.md)。按[学习路线 L5](../plans/stage-07-learning-roadmap.md)独立复现 V1/V2 日期边界和一条无答案查询；个人自测不能由工程测试代签。
