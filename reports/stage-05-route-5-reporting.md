# Stage 05 路线 5 验收：结构化报告与引用核验

> 执行日期：2026-09-25；路线状态：**Done**。Stage 05 整体仍为 `In Progress`，人工审批与运行级超时/重试尚未交付。

## 交付与边界

[派生报告契约](../docs/data/stage-05-reporting.md)定义报告、生成适配器、引用和适用性门禁。模型只选择本次政策片段与受控枚举；交易事实、XGBoost 未校准分数、图状态和政策原文由代码填入并核验。原 Stage 04 报告和审计保存在 `graph_run`，派生审计独立记录 prompt/model/index 版本、Token、耗时、费用及报告哈希。无政策证据时强制弃答，失败不输出虚构报告。

首轮真实模型虽然返回了真实 `chunk_id`，却选入“可信设备例外”：当前 Stage 04 没有独立可信设备批准记录，故它不能作为该交易的适用规则。这次人工复核发现仅核对引用存在不足以保证语义支持。修正后，代码排除缺少可核前提的例外和阈值触发条款；最终报告使用逐字政策原文，并显式标注适用性待人工复核。模型生成的自由政策解释不进入报告。

## 复验结果

`uv run --offline pytest tests/unit/test_stage05_reporting.py -q`：**16 passed**，覆盖正常、图部分失败、无命中、未经证明的例外/触发条款、伪造或错绑证据、过期/篡改正文、模型超时和 Ollama digest/Token 适配。`uv run --offline pytest -q`：**239 passed**。`uv lock --check --offline`、`uv build --offline`、`git diff --check` 均通过；构建产物未包含 `.uv-cache`、虚拟环境或生成报告。

真实本地运行：

```powershell
uv run --offline python scripts/run_stage05_report.py 3488959 --policy-as-of 2026-07-01 --relation-limit 3 --provider local_http --output reports/generated/stage05/route5-ollama-3488959.json
```

结果：路线 4 图为 `partial_evidence`，派生报告保持 `partial_evidence`，未请求审批。当前 `find_risky_neighbors` 为 `graph_artifact_unavailable`；派生报告中的 XGBoost 分数为 `0.19668689370155334`，明确为**未校准模型分数**。政策只摘录 `S05-GRAPH-SIGNAL-V1`“解释限制”段落：目标没有 held-out 图分数时应标明图能力缺口，且图分数/共享实体不证明欺诈。人工对照该段原文、当前图失败和输出文案，未发现额外虚构的政策结论；具体政策适用性仍待人工复核。模型为固定 digest 的 `qwen2.5:0.5b`，本次 prompt/output 为 **277/86 Token**，生成耗时 **953.56 ms**，本地 API 费用 **0 元**。这些是单次样例，不代表总体质量或延迟分布。

下一步路线 6 在报告哈希和独立审计上实现人工审批暂停/恢复；路线 7 评估更多案例、暂时性故障重试及费用执行门禁。
