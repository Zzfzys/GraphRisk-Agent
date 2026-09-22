# Stage 00：环境与数据门禁

> 状态：Done  
> 计划时间：Week 0  
> 实际开始：2026-09-21  
> 实际结束：2026-09-21

## 阶段目标

建立可复现的最小开发环境，确认磁盘、Python、Git、数据和关键依赖具备开工条件。此阶段不实现业务模型。

## 前置条件

- 已批准总体计划；
- 明确使用 E 盘作为主要项目空间；
- 用户明确授权开始执行。

## 范围

- 将 E 盘可用空间提高到至少 30～50GB；
- 初始化 Git 仓库；
- 使用 uv 管理 Python 3.11、虚拟环境和依赖锁；
- 建立最小 `pyproject.toml` 与 smoke test；
- 验证 GPU 与 PyTorch 运行策略；
- 验证 IEEE-CIS 数据获取路径和许可；
- 准备小型合成数据后备方案；
- 决定 Docker Desktop 的安装时间。

## 非目标

- 不训练模型；
- 不安装全部后期基础设施；
- 不创建复杂业务目录；
- 不下载未确认许可的大型数据。

## 交付物

- 可复现的 Python 环境；
- Git 仓库与基础忽略规则；
- 环境诊断命令；
- 数据获取说明；
- 磁盘容量预算；
- 可通过的 smoke test。

## 验收标准

- [x] `uv run python --version` 返回约定版本；
- [x] `uv run pytest` 能运行并通过 smoke test；
- [x] Git 工作区状态正常；
- [x] E 盘空间满足预算；
- [x] 数据来源、保存位置和许可处理方式已经记录；
- [x] 合成数据方案可在真实数据缺失时启用；
- [x] 所有密钥路径均在 Git 忽略范围内。

## 验证证据

- `uv run --offline python --version`：Python 3.11.16；
- `uv run --offline pytest`：2 passed；
- `uv lock --check --offline --cache-dir .uv-cache`：锁文件有效；
- `git status --short --branch`：`main` 分支状态可读取；
- Git ignore 检查：`.env`、`kaggle.json`、原始数据、Parquet 和模型权重均被忽略；
- `scripts/diagnose_environment.ps1`：Git 2.55.0、uv 0.12.17、RTX 2060 6GB；
- E 盘可用空间：约 47.52GB；
- IEEE-CIS 官方数据页面可访问；认证下载和规则接受需要用户 Kaggle 账户；
- 数据位置、许可处理和来源记录见 `docs/data/data-access.md`；
- 合成后备方案见 `docs/data/synthetic-fallback.md`；
- 存储预算见 `docs/data/storage-budget.md`。

## 风险与降级方案

- IEEE-CIS 无法取得：先用合成数据完成管线接口；
- Docker 安装受阻：开发阶段使用本地进程，MVP 前再容器化；
- CUDA 兼容性问题：先用 CPU 验证，随后安装兼容的 PyTorch wheel；
- 磁盘仍不足：缩小数据规模并重新规划数据目录。

## 决策记录

| 日期 | 决定 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-21 | 增加独立 Week 0 门禁 | 当前环境尚未具备直接开发条件 | 后续阶段仅在门禁通过后开始 |
| 2026-09-21 | 使用仓库内 Python 3.11.16、uv cache 和虚拟环境 | 系统没有可用 Python，仓库内环境更易复现 | `.uv-python`、`.uv-cache` 和 `.venv` 不提交 Git |
| 2026-09-21 | 通过 `uv.toml` 固定仓库内缓存 | 用户级 uv cache 在受限环境中不可写 | 普通与受限执行环境使用同一缓存位置 |
| 2026-09-21 | IEEE-CIS 认证下载不阻塞 Stage 01 | 规则接受需要用户账户，工程管线可先使用合成数据 | 真实数据指标与合成数据指标必须分开 |
| 2026-09-21 | Docker Desktop 推迟到 Stage 05 结束前评估安装 | 当前核心开发不依赖容器，避免环境工作阻塞主线 | Stage 06 容器化前必须重新检查 |

## 计划变更

- 系统 Python 缺失，因此将 Python 运行时安装到仓库内；
- IEEE-CIS 本地文件尚未取得，Stage 01 初期允许使用合成数据契约；
- Docker、PostgreSQL 和 Neo4j 仍不可用，按总体计划分阶段接入。

## 阶段复盘

Stage 00 已完成最小开发环境、Git 仓库、依赖锁、smoke test、数据获取说明、合成后备方案和磁盘预算。当前环境足以进入 Stage 01。

遗留事项：

- 用户需要使用自己的 Kaggle 账户接受 IEEE-CIS 规则并下载数据；
- Stage 01 可先使用合成数据，但真实数据缺失状态必须保持可见；
- Docker Desktop 在 Stage 06 前重新评估；
- PostgreSQL 与 Neo4j 仍按后续阶段计划引入。

## 2026-09-22 复核

当前环境、锁文件、测试、磁盘、GPU、数据校验和 Git 忽略规则再次通过；完整记录见 [`reports/stage-00-02-recheck-2026-09-22.md`](../../reports/stage-00-02-recheck-2026-09-22.md)。IEEE-CIS 数据已到位，原下载遗留事项已关闭；Docker 等仍按后续阶段计划处理。
