# C 盘存储检查与保守清理说明

检查日期：2026-10-07。只读盘点，未删除、压缩或迁移 C 盘文件；未读取密钥内容。

## 发现

C 盘检查时剩余约 **5.86 GiB**，E 盘约 **53.08 GiB**。这是磁盘存储容量，不是 RAM 内存。

项目 Python 位于 E:\GraphRisk-Agent\.venv\Scripts\python.exe，基础 Python 位于仓库 .uv-python。项目 uv 缓存、交易数据、模型和报告也位于 E 盘。但默认 TEMP/TMP 指向用户 C 盘临时目录，部分评估脚本使用 tempfile.TemporaryDirectory()，项目子进程此前可能产生 C 盘临时写入。正常退出会清理这些目录，中断后可能留下文件。

以下是文件逻辑大小之和，不是可回收物理空间承诺；未计入无法访问的文件，未跟随子目录重解析点。没有历史基线，不能据此证明各项增长量或把全部空间减少归因于项目。

| C 盘位置 | 约 GiB | 处理意见 |
|---|---:|---|
| 用户 AppData\Local\uv\cache | 0.876 | 用 uv 缓存命令清理，不手动删除内部目录 |
| 用户 AppData\Local\pip\Cache | 0.281 | 可清理下载缓存，不等于卸载已安装包 |
| 用户 AppData\Local\Temp | 0.681 | 用 Windows 临时文件清理，保留仍在使用的文件 |
| 用户 .cache\codex-runtimes | 2.373 | 当前工具使用其中 codex-primary-runtime，保留 |
| 用户 .codex | 1.320 | 包含聊天、配置、插件和运行文件，保留 |
| 用户 AppData\Local\Programs\Ollama | 2.744 | 已安装程序及计算库，不能直接删除库文件 |
| 用户 AppData\Local\Programs\DockerDesktop | 3.285 | 已安装程序，不能逐个删除 ISO 或资源文件 |
| 用户 AppData\Local\Docker | 1.660 | VHDX 约 1.546；不能当缓存删除 |
| 用户 Downloads | 0.609 | 人工确认不再需要的安装包，保留个人文件 |
| C:\pagefile.sys | 9.500 | Windows 分页文件；Docker 已出现资源不足，保留 |
| C:\hiberfil.sys | 6.149 | 休眠相关系统文件，保留 |

Ollama 默认 C:\Users\12138\.ollama\models 本次扫描为空。这不能证明当前服务模型存放在哪里，但这里没有发现大模型权重占用。

Docker settings-store.json 的 CustomWslDistroDir 当前指向 C:\Users\12138\AppData\Local\Docker\wsl。当前配置仍会把 Docker 数据写到 C 盘；之前迁移到 D 盘的情况不能作为当前已迁移的依据。

## 阻止项目临时文件继续默认落到 C 盘

每个新的项目 PowerShell 终端执行：

    Set-Location E:\GraphRisk-Agent
    . .\scripts\enter_project_env.ps1
    uv run --offline python -c "import sys,tempfile; print(sys.executable); print(tempfile.gettempdir())"

预期 Python 位于仓库 .venv，临时目录为 E:\GraphRisk-Agent\artifacts\tmp。uv 缓存保持 .uv-cache，托管 Python 保持 .uv-python；pip 缓存位于 artifacts\pip-cache。这些目录已被 Git 忽略。

脚本也保留已有 NO_PROXY 并追加本地 API/Ollama/Docker 主机名，验证正常 HTTPX 本机请求返回 200；不改变系统代理设置。

脚本仅更改当前终端及其之后启动的子进程；已经运行的服务需在初始化后的终端重启才采用新目录。不改 Windows 全局变量，不迁移应用或模型。应用及系统仍可能正常写入 C 盘。

## 保守清理顺序

1. 关闭不需要的安装程序和项目进程。打开 **Windows 设置 → 系统 → 存储 → 临时文件**，查看条目，再清理临时文件、缩略图等缓存。不要选择“下载”，回收站也应先检查是否有需要恢复的文件。Windows 更新/以前的安装项可能影响回退能力，不能按零影响项处理。
2. 用 uv 命令修剪 **C 盘**缓存，保留 E 盘供离线运行的项目缓存：

       uv cache prune --cache-dir "$env:LOCALAPPDATA\uv\cache"

   prune 仅清理 uv 判断可移除的条目，实际释放量可能很小。不要加 --force，不要直接删除 uv 内部文件。其他工具重新安装依赖时可能需要再次下载。
3. pip 缓存约 0.281 GiB。项目 .venv 本次确认没有 pip，不要为清缓存安装 pip。可在关闭 pip 安装进程后，用资源管理器打开 C:\Users\12138\AppData\Local\pip\Cache，只清理这个缓存目录中的内容；保留其他 Python 目录。已安装包不会因此卸载，后续 pip 安装可能需要重新下载。
4. 下载目录中的 WorkBuddy 安装包约 **0.494 GiB**。仅在确认程序已经安装、不需保留安装包时，通过资源管理器删除该安装包。临时目录也有 VS Code/Docker 安装更新文件，优先让 Windows 清理，不整目录强删。
5. 如需减少后续 Docker 增长，在 Docker 正常启动且确认容器数据备份后，通过 **Docker Desktop → Settings → Resources → Advanced** 查看并更改磁盘位置，使用新的空目标目录。不得手动拖动/删除 VHDX，不要用 docker system prune --volumes 清理项目数据库。此前 D 盘访问权限错误须在迁移前解决。

释放量受占用、缓存保留策略和物理分配影响，不能保证固定 GiB。需要大量空间时，应通过 Windows 存储页面找到其他大目录，再选择迁移个人文件或正常卸载不使用的应用。

## 验证

初始化脚本已验证：uv cache dir 返回 E 盘仓库 .uv-cache；项目 Python 断言通过，TEMP/TMP、uv/pip 缓存和托管 Python 均位于仓库内；NamedTemporaryFile 实际在 artifacts/tmp 创建并正常自动清理；git check-ignore 确认四个生成目录被忽略。此次不开展数据、模型、数据库或系统文件清理。

## 官方依据

- [Microsoft：Windows 存储设置](https://support.microsoft.com/en-us/windows/experience/storage-filemanagement/storage-settings-in-windows)
- [uv：缓存管理与安全](https://docs.astral.sh/uv/concepts/cache/)
- [pip：缓存命令](https://pip.pypa.io/en/stable/cli/pip_cache/)
- [Docker：Windows WSL 2 数据位置](https://docs.docker.com/desktop/features/wsl/)
