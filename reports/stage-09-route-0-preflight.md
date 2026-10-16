# Stage09 路线0：前置门禁、版本冻结与实验合同

> 日期：2026-10-07
> 状态：PASS（路线0工程）；Stage09 In Progress，路线1～8尚未执行；L0本人自测独立
> 配套：[实验合同](../docs/data/stage-09-experiment-contract.md) · [机器合同](../configs/stage09_experiment.json)

## 结论与交付

已冻结161个实际配置/代码/数据/模型/政策文件，内容SHA256为883f8fc83ff8789f6247e77eb05057bc927d76704e334f9841ef354d21c808d3，check通过。实际服务五项ready、固定模型digest、新隔离数据库与全量回归通过。

新增机器合同、stage09_preflight模块、freeze_stage09 CLI和19项单元测试；配套合同与L0实作、阶段/总体文档已更新。19项包含在全量489内，不重复相加。

本次真实生成0、embedding请求0、训练0、付费0、已选择真实交易0。fake50不是30笔真实基线成绩；下一步路线1才选案例并调用生成。

## 实测门禁

| 检查 | 当期结果 | 证据 |
|---|---|---|
| 环境/锁 | Python3.11.16、仓库.venv；torch2.9.1+cu128、PyG2.8.0.post1、LangGraph1.2.12、psycopg3.3.6、pytest8.4.2；offline lock与locked sync通过 | 环境观察/同步 |
| 内容冻结 | 161文件；权重、comparison、manifest、history、数据/政策完整哈希核对 | frozen-inputs.json |
| 服务装配 | Stage06Service.from_config(configs/stage05.yaml)；data/risk_model/graph/policy_index/ollama全true | database-verified/runtime.json |
| Ollama | 0.5B生成与0.6Bembedding固定digest匹配；仅GET tags/ps，无推理 | database-verified/ollama.json |
| 起始资源 | 可用RAM3.1923GiB≥3、GPU空闲6144MiB/利用率0%；C/D/E可用6.5885/9.8029/52.6746GiB | database-verified/resources.json |
| 真库 | PostgreSQL17.11、pgvector0.8.6；新库graphrisk_stage09_route0_b7833f2c、受限角色s09_app_b7833f2c、16段索引核验 | database-verified/database.json |
| 根回归 | **489 passed、0 skipped、1 warning**，60.01s；18真库实际运行 | database-verified/root-pytest.log |
| 机制集 | **fake50/50、real0** | database-verified/stage06-mechanisms.json |
| 容器边界/清理 | 回环15434、E盘bind数据、secret不进env/log；普通down保留数据 | container-boundary.json / cleanup.log |

第一次无DSN根回归471 passed/18 skipped；后续新库489/0才补齐真库，不能把skip当passed。Starlette/AnyIO弃用警告不失败。Stage07冻结15探针本轮未重跑，历史15/15只作交接，不伪称新结果。

## 实际绑定与预算

主质量基线明确为configs/stage05.yaml + TF-IDF + 单worker内存 + qwen2.5:0.5b；PG候选只作补充，不能混在同一质量表。Prompt版本stage05-route5-extractive-v1、schema与构造代码内容哈希绑定，policy_as_of2026-07-01、relation_limit3。

实际stage05-route8比较绑定GraphSAGE权重SHA256 b1e4696dfca80c617ddeadf2fb2ae310b39a6e445fe9127865ea56901d26e69f、alpha0.5；batch256、max50、patience20、best6/stop26，验证/测试PR-AUC0.170545/0.085999。早期0.178343/0.103733属于另一轮；Stage02 champion与controlled XGBoost分别记录，分数未校准。

生成前冻结30个不同验证期交易，每层10、至少20笔真实生成尝试；重复/停止/失败分母分开。最多144次真实生成、0付费/托管fallback。六图候选、三个种子、新增GNN最多13次、单次3600s/合计21600s，预算接入执行由后续路线完成。起始资源检查不等于持续监测或OOM保证。旧test历史已观察，候选validation-only，最终最多一次固定批量复核，不称全新盲测。

## 保留的首次失败与恢复

1. Docker首次返回29.8.0，旧隔离栈image查询500，之后无法启动；正常restart120s失败，日志明确HCS/ERROR_NO_SYSTEM_RESOURCES，旧15433连接超时。首次部分观察/无DSN日志保留。
2. 正常start后Engine恢复，但当前设置显示C:\Users\12138\AppData\Local\Docker\wsl、镜像/容器为空；旧D:\docker2\DockerDesktopWSL\disk\docker_data.vhdx仍存在。本任务未编辑磁盘设置、迁移或删除旧VHDX，不能认定旧D项目镜像/卷已恢复。
3. 新临时pgvector容器的数据bind到E:\GraphRisk-Agent\artifacts\stage-09\route0-pgdata-20261007T093634Z、新secret/15434，不重建项目大镜像。镜像逻辑627,018,023字节约598MiB，镜像层仍写当前Docker的C盘目录。
4. 新库第一次检查脚本传None给embedding provider，check需模型元数据而AttributeError；这是检查脚本错误。用正确provider/digest且不请求embedding，再建新库复测通过；业务代码/阈值未改，原脚本/部分观察保留。

当前RAM起始线只略高于3GiB，每次重任务重新检查，不因为passed自动并行。全部失败在机器summary中分类记录，不改写首轮。

## 清理后的状态与磁盘限制

临时DB容器/网络已关闭，Engine29.8.0仍可连接；E盘数据/测试秘密/证据与镜像保留。结束C/D/E约6.59/9.80/52.66GiB；先前C约8.84GiB，未逐项测Docker VHDX/系统分页增量，不能把全部变化精确算作598MiB镜像。

后续大构建前应确认是否恢复Docker数据目录到D；路线1本地TF-IDF无需重建项目镜像。本报告不授权自动搬盘、全盘改权限或清理用户数据。

## 复核命令与学习

    Set-Location E:\GraphRisk-Agent
    . .\scripts\enter_project_env.ps1
    uv run --offline pytest tests/unit/test_stage09_preflight.py -q
    uv run --offline python scripts/freeze_stage09.py --check reports/generated/stage09/route0/20261007T093634Z/frozen-inputs.json

预期19 passed、check status passed/checked_files161。后续代码变动应先归档实验变更，不静默覆盖清单。真库489来自新库和进程DSN，普通未配置DSN仍会skip18；一次性probe有固定新目录/secret，不能原样重复覆盖。

L0本人对照早期报告/metadata/清单binding画版本链，解释验证选型、已观察test、真实分母；本人尚未完成，工程不代签。

## 证据

忽略目录reports/generated/stage09/route0/20261007T093634Z/包含frozen-inputs、初始资源/runtime/Ollama、无DSN日志、database-retry部分结果、database-verified最终结果、三个probe源码、compose/边界/cleanup与summary。摘要有文件SHA256及失败分类，不含秘密值。
