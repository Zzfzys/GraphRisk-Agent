# Stage 05 路线 0 前置门禁记录

> 检查日期：2026-09-24；路线状态：**Done**。Stage 05 整体仍为 `In Progress`，政策 RAG、正式 Agent 报告与审批尚未实现。

## 上游与配置门禁

- Stage 04 的[总验收](stage-04-validation.md)已有正式模型、图、五工具、失败状态、确定性报告和独立审计。路线 0 使用预先固定的 held-out 交易 `3488959` 复验：报告 `completed`；`get_transaction`、`get_transaction_features`、`get_risk_score`、`get_related_entities`、`find_risky_neighbors` 五个工具均为 `ok`，无 issue 或 capability gap，未读取测试标签。原报告和审计仅保存在被 Git 忽略的 `reports/generated/stage05-route0-stage04-*.json`。
- 仓库内 Python 3.11.16；用户将 `langgraph>=1,<2` 加入 `pyproject.toml` 和 `uv.lock`，`uv lock --check --offline`、`uv sync --locked --offline` 均通过，锁文件现有 149 个包。实际导入版本 **LangGraph 1.2.12**，安装元数据的许可为 **MIT**。确定性 fake-provider `StateGraph` 的起点→节点→终点运行通过，返回 `fake:route0`。
- [stage05.yaml](../configs/stage05.yaml) 和配置校验绑定 Stage 01/02/04 同一 IEEE-CIS 来源，限制输出路径、模型摘要、loopback endpoint、步骤/调用/超时与 24/30 元 API 停止/总额阈值；请求显式携带 `policy_as_of`。冷启动实测接近原 120 秒超时，路线 0 将单次请求超时改为 180 秒，总时长仍为 300 秒。该预算目前只是配置，执行期费用停止逻辑属于路线 7。

## 真实本地生成 smoke

本机 Ollama 0.33.2 服务在 `http://127.0.0.1:11434` 运行。当前 PowerShell PATH 未包含 Ollama CLI，但二进制位于 `C:\Users\12138\AppData\Local\Programs\Ollama\ollama.exe`，可用绝对路径调用。下载并摘要校验通过的模型为 `qwen2.5:0.5b`，Ollama digest 为 `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`，本地文件约 397 MB；`ollama show` 显示 494.03M 参数、Q4_K_M 量化、Apache-2.0 许可。模型与提供方来源：[Ollama 模型页](https://ollama.com/library/qwen2.5%3A0.5b)。

使用**合成**证据 ID `E-1` 和 JSON schema 进行短请求，模型输出可解析且严格等于 `{"status":"needs_review","evidence_ids":["E-1"]}`。三次观测：

| 请求 | 墙钟时间 | 加载时间（Ollama） | 输入/输出 Token | 说明 |
|---|---:|---:|---:|---|
| 第一次，刚下载后 | 109.248 s | 35.805 s | 52 / 26 | 首次初始化的冷请求，接近原 120 s 超时；不当作通常延迟 |
| 同一模型热请求 | 0.565 s | 0.002 s | 44 / 26 | 再次得到符合 schema 的 JSON |
| 可复现 preflight 脚本 | 0.396 s | 0.002 s | 48 / 26 | 先核对 `/api/tags` 模型 digest，再请求 `/api/generate` |

`ollama ps` 显示 100% GPU、模型占用约 481 MB；RTX 2060 总显存 6144 MiB，加载后 `nvidia-smi` 观测到 546～550 MiB 已用、约 5406～5410 MiB 可用。此为运行时抽样值，**不是峰值内存**；系统 RAM 的读取接口拒绝访问，沿用 Stage 00 约 16 GB 的记录。没有 API 调用或费用，实测 API 花费 0 元。模型只是路线 0 的连通性/结构化 smoke 候选，尚未验证政策结论质量、引用真实性或正式调查能力。

首轮下载在约 13 MB 处报 `unexpected EOF`；续传最终成功。原始下载日志、两次手工生成摘要和 preflight JSON 保存在 Git 忽略的 `reports/generated/stage05-route0-*`，不进入仓库。可复现命令：

```powershell
uv lock --check --offline
uv sync --locked --offline
uv run --offline pytest tests/unit/test_stage05_config.py tests/unit/test_stage05_preflight.py -q
uv run --offline python scripts/check_stage05_environment.py --config configs/stage05.yaml
uv run --offline pytest -q
uv build --offline
```

结果：路线 0 聚焦测试 **22 passed**，全量回归 **188 passed**；sdist/wheel 构建成功。构建器警告仓库内 `.uv-cache` 可能进入分发包，因此检查了产物：wheel 50 个成员、sdist 161 个成员，均无缓存、原始 CSV、模型或生成报告；sdist 仅含版本控制的 `data/raw/.gitkeep`。`scripts/check_stage05_environment.py` 同时验证 fake 状态图、LangGraph 版本/许可、Ollama 模型摘要与结构化输出；当前机器返回 `passed/passed`，输出样例存于 `reports/generated/stage05-route0-preflight.json`。模型、报告和下载日志均被 Git 忽略。

## 决定与后续边界

1. 选择 `local_http` 与已核对 digest 的 `qwen2.5:0.5b` 作为路线 0 smoke 环境，不将它宣称为最终高质量生成模型。后续若替换模型，必须更新摘要、配置与 smoke 记录。
2. 本地 embedding 尚未安装或验证。路线 2 可先用已有 scikit-learn TF-IDF 建立并评估词法基线；dense 检索只能在本地 embedding 单独过资源和质量门禁后声称完成。
3. 下一条路线是政策语料与出处。路线 0 仅证明依赖与本地生成端可用，未实现政策检索、证据引用、Agent 决策或人工审批；这些仍按路线 1～8 各自验收。
