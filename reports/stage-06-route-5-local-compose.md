# Stage 06 路线 5：本地启动与 Docker Compose 验收记录

> 2026-09-27 最终验收；**路线 5 工程门禁通过**。Docker Engine 已恢复，API/工作台容器健康、真实调查与人工拒绝、镜像与挂载检查、Compose 关闭均通过。Stage 06 路线 6～7 仍待实施。

## 已交付

- `scripts/run_stage06_local.py` 提供 Windows PowerShell 一条本地启动命令。先校验 Python 3.11、Stage 05 配置、Ollama 模型名称及 digest、8000/8501 端口，再以**单 worker** 启动 API、等待 `/api/v1/ready`，随后启动 Streamlit 并等待 `/_stcore/health`。工作台只访问本次 API；子进程退出或 Ctrl+C 时清理两项服务。日志位于忽略目录 `reports/generated/stage06-local/`。
- `Dockerfile` 只复制锁文件、应用代码、配置、工作台与公开模拟政策；`.dockerignore` 排除原始/中间数据、工件、报告、密钥、本地环境及模型格式。`compose.yaml` 分开 API 与工作台，宿主端口仅绑定 `127.0.0.1`；interim/processed 数据及 Stage 02/04/05 工件只读挂载，`reports/generated` 单独可写；工作台没有数据和模型挂载。API 就绪后才启动工作台。
- 图比较产物过去把 checkpoint 路径写为 Windows 绝对路径，容器中不能直接读取。新比较产物改写相对工件路径；服务读取旧 Windows 路径时仅在能识别配置中的工件根后重绑到容器工件目录，随后仍校验模型 SHA-256。图清单还验证 train/validation/test 源文件哈希，因此 Compose 补挂 `data/processed/ieee-cis` 为只读；标签数据仅在 API 容器内用于来源完整性校验，不进入工作台或镜像。
- `configs/stage06_compose.yaml` 将 Ollama 目标限定为 `http://host.docker.internal:11434/api/generate`，仍固定 `qwen2.5:0.5b` 和 digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`。`local_http` 仍只允许回环；其他宿主、端口、路径、凭据或查询参数均被配置测试拒绝。报告 provider 在调用 `/api/generate` 前还会查询 `/api/tags` 核对 digest。

## 本机真实证据

```powershell
uv run --offline pytest -q
uv run --offline python scripts\run_stage06_local.py
```

全量 **302 passed，1 个 Starlette/anyio DeprecationWarning**，32.76 秒。聚焦测试与图工件集成测试均通过；`uv build --offline` 成功生成 sdist 与 wheel。启动器管理的 API `/api/v1/ready` 返回 200，五项 `data/risk_model/graph/policy_index/ollama` 均为 `true`；工作台 `/_stcore/health` 返回 200。同一进程内真实 held-out 交易 `3488959`、政策日期 `2026-07-01`、关系上限 3 的 API 调查返回 `verified` 和 `run-e85a6a52934b-adc1080430e94ff18769e77c73e773b4`；提交 `reject` 后再次 GET 仍为 `rejected`，`external_action_count=0`。测试后两端口均已释放。运行报告和 API/UI 日志不纳入 Git。

静态测试解析 Compose YAML，校验本机端口、服务依赖、工作台 API 地址、只读/可写卷和原始数据未挂载；检查 Dockerfile COPY 与 `.dockerignore` 的受限路径。2026-09-27 安装 Docker CLI **29.8.0** 和 Compose **v5.5.1** 后，`docker compose -f compose.yaml config --quiet` 返回 0。**语法通过仍不是镜像构建或镜像内容扫描的证据。**

## 此前未完成的容器门禁与补验命令

2026-09-27：Docker CLI、Compose 可运行，`docker compose config --quiet` 通过；`docker version` 只有 Client，Server 返回 `Docker Desktop is unable to start`，稍后又提示 `dockerDesktopLinuxEngine` 命名管道不存在。`docker desktop start` 未使 Engine 就绪；`wsl --status` 退出码 50，WSL/VirtualMachinePlatform 的功能状态需管理员权限才能进一步查询，根因尚未确认。故不能执行 `compose up`、容器健康/调查/审批、`compose down` 或镜像扫描，也不能确认镜像磁盘容量、宿主 `host.docker.internal` 连通性。路线 5 与 Stage 06 均不得标 Done。

同日再次复查：`docker version` 已显示 Client 与 Linux Server（Docker Desktop 4.92.0，Engine 29.8.0）；`docker compose ps --format json` 无容器，`docker image inspect graphrisk-stage06:local` 报镜像不存在。当前本地启动器占用 8000/8501，API `/api/v1/ready` 五项组件均为 `true`，工作台 HTTP 200；这些是**本地进程**证据，不能代替 Compose 验收。执行下方容器步骤前须先在本地启动器窗口按 Ctrl+C，确认两端口释放。C 盘剩余空间约 9.7 GiB，构建前需留意 Docker 镜像盘空间。

Docker Desktop 的 Linux Engine 显示运行中、本地启动器退出后，从仓库根目录依次运行：

```powershell
$env:Path = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;$env:Path"
docker version  # 须同时显示 Client 与 Server
docker compose config
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health
docker run --rm --entrypoint python graphrisk-stage06:local -c "from pathlib import Path; r=Path('/app'); forbidden=['.env','data/raw','data/interim','data/processed','artifacts','reports/generated']; assert not any((r/x).exists() for x in forbidden)"
docker compose down
```

随后按[演示契约](../docs/data/stage-06-demo-contract.md)用同一 held-out 交易经容器 API/工作台完成调查、人工拒绝和刷新读取，检查 `external_action_count=0`；执行镜像/构建上下文受限文件扫描并记录结果。若 API 就绪中的 `ollama=false`，先验证 Docker Desktop 容器能访问宿主的 `host.docker.internal:11434` 以及模型 digest；不要将配置改为任意 HTTP 地址或直接开放局域网。

## 2026-09-27 构建阻塞与磁盘恢复

首次 `compose up --build` 在拉取 `python:3.11-slim-bookworm` 时无法连接 `auth.docker.io:443`；本机该域名解析出的 IPv4/IPv6 地址也无法建立 TCP 连接。`graphrisk-stage06:local pull access denied` 是项目镜像尚未构建时的伴随信息。已将 Dockerfile 基础镜像改为可拉取的 GHCR Python 3.11 镜像并固定 digest，保留独立固定的 uv 0.12.19。`pyproject.toml` 与 `uv.lock` 改为 Windows CUDA、Linux CPU 的 PyTorch 来源；Windows `uv sync --locked --offline --group demo` 成功，`torch 2.9.1+cu128` 且 CUDA 可用，聚焦测试 9 passed。

第二次构建解析 147 包，Linux 安装 136 包且 `torch==2.9.1+cpu`；镜像导出时 C 盘空间从约 9 GB 降至 0，构建被中止。`docker image inspect` 曾返回项目镜像 ID，但容器运行尚未验证。构建缓存清理 6.41 GB 后，宿主 C 盘空间未回升，因为 Docker 的 `docker_data.vhdx` 已扩展至约 11.55 GB。无运行容器；Docker 内部文件系统变为只读，`docker image rm` 失败。已终止 `docker-desktop` WSL 实例，待用户在管理员环境压缩虚拟磁盘并将 Docker 的磁盘映像位置迁至空间充足的 E 盘后，才能继续镜像与容器验收。不得据此把路线 5 标为完成。

用户随后报告 DiskPart `compact vdisk` 成功；本机复查 C 盘仍仅约 0.09 GiB 可用，E 盘约 57.70 GiB，`docker-desktop` WSL 实例为 Stopped。因此下一步应先让 C 盘恢复少量可用空间，再通过 Docker Desktop 的 Settings → Resources → Advanced → Disk image location 将镜像磁盘迁至 E 盘；不可因 DiskPart 成功就重启构建。

## 2026-09-27 迁移后再次核验

- 当前磁盘空余：C 盘约 23.70 GiB、D 盘约 20.64 GiB、E 盘约 57.70 GiB；已解除 C 盘耗尽状态。本次未重新构建镜像。
- Docker 配置 `CustomWslDistroDir` 为 `D:\Docker\DockerDesktopWSL`；该目录下 `disk/docker_data.vhdx` 与 `main/ext4.vhdx` 均存在。文件迁移不能代替 Engine 正常启动的证据。
- `docker version` 和 `docker compose ps --all --format json` 均返回 `Docker Desktop is unable to start`。启动日志显示 WSL `--import-in-place` 挂载 `D:\Docker\DockerDesktopWSL\main\ext4.vhdx` 时返回 `Wsl/Service/RegisterDistro/MountDisk/HCS/E_ACCESSDENIED`。已定位到虚拟磁盘访问失败；具体权限或系统限制原因尚未确认。
- `uv sync --locked --offline --group demo` 成功；`uv run --offline pytest -q`：**302 passed, 1 warning in 57.10s**，警告为 Starlette 使用已弃用的 anyio 别名。`docker compose config --quiet` 返回 0。
- 尚缺：Engine 恢复后验证可用镜像、容器健康、真实交易调查、人工拒绝与重读、镜像受限文件扫描及 `compose down`。路线 5 继续保持未完成；下一步先处理迁移后的 WSL 虚拟磁盘访问问题。

## 2026-09-27 虚拟磁盘访问诊断

D 盘为健康 NTFS；两个 VHDX 的属性均为 Archive，没有只读、压缩或加密标志。Docker Desktop 为 stopped，WSL 当前未注册发行版。当前普通登录账户为 `DESKTOP-G17N5P0\12138`；Docker 数据目录及两份 VHDX 的所有者是 Administrators，SYSTEM/Administrators 有完全控制，但普通用户仅有读取/执行权限。用该账户以 ReadWrite 打开 `main/ext4.vhdx`（未写入任何内容）实际返回 Access denied，证实缺少写权限。

现有 ACL 已备份到忽略目录中的 `reports/generated/docker-acl-before-fix-20260927-155354.txt`，5 项全部成功。待用户在管理员 PowerShell 为该账户授予仅限 `D:\Docker\DockerDesktopWSL` 目录树的 Modify 权限；当前工具会话非 Windows 管理员，尚未修改 ACL。修复后须重新测试文件访问并启动 Docker，不能提前声称 Engine 已恢复。

## 2026-09-27 补写权限后仍无法挂载

再次实测：当前用户的 Modify 权限已经生效，`main/ext4.vhdx` 的 ReadWrite 打开测试成功；Docker 启动仍报同一 `MountDisk/HCS/E_ACCESSDENIED`。两份 VHDX 所有者仍为 `BUILTIN\Administrators`。因此上一轮补写权限仅解决普通文件访问，尚未解决 WSL 挂载。

WSL 官方仓库问题记录 https://github.com/microsoft/WSL/issues/41273 中，维护者说明迁移后所有者变为 Administrators 可导致普通用户挂载失败，建议恢复为当前用户。下一步由管理员仅将 `main/ext4.vhdx` 与 `disk/docker_data.vhdx` 的所有者改为 `DESKTOP-G17N5P0\12138`，关闭 WSL 后重启 Docker 并复验；尚未执行此所有者修改，也未验证其能否完全解决本机问题。没有证据支持关闭安全软件或重置/删除虚拟磁盘。

## 2026-09-27 容器最终验收

Docker Desktop 4.92.0 的 Engine 29.8.0 已同时出现在 `docker version` 的 Client 与 Linux Server 中。Docker 磁盘仍位于 D 盘，验收后 D 盘空余约 20.54 GiB。两份 VHDX 所有者恢复为当前登录用户后，旧的启动会话仍停留在 `Starting the Docker Engine...`；普通 `docker desktop stop --timeout 30` 超时，`docker desktop stop --force --timeout 20` 及 `docker desktop start --timeout 40` 成功，随后 Linux Server 可用。没有执行重置或删除 Docker 数据。

本次复用此前按当前 Dockerfile 和锁文件构建的 `graphrisk-stage06:local` 镜像（ID `sha256:db57b1cdbc7786fe916c3ae70ce88b425adce934e70462d43480b918edeff312`），执行 `docker compose up -d --no-build`；本次没有重新构建镜像。`docker compose ps` 显示 API 与工作台均 healthy，分别只映射 `127.0.0.1:8000`、`127.0.0.1:8501`。宿主 `/api/v1/ready` 返回 `ready`，`data/risk_model/graph/policy_index/ollama` 五项全为 `true`；`/_stcore/health` 返回 200；工作台容器内请求 `http://api:8000/api/v1/ready` 也返回相同就绪状态。宿主 Ollama 运行 `qwen2.5:0.5b`，digest 与演示契约固定值一致。

