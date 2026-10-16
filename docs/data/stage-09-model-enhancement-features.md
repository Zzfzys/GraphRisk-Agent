# Stage 09 路线 5B-1：宽表特征与时间合同

这条管线给下一路线的模型提供更完整、可追溯的输入。它不训练模型，不修改当前调查服务，不把字段数增加当作效果提升。

## 输入、输出与来源

输入绑定路线0的 IEEE-CIS 原始 `train_transaction.csv`、`train_identity.csv` 和旧 canonical Parquet。新侧表留在 E 盘 `artifacts/stage-09/route5b/<唯一运行名>/features.parquet`。旧文件、模型、时间切分和 Agent 记录保留。

侧表以字符串 `transaction_id` 对齐，另存整数 `source_transaction_dt_seconds` 作为排序与时间边界元数据。两列不进入模型；`isFraud` 不进入投影表、统计、词表或模型输入。原 CSV 仍有标签，读取 CSV 的文件解析不等于把标签用于特征。

原始交易与身份表的 ID 必须各自唯一；身份可缺失，但不能出现没有交易的孤立身份行。原始与 canonical 的 ID、时间、金额、行数必须一致。错误类型、非有限数值、重复、错配和丢行显式失败。canonical 中已有身份、卡、设备、邮箱和地址键都是启发式关联，不是已核验客户身份；未知实体沿用每交易独立占位，不能合并成一个共享“未知用户”。

| 字段组 | 用途与类型 | 时间可用性边界 |
|---|---|---|
| TransactionAmt | 数值 | 假设交易记录时已知，未独立审计数据供应方 |
| ProductCD、card1～6、addr1/2、P/R_emaildomain | 类别 | 同上；数字形式的代码保持类别语义 |
| DeviceType、DeviceInfo | 类别，身份缺失时保持 NULL | 假设交易时可获得的身份资料，未验证供应方生成时点 |
| dist、C、D、V | 数值 | `source_as_received`；生成时点未知 |
| M | 类别 | `source_as_received`；生成时点未知 |
| id_01～38 | 按机器 schema 分数值/类别 | `source_as_received`；生成时点未知，不推断真实业务含义 |
| has_identity | 数值 0/1 | 本笔是否成功关联原始身份行 |
| relative_day、relative_day_sin/cos | 数值 | 相对天编号和一天内相位，不是真实日历/星期 |
| 四类实体的历史统计 | 数值，定义见下 | 仅严格更早、无标签事件 |

可用两套输入：`core` 包含上述可用性有明确假设的14个原始字段与24个派生字段；`source_as_received` 是 core 加匿名/未知生成时点原始字段的敏感性方案。宽表上取得好成绩不能消除匿名字段的时间不确定性。下一路线必须分别报告用途，不能把后一套叫已证明因果或生产可用。

## 严格历史统计

card/device/email/address 四类 canonical 关联键，各产生五个字段：过去24小时交易次数、金额均值、金额总体标准差、距上次交易秒数、距首次更早交易秒数。邮箱键沿用 canonical 的关联语义，不额外声称是邮箱账号。

对于目标时刻 t，24小时范围为 `[t-86400, t)`，长期范围为 `event_time < t`。TransactionDT 是整数秒，窗口使用 `RANGE ... 1 PRECEDING`；同秒交易同时发生，互不进入历史。没有历史时次数为0，均值/标准差/时间间隔为 NULL；后续由训练期预处理填补并提供缺失标记。

侧表可以一次生成全时间段：每一行的历史值仍只看该行之前的事件。较晚验证/测试事件不会改变较早行。后期目标可使用当时已发生的较早验证/测试事件作为无标签上下文，不使用它们的结果标签。这与把全数据 groupby 后回填所有行不同。

执行器另用直接 `WHERE time < target` 聚合复算多时间位置及同秒目标的四类历史统计，独立于构建窗口 SQL。fixture 还检验追加未来事件、改变标签、改变同秒交易金额，以及故意破坏历史值能被审计发现。

