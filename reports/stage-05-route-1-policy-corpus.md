# Stage 05 路线 1 验收：政策语料与出处

> 日期：2026-09-24；状态：**Done**。

8 份原创模拟政策位于 `data/policies/stage05/`，覆盖共享设备新旧版本、交易速度、人工复核、证据不足、可信设备例外、图线索与高风险审批。所有文件均声明模拟来源、自身 `source_ref`、项目公开展示标记、所有者和生效区间；不含第三方政策原文或 IEEE-CIS 原始记录。[语料清单与手算例子](../docs/data/stage-05-policy-corpus.md)可独立阅读。

`scripts/import_policies.py` 连续执行两次，均返回 `documents=8` 和完全相同的 manifest SHA-256：`5944138255497bb595d46513a7100045363d1f35d99fd2607954567e64145513`。manifest 6,949 字节，逻辑 `corpus_sha256=368b68942153ae3bfd6c143f0d0da483249cc32f3f77d401dc20b56693265f70`，共 16 个章节段落。每段有章节、段序、源行号与内容哈希，可回到准确原文。

单元测试覆盖：旧版在 2026-07-01 当天失效，新版当天生效；冲突政策同时适用但不自动选优；两次导入字节一致；非法日期、重叠版本、缺来源/许可、不可公开、未知冲突目标和重复文档 ID 均失败；指令式恶意句子仅作为文本保存，不改变元数据或执行行为。`uv run --offline pytest tests/unit/test_stage05_policy_corpus.py tests/unit/test_stage05_config.py -q` 为 **30 passed**；全量 `uv run --offline pytest -q` 为 **200 passed**。`uv lock --check --offline`、`uv build --offline` 和差异空白检查通过；生成的 manifest 被 Git 忽略。额外检查分发包：sdist 包含 8 份政策及导入脚本，wheel 包含语料加载器；sdist 未包含缓存、虚拟环境、模型或生成报告。

限制：本路线没有构建 `chunk_id`、检索索引或 RAG 问答，不报告召回率，也不以这 8 份模拟政策代表真实企业制度。
