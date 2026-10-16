# Docker 恢复与 Stage 01～08 完整复测（2026-10-07）

结论：**Docker 已恢复，Stage01～08 本轮工程回归与所选端到端验收全部通过。** 根项目 470 passed / 0 skipped；18 项真实数据库测试全部运行，Stage07 冻结探针 15/15。此前本日阻塞的数据库和容器门禁已在新隔离环境补齐。

## 恢复情况

真实 Docker Engine 为 29.8.0，磁盘目录为 D:\docker2\DockerDesktopWSL，当前用户已有修改权限。本次未再次改权限或迁移磁盘。镜像/容器列表起初为空，已拉取仓库固定的 pgvector 镜像，重建 Stage06/07 镜像并正常重启 Docker Desktop。

独立 Compose 项目 graphrisk-recovery-20261007 使用新随机凭据和独立数据卷。根回归和冻结探针分别使用全新数据库，未复用演示库；凭据保存在 Git 忽略的 .secrets/docker-recovery-20261007，不进入公开日志。初始化脚本将临时文件、Python 缓存置于 E 盘，并追加进程本地 NO_PROXY 地址、保留原有条目。系统代理、分页文件和 WSL 全局配置未修改。

## 最终结果

| 范围 | 本轮证据 | 结果 |
|---|---|---|
| 主项目 Stage01～08 | 470 passed、0 skipped、1 warning，55.99 秒；18 项真实数据库测试实际运行 | 通过 |
| Stage01 数据 | 合成 3500/750/750；IEEE-CIS 413378/88581/88581，ID 唯一、跨 split 无重复、时间范围有序 | 通过 |
| Stage02 模型/HTTP | 既有 XGBoost champion 加载；health/model/info/risk/predict 正常，未知交易 404、非法请求 422 | 通过，未训练 |
| Stage03～04 工具/图模型 | 真实交易调查 verified，GraphSAGE 图状态 ok，有图证据与政策引用；根回归覆盖图/融合合同 | 通过，未调参 |
| Stage05 | 受控机制案例 17/17；真实本地 HTTP 调查及审批拒绝成功 | 通过 |
| Stage06 | 受控机制案例 50/50；独立容器 API/UI 健康，真实调查、读回、审批拒绝、重复审批及冲突 409 | 通过 |
| Stage07 数据库 | 18 项真库测试全部通过；另一全新库冻结探针 15/15、0 not_run | 通过 |
| Stage07 容器恢复 | API 重建后 pending/哈希保留；DB 停止时 ready/读取/创建均 503；DB 恢复及完整 Compose down/up 后同一哈希、rejected 状态保留 | 通过 |
| Stage08 独立 GraphRAG | 核心 94 项：91 passed、3 UI 在核心环境跳过；独立 demo 环境 UI 3/3，全部 94 项均覆盖 | 通过 |
| Stage08 真实问答 | hybrid 两跳答案 team-security、带引用；未知实体拒答 | 通过 |
| Stage08 冻结/账单 | 原 90 文件哈希/重评分一致，text/graph/hybrid 16/22、18/22、18/22；40 请求小时账单对应实扣 0.0283512 元 | 通过，未新增付费调用 |
| 依赖/分发包 | 三个锁文件离线检查；四份 wheel/sdist 重建，排除 secret、受限数据/模型、环境缓存 | 通过 |
| 镜像/运行边界 | 两个镜像无凭据/IEEE-CIS 工件；API 数据只读、仅应用 secret；UI 无数据/secret 挂载；回环端口；日志无本次四个密码原值或 PostgreSQL URL | 通过，有限样本 |

## 保留的失败与修正

