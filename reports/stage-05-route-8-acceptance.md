# Stage 05 路线 8：端到端验收与复盘

**日期：2026-09-26。工程门禁：通过；Stage 05 可标记 Done。** 个人 L0～L8 自测仍由学习者按[学习路线](../docs/plans/stage-05-learning-roadmap.md)完成，不因工程验收自动勾选。

## 复现命令与运行产物

```powershell
uv run --offline python scripts/check_stage05_environment.py --config configs/stage05.yaml
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage05.yaml --split frozen
uv run --offline python scripts/evaluate_policy_retrieval.py --config configs/stage05_dense.yaml --split frozen
uv run --offline python scripts/evaluate_stage05_agent.py
uv run --offline python scripts/validate_stage05_end_to_end.py --provider fake
uv run --offline python scripts/validate_stage05_end_to_end.py --provider local_http
uv run --offline pytest -q
uv lock --check --offline
uv build --offline
git diff --check
```

验收脚本在读取任何特征前先核对预选交易 `3488959`：`train=0`、`validation=0`、`test=1`，仅投影读取 `transaction_id`，不读取测试标签。真实运行的原始调查、报告、逐次审计和审批快照保存在 Git 忽略的[route8 产物](generated/stage05/route8-cc66a498ad8021cd-20260926T043411297359Z.json)；离线 fake 产物也保留在同目录，文件名带时间戳且独占写入。

## 完整链路与失败分支

当前有效的图比较运行是 `stage05-route8`：图 manifest SHA-256 `8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80`，GraphSAGE checkpoint SHA-256 `b1e4696dfca80c617ddeadf2fb2ae310b39a6e445fe9127865ea56901d26e69f`。同 ID 验证集选择融合系数 0.5；测试集 PR-AUC：受控 XGBoost **0.129566**、GraphSAGE **0.085999**、融合 **0.118062**。图模型与融合均未超过受控 XGBoost，不作为求职材料中的性能提升宣称。图目标 `3488959` 的 `find_risky_neighbors` 返回 `ok`，目标 GraphSAGE 分数 0.296911、融合分数 0.188317，邻居列表为空；空邻居不等于无图目标证据。这些分数均未校准。

真实 Ollama `qwen2.5:0.5b` 的 digest 为 `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`。完整调查与报告状态均为 `completed` / `verified`，政策检索和生成各实际调用 1 次，重试 0 次；端到端 **2141 ms**，输入/输出 **284/97 Token**，本地 API 估算费用 **0 元**。报告含独立的 Stage 02 未校准模型分数和当前图分数，只逐字引用当前适用的 `S05-GRAPH-SIGNAL-V1` 片段 `policy-97f6eb79d2a992d3cc2fd187`。验收脚本再次独立核对 `chunk_id`、文档版本、原文、来源、生效日期和内容 SHA-256；人工复核未发现超出所引原文的政策结论。`source_quote_verified` 不等于自动判定该交易欺诈。

同一派生报告以哈希 `478c5e946842e187d0838e71981e5c42069194413df2ed20012536e4eed09f10` 进入 `pending`，再由本地演示复核员明确 `reject`；审批后状态为 `rejected`，外部处置次数始终为 0。`policy_as_of=2025-12-31` 的无有效政策分支返回 `insufficient_evidence`、政策结论空、生成调用 0。受控模型断连分支返回 `generation_error`，生成尝试 2 次后停止且没有报告。这三类分支均在同一真实 held-out 调查入口下运行。复核员身份只是演示字段，尚无生产认证。

## 检索、Agent 与工程门禁

语料为 **8 份原创模拟政策、16 个稳定 chunk**，并非真实企业制度。冻结查询集 15 条（可回答 11、无答案 4）：默认 TF-IDF 的 Recall@3 **1.000**、MRR **0.848485**；本地 embedding 的 Recall@3 **1.000**、MRR **0.954545**；两者无答案误召回与旧版误引均为 **0**。本次 TF-IDF P50/P95 为 6.669/31.559 ms，本地 embedding 为 94.021/2617.431 ms，因此默认仍保留离线 TF-IDF；小语料成绩不能外推到真实企业知识库。

[17 个固定 Agent 案例](../data/evaluation/stage05_agent_cases_v1.yaml)离线复验 **17/17**：分支、工具/参数绑定、引用、无依据结论、失败重试、版本、注入与审批零外部动作通过。真实生成与 fake 案例分别报告，不合并成一个准确率。`uv run --offline pytest -q` **260 passed**；LangGraph 1.2.12 与真实 Ollama preflight 通过；锁文件、sdist/wheel 构建与 `git diff --check` 通过。分发包未包含 `.uv-cache`、`.venv`、原始 CSV/Parquet、模型、生成报告或密钥；sdist 仅含空值 `.env.example` 和 `data/raw/.gitkeep` 作为模板/目录占位。

## 首次失败、修复与边界

首次端到端运行只有 `partial_evidence`。诊断发现旧 `same-id-v4` 比较产物绑定的是更早的 GraphSAGE checkpoint，当前模型文件哈希已变化，图服务正确返回 `graph_artifact_unavailable`。保留旧比较运行作历史记录，使用当前有效 checkpoint 新建 `stage05-route8` 比较产物，并同步 Stage 05 配置、Stage 04 CLI 默认值及文档；真实图工具随后返回 `ok`。

第一次完整图证据下的真实报告被拒绝为 `draft_evidence_mismatch`。诊断发现小模型把已完成调查的 `uncertainty` 写成 `partial_evidence`，其余交易、来源、证据 ID、政策片段和动作均匹配。最终报告的状态与不确定性本来由 Stage 04 证据确定，因此把模型该枚举降为非权威提示；双向回归证明模型说错不能升级或降级权威调查状态，交易/来源/证据/引用/动作校验仍严格。失败输出保留在 Git 忽略的 `reports/generated/stage05/route7-cc66a498ad8021cd-20260926T042741867910Z.json`；修复后真实链路 `verified`。

当前可靠性仍限于单进程费用账本和本地软截止时间；审批检查点仅同进程，跨进程持久恢复属于 Stage 07。政策适用性仍由人工最终判断，图/模型分数未校准，测试集单笔演示不构成总体准确率。Stage 06 从本阶段接收已验证的五工具、版本化检索、结构化报告、审批状态和 17 案例；下一步交付 API、工作台、Docker 演示和至少 50 案例，保留上述限制与真实失败路径。
