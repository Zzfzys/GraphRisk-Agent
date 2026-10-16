# Stage 09 路线 5A：Docker 补充复测（2026-10-08）

**当前版本已跑通 Docker 完整演示链路。** 根回归640项通过、0跳过；当前镜像、API、工作台和真实数据库正常。一笔从容器内新建的真实4B调查返回 `201 / verified`，报告存入SQL，错哈希被拦截、拒绝审批成功，移除测试观察器并重启API后，报告和拒绝状态仍能恢复。

本次发现并修复新版报告的数据库恢复缺陷，新增12项回归测试。新增真实模型生成1次、训练0、付费0；原45笔独立交易的质量结果不改写。这是演示和恢复验收，仍不是生产就绪、欺诈识别准确率或人工实用性合格的证明。本人学习、人工实用性复核和路线6仍待完成。

## 原来的测试为什么漏掉了问题

数据库按既定规则只保存派生报告、审计和审批，不保存临时调查对象 `graph_run`。路线5新增主评分决策校验，路线5A新增分析与证据校验，都依赖这个对象。刚生成时它在内存里，所以通过；从数据库重新读取时对象已经删除，便报 `model_decision_binding_mismatch`。

未修复代码上，原有真库回归628项通过、0跳过；随后把两份真实新版输出保存/恢复，2/2失败。旧真库测试使用旧报告合同，没有覆盖新路径。因此先用真实结果复现，再补新合同回归。

修复后先严格核验完整来源，再保存请求、版本、派生结果三个SHA256绑定。恢复时检查这些绑定和原报告/审批哈希，不再要求已删除的对象。新调查仍执行全部原始证据校验，内存模式不能用摘要跳过它。数据库不新增原始图、Prompt或标签；XGBoost/GraphSAGE权重、Prompt、评分规则与SQL schema不变。这是在受信任数据库边界内检查保存内容一致性，不是对抗可任意重写数据库的管理员的数字签名。

## 实测结果

| 检查 | 结果 | 验证范围 |
|---|---|---|
| 未修复的真实报告恢复 | 2/2失败 | 缺陷确实存在，失败证据保留 |
| 修复后真实报告恢复 | 2/2通过 | 完整与图缺口报告，分析不变 |
| 根回归，真实Docker数据库DSN | **640 passed、0 skipped、1 warning，80.50秒** | 原有18项及新增6项真库集成实际执行，另有6项新增单元测试 |
| 当前源码镜像构建 | 成功 | Python3.11、uv.lock；容器安装的修复源码哈希与登记一致 |
| API、工作台、数据库 | 全部healthy | ready六项依赖为true，工作台health返回200 |
| 容器内新调查 | **201 / verified，实际4B生成1次** | 真工具、真政策检索、真本地模型、真SQL；不是注入旧报告 |
| 新报告错哈希、拒绝审批 | 409、200 | report绑定有效，外部动作0 |
| 去除观察器并重启API | 新报告GET200，rejected保留 | 派生报告内容和审批终态不变，不重新推理 |
| 已封存两份报告与新报告读取 | 3/3返回200 | 两份完整/一份图缺口；图缺口审批仍不可用，返回409 |
| Linux容器内AppTest | 3/3通过 | 工作台通过真实API HTTP显示线索、缺口、出处和刷新；没有传输替代，没有浏览器视觉验收 |
| 权限与保存边界 | 通过 | 独立应用库；账号非superuser、不能建库/建角色；SQL不存原始graph_run |
| 原质量证据复查 | 通过 | 原48条重新评分、首轮15条保留；185输入、248注册文件、215历史闭环文件核对 |

新增测试覆盖旧合同兼容、新主评分/质量合同、图不可用、跨实例恢复后拒绝、缺少绑定拒绝恢复、请求/版本/审计/分析篡改。篡改分析后即使重算报告哈希，也不能通过保存的来源绑定。部分证据不会因为恢复而自动升级。

新增测试最初有fixture导入及参数错误；HTTP检查脚本也曾错误要求部分证据暴露审批哈希，均已修正并保留失败日志。最终以640项通过及上述真实容器证据为准；一个现有Starlette弃用警告不影响结果。

## 一笔真实生成与资源恢复

构建后初次检查RAM仅2.078GiB，低于3GiB门禁，未发送生成。正常停止本次三个测试容器、重启Docker Desktop后，同一镜像和数据库恢复正常，内存回升到约4GiB，才预先登记一次烟测合同。

烟测沿用已有开发交易dev-01，不添加新的独立交易。配置仍为qwen3:4b、固定digest、temperature0、最多512输出Token、think=false、keep_alive=0；最多一次生成、零重试。测试专用入口只观察真实Ollama POST：发送前等待宿主完成资源检查及原全局账本持久登记，之后保存真实请求和响应，不更换回答。观察器只在这次私有测试入口中使用，验收后已恢复正常入口。

