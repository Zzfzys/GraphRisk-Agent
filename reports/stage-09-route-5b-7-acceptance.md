# Stage09 5B路线7：端到端验收与复盘

状态：**5B路线0～7工程完成，2026-10-15。增强基线质量Pass，当前GNN增量Fail/不晋级，LB0～LB7本人学习Pending。** Stage09整体仍In Progress，原路线6～8尚未完成。

用简单的话说：这轮把之前的实验结果、网站接口、数据库保存、人工审批和容器运行连起来检查了一遍。系统能按冻结模型给出结果，能保存并恢复报告，也能在依赖断开时明确报错；这些工程结果没有把GraphSAGE变成更好的模型，也没有增加真实LLM质量成绩。

## 本轮实际验收

| 项目 | 实测 | 说明 |
|---|---|---|
| 完整当前测试集 | **806 passed、1 skipped、10 warnings，85.72秒** | 使用本轮新隔离DSN，非沿用历史通过数 |
| 其中真实数据库 | **24/24执行通过，0跳过** | PostgreSQL、pgvector、现代报告/审批恢复 |
| 新增总验收器测试 | **11 passed，9.01秒** | 新增于完整回归之后，单独记录，不冒充第二次全量运行 |
| 特征、基线、条件增强、确认原生核验 | 全部通过 | 590,540行特征、7个表格对照、5组条件实验、9图+3树确认均可追踪 |
| 同构扩量历史核验 | 9组真实完成与文件/账本绑定通过 | 使用原生verify_plan与完整封存，不伪造仍处于开发期的账本状态 |
| 原覆盖交付复核 | 177,162笔通过 | 继承5案真权重/3重复及旧33份记录证据，没有重训或重开标签批次 |
| 容器调查 | 2笔HTTP201/verified | 分别验证fold2/fold3；真实交易/模型分数/关系/政策，报告生成用明确标记的确定性夹具 |
| 审批 | 错hash409，人工拒绝200 | 未执行外部动作 |
| API进程重启 | 2/2报告、hash、版本、rejected状态不变 | 从真实运行库恢复，无重新生成 |
| 工作台 | 2/2 AppTest通过 | Linux容器通过真实API HTTP读取，未替换传输；显示“仅关系/无独立分数” |
| 数据库临时断开 | ready503、报告GET503/persistence_unavailable | 本轮独立库；恢复后2/2报告可读，无重新生成 |
| 容器Linux新事件真权重 | 1案通过 | 树分数与封存Windows结果差0，图均值差1.086543e-8≤1e-6；不读标签、不写历史 |
| 数据边界 | 通过 | 有限应用角色、模型/数据只读；SQL不保存原始graph_run/Prompt/raw_features/标签 |
| 健康状态 | API/工作台/DB均healthy，无OOMKilled | 取样证据保存；最终停止本轮栈释放内存，不删容器/卷 |
| 本轮真实数据fit/源评分标签批次/真实生成/付费调用 | 全部0 | 两次确定性生成夹具单独记录，不加入真实Agent质量分母 |

唯一skip是Windows不允许创建符号链接的既有测试。10条warning为Starlette弃用别名及小夹具All-NaN统计；没有用skip掩盖数据库未执行。新增验收器的11项测试能拒绝文件/账本漂移、隐藏生成费用、伪造学习完成、缺失条件及路径/秘密越界。

完整回归范围是主项目根tests目录；本轮没有改动或重跑独立GraphRAG包/UI环境，不将其算作新增回归证据。

## 实验结论没有改变

只有一份IEEE-CIS标注交易来源。三折外层合计150,588笔，训练窗口重叠，外层窗口互不重叠；九个种子结果不是九份独立数据。

| 结果 | 冻结数值 | 判定 |
|---|---:|---|
| 增强XGB三折ROC均值 | 0.905089，最差0.887805 | 基础≥0.80通过，均值0.90探索达到 |
| 增强XGB三折AP均值 | 0.544367 | 固定外层确认，不是准确率 |
| 三折融合alpha | 1/1/1 | 完全使用XGB |
| 融合AP增量、正收益折 | 0、0/3 | GNN不晋级 |
| 两项增量95%区间 | [0,0] | 未取得稳定增量 |
| 历史test XGB AP/ROC | 0.537789/0.903670 | 88,581笔描述性复核，历史已观察，不是新盲测 |

当前GraphSAGE是预算内审计/扩量/消融后的实现，不等于已经调到理论最优。部分运行最佳轮次触及25轮上限；宽表与图输入信息不同，不能把全部差距归于架构。路线4的单折关系收益未转为路线5简单融合的稳定收益，因此保留关系/实验模型、退出主评分。异构模型Deferred，不在已见外层后继续救分。

## 候选、失败与预算

共享账本仍为38次尝试，33完成、5个旧失败；没有重置或退款：

| 家族 | 尝试/完成/失败 | 已计秒数 |
|---|---|---:|
| GNN | 26/23/3 | 18,941.923335 |
| XGBoost | 11/9/2 | 8,439.153316 |
| MLP | 1/1/0 | 54.666261 |

外层评分释放1批、历史test释放1批；本轮前后训练账本与原生成账本逐字节一致。账面剩余额度不代表允许在已观察外层/test后重新挑模型。