## 训练期预处理

路线1只拟合 frozen fold-1 的250,979个训练 ID；先核对数量、时间上界与有序 ID 哈希。fold-2/3 的预处理等最终方案冻结后再各自拟合，避免提前查看未来训练分布影响本轮搜索。

1. 数值用本折训练期中位数填补，每个保留数值都有 `missing:<字段名>` 0/1标记。0与缺失保持不同。
2. 保存填补后的训练均值/尺度，供后续需要标准化的无图对照使用；树模型默认不标准化。
3. 类别词表仅从训练期建立。使用 pandas 原生 Categorical，下一路线的树模型必须显式启用类别处理。类别编码不能当作有大小关系的数值输入。
4. `__missing__` 与 `__unknown__` 分别表示缺失与词表外值；真实类别统一加 `v:` 前缀，所以名为 `__missing__` 的真实字符串也不会与缺失混淆。
5. 仅根据训练期去掉全缺失、没有缺失变化的常量、逐行完全相同的重复列。哈希只用于候选短名单，再按 `IS DISTINCT FROM` 精确确认；不读标签、不做相关性筛选或 target encoding。单个非缺失值加一部分缺失的列仍保留。
6. transform 要求精确特征列集合，拒绝 ID、标签、预测分数和额外列；错误数值、inf和 float32 溢出显式失败。不修改传入 DataFrame。

`field-catalog.json` 的缺失数、基数、均值等只描述 fold-1 训练期；不代表全数据分布。每套预处理 JSON 保存输入/输出顺序、删除原因、数值统计、词表、拟合范围及内容哈希，可重新加载。原始字段数量、保留输入数量和带缺失标记后的输出数量分别记录。

## 内存、资源、版本与证据

已有 DuckDB/Parquet 完成连接与统计。线程1、DuckDB限768MB、最多5GiB E盘 spill；统计每次8列，转换每次4,096行，不在 Python 中复制完整宽表矩阵。DuckDB限额不等于整个进程内存限额，所以另测 Windows 进程峰值工作集与系统可用内存。

起始检查 RAM/GPU/C/D/E，执行每2秒采样 RAM/磁盘；RAM最低3GiB、C/D最低5GiB、E最低10GiB。资源不足、测量失败或60分钟上限触发即停止，保留失败证据；不降低门禁，不自动关闭用户应用。成功前最后一次采样也必须通过。

新源码、特征 JSON 与本说明必须先重新登记，旧路线0冻结保留。新登记共享首次5B账本，不恢复训练额度。执行器复制绑定源码的小型快照到 E 盘，保存 Parquet/预处理/字段表/直接历史审计 SHA、资源证据、原始输入复核结果及无训练/无新生成/无付费/无外层或test标签访问的范围声明。

只读检查使用归档源码与 E 盘输入，后续编辑代码不会使旧产物失去核验依据；产物、冻结内容或资源记录漂移会失败。共享账本仍检查当前状态，缺失不会自动重建。所有大数据、数据库临时文件、缓存和私有证据留 E 盘；成功后只删除本次创建的 `working.duckdb` 中间文件。

## 使用入口

先读[路线1验收报告](../../reports/stage-09-route-5b-1-features.md)中的实际运行名和结果。每个新的 PowerShell：

```powershell
cd E:\GraphRisk-Agent
. .\scripts\enter_project_env.ps1
$env:PYTHONPATH = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
uv sync --locked --offline --check --group demo
```

入口是 C 盘工作树 `scripts/build_stage09_wide_features.py`：`--check <已有报告目录>` 只读复查；`--name <新唯一名> --registration <新的源码登记目录>` 构建新版本，已存在目录拒绝覆盖。不是每次学习都要重建590k数据。该入口不运行 XGBoost/GraphSAGE，不产生 ROC/AP，也不改服务版本。

本人练习见[LB1](../plans/stage-09-route-5b-learning.md#lb1模型输入到底少了什么180分钟)，工程通过与本人完成学习分别记录。
