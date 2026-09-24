# Stage 05 路线 0 前置门禁记录

> 检查日期：2026-09-24；状态：**部分通过，LangGraph 与真实生成端门禁阻塞**。

## 已通过

- Stage 04 工程总验收见 [stage-04-validation.md](stage-04-validation.md)：五工具、GraphSAGE、受控 XGBoost、融合比较、确定性报告和独立审计已通过。路线 0 用预先固定的 held-out 交易 `3488959` 重新运行正式调查，结果 `completed`，`get_transaction`、`get_transaction_features`、`get_risk_score`、`get_related_entities`、`find_risky_neighbors` 均为 `ok`，无 issue 或 capability gap；未读取测试标签。原报告及审计在被 Git 忽略的 `reports/generated/stage05-route0-stage04-*.json`。
- 仓库内 Python 3.11.16；`uv lock --check --offline` 成功，现有锁文件解析 128 个包。RTX 2060 6GB，检查时 `nvidia-smi` 报告总显存 6144 MiB、空闲 5955 MiB；CPU 运行能力由 Stage 04 重新加载与测试验证。当前系统 RAM 读取接口拒绝访问，沿用 Stage 00 的约 16GB 记录；没有测到生成模型的峰值内存。
- [stage05.yaml](../configs/stage05.yaml) 与类型化配置验证同源数据、输出路径、预算/停止阈值、provider 和 endpoint；显式 `policy_as_of` 输入校验通过。`uv run --offline pytest tests/unit/test_stage05_config.py -q`：17 passed。
- `uv run --offline pytest -q`：183 passed；旧阶段回归未因路线 0 契约变更而失败。

## 未通过与原因

- 仓库虚拟环境中没有 `langgraph`，本机检查未找到可用的本地生成服务、模型权重或可用的 API 凭据。没有本地 embedding 模型；当前只配置 `tfidf` 词法基线。RTX 2060 有空闲显存并不证明任何 LLM 能加载。
- `uv add "langgraph>=1,<2"` 在受限网络中无法访问 PyPI；一次提权请求的自动审核连接中断，后续经允许的尝试仍持续重试 PyPI，已停止。`uv add --offline` 也因依赖缓存不完整而无法解析。失败的临时 `pyproject.toml` 修改已撤销；`uv.lock` 未更改。因此 LangGraph 版本/许可证/依赖锁、fake provider 最小状态图均无通过证据。
- 未发生真实模型调用，故模型名称/版本、许可、加载内存、短请求延迟、结构化输出成功率、Token 和费用均为**未测**；不把文档中的候选模型或 fake provider 当作真实 smoke。没有发生 API 消费。

## 决定与解锁条件

1. 保持 `generation.provider: unconfigured`，不声称路线 0 全部完成；Stage 05 继续保持 `In Progress`。按[实验契约](../docs/data/stage-05-experiment-contract.md)执行不依赖 LLM 的语料准备仍可进行。
2. 网络恢复后在仓库环境执行 `uv add "langgraph>=1,<2"`，检查确切锁定版本和许可证，运行一个离线 fake-provider `StateGraph` 最小节点/终止 smoke。不能仅凭包可导入宣布工作流可用。
3. 真实生成优先本地：提供已授权、许可证清晰的模型权重和服务，测量加载内存、设备、短请求延迟及一次结构化输出。若本地不满足，可在已有授权的少量 API 范围内选择 HTTPS provider，并在调用前实现 24/30 元停止/总额门禁。随后记录实际型号、版本、定价来源和估算费用；未有其中一个真实 smoke 前，不进入 LLM 验收。

相关命令：

```powershell
uv lock --check --offline
uv run --offline pytest tests/unit/test_stage05_config.py -q
uv run --offline pytest -q
uv run --offline python scripts/run_investigation.py 3488959 --data-config configs/ieee_cis.yaml --training-config configs/train_baseline_ieee_cis.yaml --graph-config configs/graph_ieee_cis.yaml --comparison-run same-id-v4 --relation-limit 3 --report-output reports/generated/stage05-route0-stage04-report.json --audit-output reports/generated/stage05-route0-stage04-audit.json
```
