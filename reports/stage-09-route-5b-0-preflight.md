# Stage09 5B 路线0：前置门禁、版本冻结与新实验合同

**工程准备完成，2026-10-09。** 本次把“用什么数据、按什么时间训练、怎样比较、最多花多少训练额度”固定下来，并核对电脑和旧产物能支持下一步。尚未开始增强特征、模型训练或质量评估；GraphSAGE的旧效果没有改变。

可以进入路线1：原始特征与时间正确管线。本人LB0学习仍待完成，不由工程测试代签。

## 这一步实际做了什么

1. 确认原始IEEE-CIS文件、canonical及三份切分的ID/time一致，未读取新的欺诈标签分布或计算指标。
2. 核对旧5A质量结果与Docker补充验收，确认历史原始输出、权重和预算没有被覆盖。
3. 建立独立5B机器合同、三时间折、参数候选顺序、资源门禁及预算账本；失败、崩溃、超时、重复访问与改目录重置预算均有保护。
4. 使用E盘现有环境和数据，执行当前C盘工作树代码；新增私有实验证据与测试临时数据留在E盘。没有安装第二套依赖、复制大数据/权重到C盘或改变系统环境。

交付：[机器合同](../configs/stage09_model_enhancement.json)、[合同说明](../docs/data/stage-09-model-enhancement-contract.md)、[登记/检查入口](../scripts/preflight_stage09_model_enhancement.py)、[回归验证入口](../scripts/verify_stage09_model_enhancement.py)。

## 真实输入和版本

| 项目 | 本次核验结果 |
|---|---|
| 原交易CSV | 590,540笔、394列、683,351,067字节 |
| 原identity CSV | 144,233笔、41列、26,529,680字节；ID唯一且属于交易源 |
| 原train / validation / test | 413,378 / 88,581 / 88,581；不重叠、完整覆盖、时间严格有序 |
| 新development | 旧train+validation，共501,959笔；旧test隔离于候选搜索 |
| Python / uv | E盘Python3.11.16、uv0.12.18；锁文件内容一致 |
| 依赖检查 | `uv sync --locked --offline --check --group demo`成功，147个安装项无需改变 |
| 已安装关键包 | DuckDB1.5.5、PyTorch2.9.1+cu128、PyG2.8.0.post1、XGBoost3.2.0、pytest8.4.2 |
| 主评分 | Stage02 `xgboost-v1`；`xgboost_only`保持 |
| 实验图模型 | `graphsage-stage09-candidate-4`，28维，权重SHA `fee18ce1…c34c9`；仍experimental |
| 质量配置/政策 | 明确绑定`stage09_quality_4b.yaml`的qwen3:4b及digest；现有TF-IDF政策索引/语料版本已绑定 |
| 历史5A复查 | 185输入、248注册文件、215原闭环文件、48条输出重新评分、66份Docker闭环文件通过；首轮15条保留 |
| 旧Stage09生成预算 | 130/144，路线5A合计64/66，均不变 |

本次验证的是文件与历史证据绑定，没有新建调查、连接数据库运行回归、重建镜像或验证当前网页状态。640项真库根回归属于2026-10-08历史证据，不写成本次新测试成绩。

最初字节比较显示C/E锁文件不同，TOML解析与Git比较确认只是LF/CRLF换行；依赖定义并未变化。两处原始字节哈希仍分别保留，不静默把字节差异改成相等。

## 冻结的三组窗口

窗口按相对交易时间排序划分，等时事件不拆开；2000年的日期只是适配器锚点。每段保存rank、起止相对秒、ID哈希和行数。

| 时间折 | 训练 | 内层早停 | 内层融合校准 | 外层确认 |
|---|---:|---:|---:|---:|
| fold-1 | 250,979 | 25,098 | 25,098 | 50,196 |
| fold-2 | 326,273 | 25,098 | 25,098 | 50,196 |
| fold-3 | 401,567 | 25,098 | 25,098 | 50,196 |

候选搜索只用fold-1。先冻结最终配置，再运行三个时间折×三个种子，最后统一看外层结果。后续折可用此前发生的标签训练，但只能执行冻结规则，不能用前折成绩再选模型。训练期有重叠，这不是三个独立数据集；旧数据及指标已经历史观察，不能称全新盲测。

内层两段分别用于早停与融合校准，避免用同一批标签既选择最佳轮数又调整融合。旧test最多一次冻结后描述性复核。当前外层与test访问次数均为0。

## 新预算和停止规则

| 预算 | 上限 | 当前消耗 |
|---|---|---:|
| GNN fit | 30次；累计12h、每次60min | 0次 / 0秒 |
| XGBoost fit | 12次、每次60min | 0次 |
| 无图MLP fit | 3次、每次60min | 0次 |
| 本路线生成 / 付费调用 | 0 / 0 | 0 / 0 |

GNN预分配3规模、6参数、9条件增强、9最终确认、3失败恢复。最终确认还预留最多9h墙钟，开发最多先使用余下3h。确认实际耗时更少时可以释放时间，训练次数不退款。候选组合、候选顺序、三种子及原28维mean两层结构在机器合同中固定。