真实 held-out 交易 `3488959`、数据源 `ieee_cis`、政策日期 `2026-07-01`、关系上限 3，经容器 API 创建调查返回 `verified`、报告 `source_quote_verified`、审批 `pending`，运行 ID 为 `run-408ce501c442-1c1c2ddf96ed429d9db38c371be71d38`。人工提交 `reject` 后重新读取，审批仍为 `rejected`，报告 SHA-256 保持不变，`external_action_count=0`。首次省略必填 `dataset_source` 的测试请求收到结构化 `invalid_request`，补齐请求后通过。

镜像内扫描 `/app/.env`、`/app/data/raw`、`/app/data/interim`、`/app/data/processed`、`/app/artifacts`、`/app/reports/generated`、`/app/tests`、`/app/docs`、`/app/scripts` 均不存在；在 `/app` 应用目录排除依赖与缓存后，未发现 CSV、Parquet、模型权重、密钥和 `.env` 文件。允许公开演示的 `/app/data/policies/stage05` 存在。实际 `docker inspect` 显示 API 的五项数据/工件卷均为只读，只有 `reports/generated` 可写；工作台容器无卷挂载。`.dockerignore` 和现有静态测试限制构建上下文，`docker compose config --quiet` 成功。

最后执行 `docker compose down`，API、工作台和 Compose 网络均被删除；`docker compose ps --all` 为空，8000/8501 不再监听。路线 5 工程验收完成；个人 L5 学习自测由学习者独立完成。此记录不代表 Stage 06 总验收，路线 6 的 50 案评估及路线 7 的总复盘尚待实施。

## L5 学习练习

画出 `浏览器 → 本机 8501 → workbench 容器 → api:8000 → host.docker.internal:11434`；指出 Compose 只读卷不是镜像层、`reports/generated` 是唯一可写挂载、容器内 `127.0.0.1` 指向容器自身。个人 L5 自测由学习者完成。