实际API调查约12.16秒；输入/输出1336/117 Token。25次资源采样最低RAM3.2319GiB，资源门禁未触发，启动与发送前空闲显存/磁盘检查通过。原始模型判断独立对照冻结工具状态通过，公开分析草稿与原始回答一致；来源、模型版本、引用和审批仍由真实服务核验。这一例证明容器链路能执行，不作为新冻结集准确率，也不参与原30/10案的耗时统计。

真实生成账本新增sequence130，phase为 `route5a_docker_recheck`。该名称只用于区分集成烟测；核验器同时限制“原路线5A63次＋本次1次”不超过原66次上限，不允许换phase扩预算。当前路线5A合计64/66，Stage09累计130/144。原native summary仍记录该轮结束时63/129，不覆盖；当前额外一次单独记账。没有隐藏预热、额外重试、新训练、下载模型或付费调用。

## 环境与私有证据

- 镜像 `graphrisk-stage09-quality-recheck:20261008`，ID `sha256:bde315eaf07e7d895a561195bc97fa6281d9e3edd0272e8a016a5104c5b6d42b`；约6.27GiB，使用现有D盘Docker存储。最后记录C/D/E空闲约18.09/30.04/50.21GiB，未迁移或清理系统目录。
- PostgreSQL17.11 / pgvector镜像；独立容器 `graphrisk-stage09-5a-recheck-db-20261008`、独立卷，host仅127.0.0.1:15435。回归库与应用演示库分开，不修改原有数据库。API/工作台仅127.0.0.1:8000/8501。
- 密码在`.secrets/stage09/`，挂载给受限应用，不进镜像、日志或公开文档。私有Compose绑定本机数据和模型只读，输出只写本轮专用目录。
- 证据位于 `reports/generated/stage09/route5a/docker-recheck-20261008/`：修复前/后恢复、最终回归、镜像、HTTP、UI、权限、初次资源门禁、一次生成合同、25次资源采样、原始Ollama HTTP、新报告及审批重启证据、独立质量重评、补充acceptance；原失败与最终结果并存。

重点看 `fresh-investigation.json`、`container-output/ollama-http.json`、`fresh-generation-resources.json`、`fresh-approval-before.json`、`fresh-approval-after.json`、`regression.json`、`container-http-after.json`、`container-ui.json`、`native-lineage-check.json` 和 `acceptance.json`。完整交易/Prompt只留在Git忽略目录。`generation-resource-gate.json`是最初内存不足的历史记录，不能拿它否定后来在门禁通过后完成的一次烟测。

## 保留冻结结果与复查

修复前源文件和验收文档按原SHA保存在 `artifacts/stage-09/route5a/20261008-quality-v2/source-before-docker-repair/`。原v1/v2的注册、输入、原始HTTP、模型输出、summary和验收JSON不修改；仅两处服务源码变化单独登记，文档随之更新。Docker证据单独封存并绑定原验收，不改写成当时做过Docker验证。

原 `scripts/evaluate_stage09_quality.py check` 会严格拒绝当前源码漂移，这是预期保护。新只读核验只允许登记的两处源码变化，要求原文件与封存哈希匹配，其余冻结输入必须仍匹配；使用原评分器重新检查原48条，原63次与新增1次分别核对，同时检查总预算、真实HTTP/Token、原始受限判断及资源门禁，不改写原summary。

~~~powershell
. ./scripts/enter_project_env.ps1
uv run --offline python scripts/check_stage09_quality_docker_recheck.py
uv run --offline python reports/generated/stage09/route5a/docker-recheck-20261008/run_database_regression.py
~~~

第二条需独立数据库已启动，DSN只注入子进程。普通pytest没有DSN仍会跳过真库项，不能称真库通过。不得重跑 `run_one_container_generation.py`，脚本会拒绝已开始/已完成烟测；未知请求结果须先审计，不自动重试。

测试栈保留运行供查看工作台；停止时保留镜像、数据库和卷：

~~~powershell
. ./scripts/enter_project_env.ps1
docker compose --project-directory E:/GraphRisk-Agent -f reports/generated/stage09/route5a/docker-recheck-20261008/compose.yaml stop
docker stop graphrisk-stage09-5a-recheck-db-20261008
~~~

重新启动：

~~~powershell
. ./scripts/enter_project_env.ps1
docker start graphrisk-stage09-5a-recheck-db-20261008
docker compose --project-directory E:/GraphRisk-Agent -f reports/generated/stage09/route5a/docker-recheck-20261008/compose.yaml up -d --wait --wait-timeout 180
~~~

这是显式Stage09补充栈，不修改旧Stage07默认Compose。工作台读取/刷新已有报告不生成；创建新调查会新增本地生成，需要先满足资源与剩余预算约束。人工鉴权、多worker、生产备份体系仍是既有后续边界，不能因本轮通过而称生产就绪。
