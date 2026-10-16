# Stage 09 路线 5B-1：原始特征与时间正确管线验收

**工程完成，2026-10-09；LB1本人学习待完成。** 已用全部590,540笔 IEEE-CIS 交易生成独立特征侧表，并验证第一折训练期的两套预处理。下一步可以进入5B路线2：增强XGBoost与公平对照。

用简单的话说：过去模型只拿到了原CSV的一小部分信息；这次把原始字段接通，再加上“这张卡/设备/邮箱/地址之前出现过多少次”等历史信息。填补值和类别词表只从训练期学习，同秒交易不会互相偷看。现在模型的输入准备好了，但还没有训练，所以不知道新模型是否能达到ROC-AUC 0.80。

## 实际完成的内容

1. **完整原始字段接入。** 交易CSV394列、身份CSV41列，经排除ID、时间元数据和标签后，有431个原始候选字段。逐交易对齐144,233条身份资料；不存在的身份保持缺失。
2. **24个派生字段。** 四类关联实体各五项历史统计，加身份存在标记和相对时间字段。历史只看严格更早事件，等时交易视为同时；不用欺诈标签生成特征。
3. **两套输入分开登记。** `core` 使用可用性有明确假设的字段和历史统计；`source_as_received` 再加入匿名原始字段。C/D/V/id等字段的供应方生成时点未知，不能因为不读标签就声称已证明无未来信息。
4. **可重载预处理。** 数值训练中位数填补、全部保留数值的缺失标记、训练类别词表、未知与缺失分开；只按训练期常量/精确重复筛选，不做target encoding。保存列顺序、统计、词表、用途和哈希。
5. **资源与版本证据。** 新源码/字段合同重新冻结，完整大文件与临时数据库留E盘；旧模型、canonical、时间切分与生成预算不覆盖。特征和资源记录可只读核验。

代码入口：[字段schema](../src/graphrisk/features/wide_contract.py)、[连接与历史管线](../src/graphrisk/features/wide_pipeline.py)、[预处理](../src/graphrisk/features/wide_preprocessing.py)、[资源与证据检查](../src/graphrisk/features/wide_execution.py)、[构建/只读CLI](../scripts/build_stage09_wide_features.py)。详细定义见[特征与时间合同](../docs/data/stage-09-model-enhancement-features.md)。

## 完整数据结果

| 项目 | 真实结果 |
|---|---|
| 侧表行数 / 唯一交易 | 590,540 / 590,540，与冻结输入一致 |
| 侧表列 | 457：2个元数据列 + 455个候选特征；标签列不进入侧表 |
| 候选特征 | 431个原始字段 + 24个派生字段 |
| Parquet大小 | 114,723,944字节，约109.41MiB |
| 预处理拟合范围 | fold-1训练期250,979笔；ID顺序哈希与路线0一致 |
| 分块转换 | 两套输入各62块、各250,979行，0丢行 |
| 独立历史复算 | 9个目标含2个同秒目标、4类实体、180个数值/缺失比较，通过 |
| 独立读取已落盘Parquet | 590,540行/唯一ID、144,233个身份连接、457列及0禁用列确认；训练金额中位数重新计算一致 |
| 执行期间原始输入复查 | 493个冻结输入通过，含266个E盘执行输入 |
| 新真实模型fit / 新生成 / 付费 | 0 / 0 / 0；旧生成130/144不变 |
| 外层标签 / test标签访问 | 0 / 0；共享账本未冻结模型决策 |

直接历史复算从窄源表按 `time < target` 查询，不复用构建窗口函数。它是多个时间位置的抽查，不是逐行数学证明；完整时间规则还由未来注入/等时fixture检查。Parquet字节可重复性在小fixture验证，完整侧表本次构建一次并绑定SHA，没有重复生成整份数据冒充独立复现。

| 输入方案 | 候选特征 | 保留输入 | 数值 / 类别 | 加缺失标记后输出 | 训练期删除 |
|---|---:|---:|---:|---:|---|
| core | 38 | 38 | 25 / 13 | 63 | 无 |
| source_as_received | 455 | 454 | 417 / 37 | 871 | V107为训练期常量 |

871并不代表871个独立业务信息：其中417个是数值缺失标记。类别保持pandas原生Categorical，下一路线树模型须显式启用类别处理；不把代码值当连续数值。旧GraphSAGE仍为28维，此处没有修改其结构或权重。

字段盘点只用第一折训练期统计。例如 V1缺失162,820/250,979（约64.87%），DeviceInfo缺失183,574/250,979（约73.14%）；缺失并非0，也不是已识别低风险。字段表保存全列的缺失数、训练行数、基数和数值统计，可按 `missing_count / count` 计算缺失率。

## 内存、磁盘与耗时

| 项目 | 观测 |
|---|---|
| 构建管线耗时 | 196.26秒，约3分16秒 |
| 进程峰值工作集 | 1,760.84MiB，约1.72GiB |
| RAM/磁盘采样 | 98次，每约2秒采样及首尾观测 |
| 最低可用RAM | 4.7420GiB，高于3GiB门禁 |
| 最低磁盘余量C/D/E | 18.3474 / 30.0056 / 48.3312GiB，均通过 |
| SQL资源 | 1线程、768MB DuckDB限额、最多5GiB E盘spill |
| 转换方式 | 每批4,096行，不在Python保存完整871列训练矩阵 |