部分早期检查器只允许开发阶段，原图路线3的完整check会拒绝已经发生最终标签释放的账本。本轮没有伪造旧状态或关闭该保护，而是核对归档源码、所有闭包文件、各真实完成任务与当前共享账本，再执行可用于最终阶段的原生确认/覆盖检查。不同阶段的状态字段应按当时语义解释。

## 容器、资源与本轮修正

没有下载或重建镜像；使用本机缓存运行时sha256:bde315eaf07e7d895a561195bc97fa6281d9e3edd0272e8a016a5104c5b6d42b，只读覆盖当前工作树源码，代码与运行时分别绑定。容器Python3.11.14，本机3.11.16，均满足3.11约束；实际API路径为/code/api/main.py，已核对加载文件hash。不能把源码覆盖运行称为新镜像构建验收。

新Compose项目graphrisk-5b-acceptance-20261015使用独立测试库/运行库、仅本机端口、独立密码。旧演示数据库和旧Compose未修改。DB上限384MiB、API容器总量2304MiB、工作台384MiB；API上限是主/子进程总额，区别于路线6单进程1280MiB。记录为阶段开始资源检查、容器取样和OOM状态，不宣称测得完整进程树峰值或生产SLO。

遇到并修正的验收搭建问题：

1. 内部DB网络发布端口为0：增加仅本机调试网络及明确空闲端口，不改旧栈。
2. 向只读配置目录嵌套挂载一个不存在的文件：改为新建完整配置目录、一次只读挂载，原失败配置和日志保留。
3. Windows默认GBK读取带中文的UTF-8报告：读取明确指定UTF-8，再继续断库测试，未重复生成。

这些是验收脚本/拓扑修正，没有改训练、模型决定、业务评分或旧数据库schema。早期JSON峰值、SQL OOM、日历和旧API误导入的失败仍在路线6证据中保留。

## 产物、复现与停止

私有证据：E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261015-acceptance-v1/。包含登记/账本前状态、完整pytest文本/JUnit、11项检查器测试、原生artifact-audit、Compose及修正记录、容器HTTP/恢复/UI/新事件结果、健康/资源和最终acceptance。私有密码只在E盘.secrets目录，不写入公开说明或连接串。

默认只读核验不训练、不预测、不调用LLM，也不要求Docker保持运行：

```powershell
. E:\GraphRisk-Agent\scripts\enter_project_env.ps1
$stage09Source = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$env:PYTHONPATH = "$stage09Source\src;$stage09Source"
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location E:\GraphRisk-Agent
uv run --offline --no-sync python "$stage09Source\scripts\check_stage09_5b.py"
```

预期工程done、基线passed、GNN failed_no_promotion、本人Pending；复核模型/覆盖、封存文件、源归档及两个账本。不提供隐式重训或真实LLM复现开关。

实际新进程只读核验已通过：530份证据/源码文件、原生三折确认和177,162笔覆盖均通过。最终验收SHA为4108c05116001c793733ad0f3f32762a620d2894d0c5aac364bb1f40fa7c33d8。另完成7份新增Python源码语法和8份核心文档链接核验，无缺失链接，git diff --check通过。封存快照保留原发布文本，本段为随后只读核验结果，不改写原封存产物。

需要重新启动本轮**夹具报告验收栈**时，保留当前私有目录并执行：

```powershell
uv run --offline --no-sync python "$stage09Source\scripts\run_stage09_5b_acceptance.py" container-up
docker compose --project-directory E:/GraphRisk-Agent -p graphrisk-5b-acceptance-20261015 -f reports/generated/stage09/route5b/20261015-acceptance-v1/compose.yaml port api 8000
docker compose --project-directory E:/GraphRisk-Agent -p graphrisk-5b-acceptance-20261015 -f reports/generated/stage09/route5b/20261015-acceptance-v1/compose.yaml port workbench 8501
```

读取已有报告不生成；不要重跑setup/seed改写已封存结果。本轮验收栈不是新的真实LLM质量评估环境。停止只用：

```powershell
uv run --offline --no-sync python "$stage09Source\scripts\run_stage09_5b_acceptance.py" stop
```

不执行down -v。隔离库与容器保留以便复查，测试夹具/私有快照不公开。真实本地LLM演示沿[路线6启动说明](stage-09-route-5b-6-coverage.md)显式选择新配置；开始新调查会运行LLM，不是只读check。

## 交接与本人复盘

新增[模型卡](../docs/data/stage-09-5b-model-card.md)、[数据卡](../docs/data/stage-09-5b-data-card.md)、[90分钟个人复盘模板](../docs/data/stage-09-5b-review-template.md)。材料就绪不代表本人学完；LB0～LB7仍Pending。

下一步是**Stage09原路线6：分层回归与CI**，接收当前源码/锁、时间/版本/引用/恢复合同、24项隔离真库测试及默认只读核验。CI不可自动下载IEEE数据、训练大模型、调用本地/付费LLM或依赖本机已缓存环境；远程job未运行不能写远程通过。

原路线7仍需可观测性/漂移及独立备份恢复，原路线8仍需跨Stage09总验收。单worker、未认证复核员、无外部处置、匿名字段时点假设、未校准、历史test已观察、持续追加图索引/正式负载/生产备份等限制交Stage10。此次API/DB重启和断开恢复不等于完成备份恢复或生产灾备。
