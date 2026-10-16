# Stage 06 路线 0：前置门禁与演示契约验收

**检查日期：2026-09-26。工程结果：路线 0 通过；Stage 06 仍在实施中。** 输入与权限边界冻结在[演示契约](../docs/data/stage-06-demo-contract.md)。下表只记录本机实际检查，不代表 Docker 或后续 UI 已验收。

| 门禁 | 实际命令或检查 | 结果 |
|---|---|---|
| Python 与 uv | `uv --version`；`uv run --offline python --version` | uv 0.12.18；仓库 `.venv` Python 3.11.16。系统 `python` 命中 WindowsApps 别名且不可运行，因此始终用 `uv run`。 |
| 锁文件与 UI 依赖 | `uv add --group demo "streamlit>=1.57,<2"`；`uv sync --locked --group demo --offline`；`uv lock --check --offline` | 新增 `demo` 组并锁定 Streamlit 1.64.0；锁文件离线解析 160 个包。首次离线添加失败（缓存缺包）；受限网络下在线尝试被 socket 权限阻止；获准的联网重试成功。 |
| Streamlit 匹配文档 | `uv run --offline python C:\Users\12138\.agents\skills\developing-with-streamlit\scripts\discover.py --project-dir E:\GraphRisk-Agent` | 发现 `.venv\Lib\site-packages\streamlit\.agents\skills\developing-with-streamlit\SKILL.md`，已读取；路线 4 依照 1.64.0 的匹配参考实现。 |
| 本地 Ollama | `ollama --version`；`ollama list`；`GET http://127.0.0.1:11434/api/tags`；`uv run --offline python scripts/check_stage05_environment.py --config configs/stage05.yaml` | Ollama 0.33.2，`qwen2.5:0.5b` digest 与配置完全一致；LangGraph 1.2.12 与真实生成 preflight 通过。 |
| 图与政策工件 | 比较 `artifacts/stage-04/ieee-cis/graph-manifest.json` 内 `content_sha256`、`stage05-route8/metadata.json`、checkpoint 文件 SHA-256；读取 TF-IDF 索引元数据 | 图内容哈希 `8ace6710…f301a80` 与比较产物一致；checkpoint `b1e4696d…01d26e69f` 与比较产物一致；索引版本 `9eade9b4…605b3e7`、16 chunk。文件自身 SHA-256 与 manifest 的**内容哈希**不是同一字段，不能互相代替。 |
| 数据、样本与 17 案 | `uv run --offline python scripts/evaluate_stage05_agent.py`；`uv run --offline python scripts/validate_stage05_end_to_end.py --provider local_http` | 17/17 通过；`3488959` 归属 train/validation/test = 0/0/1；真实本地模型路径 `verified`（2234 ms），同进程人工拒绝 `rejected`，无政策 `insufficient_evidence`，故障 `generation_error`。新产物在 Git 忽略的 `reports/generated/stage05/`；输出未见 `is_fraud` 或 `label` 键。 |
| 回归 | `uv run --offline pytest -q` | 260 passed（32.65 s）。 |
| 资源与端口 | `.NET DriveInfo('E')`；PyTorch `torch.cuda.is_available()`；对 `127.0.0.1` 端口 8000/8501 作绑定检查 | E 盘可用约 57.76 GiB；当前 CUDA 可用；本次检查时两个端口均空闲。Docker 镜像的 C 盘空间与虚拟化条件尚未验证。 |
| Docker/Compose | `Get-Command docker`；检查常见 Docker Desktop CLI 路径；`wsl --status` | 未发现 Docker CLI；`wsl --status` 退出 1 且无诊断文本。无法确认 Docker Desktop 安装、许可、虚拟化或容器运行条件。路线 5 的 Compose 运行验收待具备 Docker 后执行。 |

## 后续门禁与决定

路线 1 可按[演示契约](../docs/data/stage-06-demo-contract.md)开始本地 API 组合；现有 Stage 05 能力和 Streamlit 环境已具备。Docker 不可用不阻断本地路线 1～4，但路线 5 和 Stage 06 的容器验收不得因此标成完成。容器方案还需解决 `Stage05Generation.local_http` 的回环地址限制：只给受控宿主 Ollama 地址开明确配置入口并加负面测试，不改为任意 HTTP 主机。当前未安装 Docker，也没有容器运行证据。

学习练习 L0：画出浏览器、Streamlit、FastAPI、Stage 05、Ollama 的信任边界；指出容器内 `localhost` 的指向，并解释为什么报告/审批状态应由单进程 API 持有。该个人练习不随工程门禁自动判为完成。