1. 首次并行构建/回归期间 Docker/WSL 引擎中断，构建 EOF、数据库退出 255，根回归为 462 passed / 8 failed，失败均为 persistence_unavailable。OOMKilled=false，根因不能单独断定是 OOM。顺序执行后根 470/470、两个镜像和容器验收通过。
2. 第一个新探针库遗漏导入固定 16 段政策索引；补齐后换新库重跑。
3. 后续探针为 14/15，本机 Ollama /api/tags 被继承代理返回 502。项目终端加入本地 NO_PROXY 后，正常 HTTPX 请求返回 200，新库 15/15。不修改应用检索逻辑或门槛。
4. 检查脚本曾错误地向 Stage06 内存模式发送持久化 Idempotency-Key，系统正确返回 422；按现有合同修正检查请求后通过。完整栈重建加入 Compose 健康等待，避免启动初期检查过早。

首次失败日志、14/15 原观测和检查脚本首次状态均保留。Starlette/AnyIO 弃用警告及 AppTest bare-mode 警告未造成断言失败。

## 当前状态与复现

隔离测试栈已用普通 down 关闭释放内存。**Docker 引擎保持可连接，镜像和数据库卷保留。** 当前没有在 18000/18501 监听的测试服务，需要启动后再访问。

启动本次已验收的 Stage07 隔离演示，复用保留的卷及本地 secret：

    Set-Location E:\GraphRisk-Agent
    . .\scripts\enter_project_env.ps1
    docker version --format '{{.Server.Version}}'
    docker compose -p graphrisk-recovery-20261007 -f compose.stage07.yaml -f compose.stage07-debug.yaml -f reports/generated/stage08/docker-recheck-20261007/compose.override.yaml up -d --no-build --pull never --wait --wait-timeout 240
    Invoke-RestMethod http://127.0.0.1:18000/api/v1/ready

工作台：http://127.0.0.1:18501 。该组合显式启用隔离库本机 15433 调试端口，默认 compose.stage07.yaml 没有数据库宿主端口。结束后使用相同文件组合执行 down，保留卷，不加 -v。

普通 uv run --offline pytest -q 未注入隔离 DSN 时仍会跳过 18 项真实数据库测试。此次 470/470 来自新库和进程环境中的迁移/应用 DSN。生成的 run_regressions_sequential.py、run_remaining.py 可追溯准备、迁移、授权、索引和脱敏运行过程；一次性脚本有固定证据输出，不能原样重复覆盖首次观测。新复测应使用新目录和新隔离库。

## 资源与质量边界

构建时 Windows 可用物理内存一度约 230 MiB，系统分页文件由约 9.5 GiB 增至 12 GiB，本次未删除或禁用。关闭容器时磁盘剩余约 C 6.76、D 9.71、E 52.87 GiB。镜像/构建缓存/数据库卷在 D 盘，项目缓存/报告在 E 盘；大型构建仍可能引起系统分页，不能承诺 C 盘零写入。

17/50 机制案例及多数冻结探针使用受控 provider，不能当真实模型准确率。本轮三次 GraphRisk 真实调查（本地 HTTP、Stage06 容器、Stage07 容器）均使用同一笔 held-out 交易 3488959，尚无多交易真实 Agent 质量基线。GraphSAGE 可加载/推理不证明参数充分或优于 XGBoost。90 条 GraphRAG 只复评分，未重新生成。长期并发重负载稳定性未由本轮短期顺序验收证明。

Stage01～08 工程状态保持 Done；Stage09 开头先补多交易真实本地 Agent 基线，再安排 GraphSAGE 实现、采样、特征消融及调参诊断。新增付费调用 0、训练 0，未自动开始 Stage09。

## 证据

[机器摘要](generated/stage08/docker-recheck-20261007/summary.json)。完整忽略目录：reports/generated/stage08/docker-recheck-20261007/。关键文件包括 database-rerun/root-pytest.log、remaining-checks/stage07-v2.json、graphrag-core.log、remaining-checks/graphrag-ui.log、data-http-recheck.json、stage6-container-summary.json、stage7-container-summary.json、三个 boundary.json、graphrag-frozen-rescore.json、billing.log、镜像构建日志及 cleanup.log。摘要记录证据 SHA256，不含凭据。
