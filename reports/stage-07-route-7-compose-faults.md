# Stage 07 路线 7：Compose、故障注入与数据边界

> 工程门禁：通过（2026-09-29）。Stage 07 总验收仍待路线 8；个人 L7 自测未代签。操作步骤见[Compose 指南](../docs/data/stage-07-compose.md)。

## 部署与真实调查

独立的 `compose.stage07.yaml` 部署 pgvector/PostgreSQL、一次性 `bootstrap`、FastAPI、Streamlit；宿主 Ollama 由 `host.docker.internal:11434` 提供生成和 embedding。Stage 06 `compose.yaml` 保持原路径。`docker compose -f compose.stage07.yaml up -d --build` 成功，bootstrap 退出码 0，迁移/索引导入后为 schema v1、16 条固定政策向量。API `/api/v1/ready` 返回 `ready`，数据、风险模型、图、政策索引、Ollama、PostgreSQL 均为 true；工作台健康检查和根页面均为 HTTP 200。DB 默认无宿主端口；API/UI 分别仅在 `127.0.0.1:8000/8501`。可选 `compose.stage07-debug.yaml` 只在 `127.0.0.1:15433` 发布数据库端口。两组 Compose 配置语法检查通过。

真实 IEEE-CIS held-out 交易 `3488959`（政策日期 `2026-07-01`、关系上限 3）创建运行 `run-594f2727dc14-eb859caf7d1f4302be2131a539d0ab41`，会话 `session-run-594f2727dc14-eb859caf7d1f4302be2131a539d0ab41`，请求 ID `req-baabf02a0648408f8e46da53e19121fe`。报告 SHA-256 为 `4f0b3c97baaccca396487dddd87db477c41d1285172abd5e35eade55f3b6ac34`，状态 `verified/pending`，政策片段 `policy-1cd398a94233baa2ed38d318`。XGBoost 分数 `0.19668689370155334`、GraphSAGE 分数 `0.2969105839729309`；严格历史邻居数为 0，未编造关系。外部动作 0。窄字段原始观测保存在忽略目录 `reports/generated/stage07/route7-compose-state.json`。

## 故障矩阵

| 操作 | 期望 | 实测 |
|---|---|---|
| `up -d --no-deps --force-recreate api` | 旧运行可跨 API 容器恢复 | GET 同一 `run_id` 为 HTTP 200、`verified/pending`、同报告哈希 |
| 同一绑定载荷先拒绝、再重复拒绝、再批准 | 幂等决定；冲突拒绝 | 首次和重复拒绝均 HTTP 200 `rejected`；相反批准 HTTP 409 `approval_conflict`；GET 保持 `rejected`、同哈希、外部动作 0 |
| `stop db` 后请求 ready、旧运行 GET、新调查 POST | 不假装持久化可用，不退回内存 | ready HTTP 503 且 `postgres=false`；GET/POST 均 HTTP 503 `persistence_unavailable` |
| `start db` | 旧运行继续可读 | ready HTTP 200 且 `postgres=true`；GET 同一运行仍为 `rejected`、同哈希 |
| `down` 后 `up -d --no-build`（不加 `-v`） | 卷保存运行、审批、政策 | bootstrap 可重复执行；同一运行/哈希/拒绝状态、UI HTTP 200；数据库为 schema v1、向量 16、运行 1、审批 1 |

这些操作使用真实容器、真实 pgvector 库和宿主 Ollama。单项观测分别保存在忽略目录 `route7-approval-recovery.json`、`route7-db-down.json`、`route7-db-recovered.json`、`route7-compose-restarted.json`（均在 `reports/generated/stage07/`）。本次没有执行 `down -v`，因为那会删除持久卷而不是模拟普通重启。

## 权限与数据边界

- 应用数据库角色实测 `superuser=false`、`createdb=false`、schema `CREATE=false`、政策向量 `SELECT=true`、`INSERT=false`。管理员 secret 只挂在 DB/bootstrap；API 只挂应用 secret；工作台无 secret、无数据挂载。
- API 的特征数据、模型、图、政策挂载为只读，生成报告挂载可写；DB 使用独立命名卷。镜像内检查未发现 `.secrets`、`data/raw`、`data/interim`、`data/processed`、模型工件或生成报告；镜像环境变量没有 DSN 或密码。构建包仅含数据目录的 `.gitkeep` 占位文件。Git 忽略规则覆盖本地 secret。
- API 公共响应的字段扫描未发现 `isFraud`、`identity_key`、embedding/vector、密码、原始交易行、`card1`、`addr1`。容器日志按本机两个 secret 值与 `postgresql://` 扫描均未命中；扫描只证明本次输出边界，不代表任意未来日志自动安全。

## 回归与范围

`uv run --offline pytest -q`：**339 passed，1 条 Starlette 弃用警告**，58.61 秒。`uv build --offline`、`git diff --check` 与 Compose 配置语法检查通过。路线 7 单元测试覆盖宿主 Ollama 地址限制、镜像/网络/挂载/secret 拓扑、调试端口及含特殊字符密码的 DSN 构造。Stage 07 并未增加真实复核员认证，也没有实现封禁、拒付或外部通知；分数未校准，固定 held-out 样本不能代表总体准确率。最终跨路线验收、Stage 06 默认路径复核与个人学习复盘留在路线 8。