196秒从起始资源门禁后计时，包含源码归档、构建、训练期预处理、分块转换和结束输入复核；额外只读检查/回归另记。DuckDB限额与整个进程工作集口径不同。采样最低值不是连续每一毫秒的证明，也不代表下一路线训练必定满足资源线。成功后仅删除本次新建的 `working.duckdb` 中间文件；原始数据、旧模型和卷不删除。C盘只有本次源码/文档编辑，没有复制完整数据、环境或权重。

## 测试与限制

最终冻结版本的特征、旧适配器/历史规则、新合同和共享预算门禁回归：**75 passed、1 skipped，11.54秒，0 errors**。其中路线1新增39个测试，另6个旧数据规则测试和31个路线0门禁测试。唯一跳过项是Windows不允许创建符号链接的旧路径门禁fixture；不是数据、历史或预处理测试跳过。

覆盖：同秒互不计数、未来追加和标签改变不影响历史、直接复算发现故意污染、训练中位数/类别词表不看未来、精确重复筛选、缺失/0/未知/保留词区分、不修改调用方DataFrame、字节复现、重复/丢行/错时间/孤立身份、非法数值、标签/元数据输入拒绝、哈希漂移、资源失败关闭以及只读检查不重建预算。

本次没有启动网页、数据库、Docker或LLM的整体回归。兼容依据是旧管线未修改、旧数据规则测试通过以及冻结的旧输入SHA不变；不把历史640项全系统测试写成本次新成绩。匿名字段可用性仍有限，fold-2/3预处理待最终方案冻结后各自训练，不提前用于第一折选型。

旧路线0源码归档时一次复制被版本漂移保护拒绝，随后按原始216个源文件SHA重新对齐，完整旧版另存 `20261009-preflight-v2/source-snapshot-final`。原冻结、预算与首次证据保留；本路线完整构建成功，没有真实训练失败或额度消耗。

## 私有证据与版本

大数据与逐列分布只留本地忽略目录，不提交受限数据。

| 证据 | E盘路径/哈希 |
|---|---|
| 新源码登记 | `reports/generated/stage09/route5b/20261009-feature-source-v1/freeze.json` |
| 登记SHA | `1556f8f4e3af5c78fce32e3a07d33980fa430cc4b1796e236de1c7eac512d924` |
| 特征产物根 | `artifacts/stage-09/route5b/20261009-features-v1/` |
| 特征报告根 | `reports/generated/stage09/route5b/20261009-features-v1/` |
| Manifest SHA | `e48272eefa369a7bc2fdafc093957dfdfaa1c95b81e3b67330493e30a6bbdf34` |
| Parquet SHA | `7836b5c2d769516118ec1a79368c70052d56cb0b5560ee43849f72c33ceb57f0` |
| 回归证据 | `reports/generated/stage09/route5b/20261009-feature-verification-v1/{pytest.txt,verification.json}` |
| 序列化独立检查 | 同回归目录 `serialized-table-check.json`；另确认9个新增Python文件AST可解析 |
| 共享首次账本 | `artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json`；fits/外层/test均0 |

产物目录有 `features.parquet`、`field-catalog.json`、两套预处理JSON、`history-audit.json` 和绑定源码快照。报告目录有首尾资源/完整采样及manifest；manifest同时绑定资源文件和产物SHA。只读复核可继续使用归档源码，不要求后续工作树永久停留在该版本。

## 自己怎样验证

先用只读检查，无需再次构建590k数据。新的PowerShell运行：

```powershell
cd E:\GraphRisk-Agent
. .\scripts\enter_project_env.ps1
$env:PYTHONPATH = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
uv sync --locked --offline --check --group demo
uv run --offline --no-sync python C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent/scripts/build_stage09_wide_features.py --execution-root E:/GraphRisk-Agent --check E:/GraphRisk-Agent/reports/generated/stage09/route5b/20261009-features-v1
```

应返回 `status: passed`、590540行、431原始字段、455候选特征、5个产物文件与493个输入通过；`read_only: true`。本次构建结束及最终回归前后均已执行成功。

学习时可以只跑三个关键fixture，测试临时目录给新的唯一名：

```powershell
uv run --offline --no-sync pytest C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent/tests/integration/test_stage09_wide_features.py -q -k 'history_excludes_same_second or future_and_label_changes or train_only_fill_vocab' --basetemp E:/GraphRisk-Agent/artifacts/pytest-my-5b1-learning-v1
```

完整回归保存入口是 `scripts/verify_stage09_wide_features.py --features <上述报告根> --name <新的唯一验证名>`；本次实际结果已保存，不用为了看结果重复运行。需要完整重建时先用 `preflight_stage09_model_enhancement.py --name <新登记名>` 绑定当前源码，再用构建入口 `--name <新特征名> --registration <新登记目录>`。既有目录拒绝覆盖，任何新登记沿用同一训练额度。

按[LB1六天练习](../docs/plans/stage-09-route-5b-learning.md#lb1模型输入到底少了什么180分钟)完成学习。路线1工程通过不能替代本人学习，也不能替代下一路线的真实模型质量比较。