训练前先落账；失败扣次数与实际时间；未决崩溃阻止后续工作，审计标abandoned按整次cap扣账。换源码版本继续使用第一份账本，不能开新目录得到另一套30次额度。超时接口和资源检查必须由后续训练runner接入；本路线没有执行真实训练来验证其峰值。

质量门槛保留：增强XGB三折平均ROC最低0.80、0.90探索；融合同集合AP绝对+0.005、Recall@Top1%相对+5%，至少两折AP正收益，三折×三种子并报告配对时间块bootstrap区间。预算/源码校验不是模型已达标的证据。

## 资源实测

最终登记时（2026-10-09 12:03香港时间）：

| 资源 | 观测 | 最低线 | 结果 |
|---|---:|---:|---|
| 可用RAM | 5.82GiB | 3GiB | 通过 |
| 空闲显存 | 5,955MiB | 4,096MiB | 通过 |
| 空闲GPU占用 | 0% | ≤10% | 通过 |
| C / D / E空闲磁盘 | 18.36 / 30.01 / 50.22GiB | 5 / 5 / 10GiB | 通过 |

这是当时启动门禁，不保证全量宽表、图缓存或训练峰值都能容纳。后续每次大任务前重测，发现不足先停止。没有关闭用户应用、删除卷或修改全局临时目录。

## 测试与验收

最终有针对性的回归：**96 passed、1 skipped、1 warning，11.55秒**。含31项新测试（30通过、1跳过）及66项旧合同/预算/部署测试。跳过项是实际符号链接fixture，Windows不允许创建；基础路径逃逸和秘密路径拒绝测试已执行。一个现有Starlette弃用警告。没有新数据库/容器/HTTP质量测试。

测试覆盖确认额度保护、GNN墙钟保护、候选顺序、早停/校准时间隔离、等时边界、错误ID/time、同大小源码修改、失败/崩溃计数、错误恢复种子、重复结果访问、旧目录拒绝覆盖以及跨版本预算防重置。小fixture的结果只证明程序机制，不作为模型质量成绩。

首轮21通过/3个fixture错误：DuckDB的`COPY`同时包含子查询参数和目标路径参数时不支持该写法；改为参数化建立小表，再单独COPY，随后24项通过。之后补充最终确认、路径和跨版本记账测试。失败原因、首轮结果在本报告保留，不隐去。

| 路线0验收项 | 证据/结果 |
|---|---|
| 数据/时间正确输入门禁 | 原CSV与canonical/split ID-time一致，标签未投影到新分析 |
| 合法E执行根/环境 | Python、锁文件、缓存和临时目录实测通过 |
| 独立合同/预算/停止规则 | 严格机器合同、持久账本、共享预算指针及回归通过 |
| 版本冻结及只读复核 | 最终482文件SHA/大小通过，fit/外层/test消耗为0 |
| 旧证据保留 | 原5A重评分、Docker闭环、旧权重与旧账本绑定通过 |
| 资源实测与限制 | 最低门禁通过，全量峰值未测 |
| 工程 / 基础质量 / 图增量 / 学习 | Done / Not measured / Not measured / Pending |

## 私有证据及复现

最终登记：`E:\GraphRisk-Agent\reports\generated\stage09\route5b\20261009-preflight-v2\freeze.json`，482输入，SHA256 `12e9b1bbfe7165a84e28b535df68a5c2a6680f2008352fc758145496b93f5b92`。

最终测试：同一route5b根下`20261009-route0-verification-v2/pytest.txt`及`verification.json`；初版冻结与验证目录v1均保留。v2补齐跨版本指针与4B配置绑定，继续使用`artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json`，其SHA绑定原冻结；共享指针在`artifacts/stage-09/route5b/budget-binding.json`。不是每个源码版本各有30次。

只读复查（PowerShell）：

```powershell
Set-Location E:\GraphRisk-Agent
. E:\GraphRisk-Agent\scripts\enter_project_env.ps1
$taskSourceRoot = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$env:PYTHONPATH = Join-Path $taskSourceRoot 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
uv run --offline --no-sync python "$taskSourceRoot\scripts\preflight_stage09_model_enhancement.py" --execution-root E:/GraphRisk-Agent --check E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-preflight-v2
```

预期`status=passed`、`files_checked=482`、`fits_used=0`、`outer_access=0`、`test_access=0`。后续实现修改冻结源码后严格检查会拒绝，这是预期；保存原版本，使用`--name`创建新源码登记，自动沿用共享账本，不把漂移抹掉。

重新执行门禁回归会创建一个新的私有测试证据目录；名称须从未使用：

```powershell
uv run --offline --no-sync python "$taskSourceRoot\scripts\verify_stage09_model_enhancement.py" --execution-root E:/GraphRisk-Agent --registration E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-preflight-v2 --name my-route0-check-01
```

不需要启动Docker或Ollama模型。本人接下来完成LB0：画时间线、手算九次确认和失败扣账、解释“改配置不改变旧权重”。路线1准备列盘点和train-only特征处理，本轮没有提前实现。
