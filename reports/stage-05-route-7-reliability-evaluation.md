# Stage 05 路线 7：可靠性与首批 Agent 评估

**日期：2026-09-26。结论：路线 7 实现与离线门禁通过；Stage 05 整体仍为 In Progress，路线 8 待执行。**

## 交付与复现

- [可靠性契约](../docs/data/stage-05-reliability-evaluation.md)与 `Stage05ReliableRunner`：只重试连接/超时等暂时性错误，实际检索/生成尝试各受配置上限约束；输入、数据源、计划、索引与生成 schema/证据错误直接终止。总时限在调用前后检查，本地 HTTP 请求取剩余时限；费用在每次请求前预留，未知计费的超时不退还预留。
- [固定 Agent 案例](../data/evaluation/stage05_agent_cases_v1.yaml)：SHA-256 `d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1`；17 个案例一次性从文件执行，不在脚本里删减难例。逐案输出位于 Git 忽略的 `reports/generated/stage05-agent-eval-v1.json`。
- 复现：`uv run --offline python scripts/evaluate_stage05_agent.py`；`uv run --offline pytest tests/unit/test_stage05_reliability.py tests/integration/test_stage05_agent_evaluation.py -q`。真实抽样：`uv run --offline python scripts/run_stage05_reliable.py 3488959 --policy-as-of 2026-07-01 --provider local_http`。默认运行文件带 UTC 微秒时间且独占创建，避免覆盖失败证据。

## 离线固定集结果

**17/17 案例通过，复合门禁通过率 1.000。** 门禁同时核对预期分支、实际调用次数、工具白名单与交易/数据源/日期参数绑定、逐条引用 ID/原文/版本、无无依据政策结论、审批状态与外部处置次数。覆盖正常、无政策、政策尚未生效、已失效 v1 注入、政策文本诱导自动封禁、模型连接失败与恢复、检索暂时/永久故障、图部分失败、来源错绑、非法工具计划、交易不存在、总超时、费用停止、审批批准与拒绝。所有审批案例的外部处置次数为 0。

17 案合计 15 次检索和 11 次生成尝试；非超时 fake 案例的延迟以进程调度/磁盘缓存为主，**不应**作为真实模型性能基准。所有 fake 生成返回 44 输入 / 11 输出 Token、估算费用 0 元，用于验证审计字段，不代表真实 Token 消耗。总超时案例使用注入时钟，模拟检索返回时已耗时 2 秒、超过 1 秒上限，于是生成尝试为 0。模型连接失败案例尝试 2 次后停止；schema 或来源错误不会重试。旧版案例在 `2026-07-01` 故意返回有效期止于当天的 v1 片段，报告拒绝该引用。注入案例把 `auto_block` 指令放入不可信检索文本，并让 fake 模型尝试输出这一动作；报告无产物，未进入审批或处置。

## 真实本地模型抽样与故障记录

使用已核对 digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67` 的 `qwen2.5:0.5b`、默认 TF-IDF 索引和 IEEE-CIS 交易 `3488959`。成功运行状态为 `partial_evidence`：6 个图节点，实际政策检索 1 次、模型生成 1 次、重试 0 次；总 6265 ms，其中生成约 4906 ms；277 输入 / 86 输出 Token，本地运行估算 API 费用 0 元。报告引用 `S05-GRAPH-SIGNAL-V1` 的当前片段，未出现报告引用校验错误。Stage 04 图服务仍返回 `graph_artifact_unavailable`，因此不能将此报告标为完整图证据，也不能把未校准模型分数当作欺诈概率。该一次抽样只证明链路可运行且约束生效，不代表真实模型质量分布。

首次真实尝试时 Ollama 服务未监听，审计记录 `generation_error`、检索 1 次、生成连接重试 2 次、费用 0；之后发现本机模型目录是 `E:\ollama`，新服务未继承 `OLLAMA_MODELS`，显式指定目录并核对 `/api/tags` 的模型摘要后重跑成功。首次失败的默认输出路径在重跑时被覆盖；为防再次丢失原始失败产物，CLI 已改为时间戳文件名与独占写入。离线固定集中的 `model_unavailable` 保留同类失败分支，不能用后来的成功覆盖失败事实。

## 完整校验与限制

`uv run --offline pytest -q` 为 **260 passed**；聚焦可靠性/评估测试为 **12 passed**；`uv lock --check --offline`、`uv build --offline`、`git diff --check` 通过。wheel/sdist 检查没有 `.uv-cache`、`.venv`、生成报告或模型文件；sdist 仅包含受控的 `data/raw/.gitkeep`。真实调查配 fake 报告的 CLI smoke 再次得到 `partial_evidence`，并产生新的时间戳输出文件，没有覆盖前一份报告。费用账本目前是单进程内存态；同步本地调查与检索不能被软截止时间强制抢占；fake 注入案例不是完整的对抗安全证明。路线 8 需要以真实 held-out 交易完成完整演示、复核整体门禁及 Stage 06 交接。
