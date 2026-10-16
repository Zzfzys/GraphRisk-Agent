# Stage09 5B-6：完整历史覆盖、关系邻居与新事件推理

状态：**工程验收完成，2026-10-15；LB6本人学习Pending。** Stage09仍在进行，下一步为5B路线7。

这一步解决“系统能否给该给分的交易提供结果、能否诚实显示关系、新交易能否重复算出相同结果”。没有重新训练模型或提高准确率。增强XGBoost继续主评分，GraphSAGE仍是实验信号，不参与融合。

## 验收结果

| 项目 | 实测结果 |
|---|---|
| 原validation | 88,581笔，fold2冻结模型 |
| 原test | 88,581笔，沿用fold3冻结模型及封存预测 |
| 合计 | 177,162/177,162；缺失ID、时点图索引、无效分数、严格时间违规均0 |
| 模型资格 | 训练、早停、校准都先于目标期；原validation不用已见其标签的fold3 |
| 新事件真权重 | 冻结5案：4笔已知交易改ID，覆盖验证/测试及不同时间；另1案未知实体且无历史；重复3次通过 |
| 在线/离线 | 4笔树分数差0；12个图种子分数最大差8.94e-8，均低于预定1e-6，三种子均值也通过 |
| 真实HTTP | 覆盖查询、新事件预测、2笔报告、人工拒绝后重新读取；最终ready五项全true |
| 关系展示 | 两份HTTP结果含15/14笔relation_only，显示“无独立分数”，不填0 |
| 旧记录 | 33份真实旧记录恢复；31份报告和31份审批hash相同，33份公开视图相同；旧配置/模型绑定通过 |
| 回归 | **173 passed, 1 warning in21.98s**；warning来自Starlette弃用别名，无失败 |
| 本轮新增训练/评估标签批次/真实LLM/付费 | 全部0；HTTP报告/审批机制另用了2次明确标记的确定性生成夹具 |

完整覆盖包含真正的时点图缓存及根编码，不只是分数表。验证期每实体最近5笔，严格排除同秒及未来事件，三种子顺序CPU推理。测试期复用路线5已绑定的完整图及预测，不重开标签计算新成绩。

训练期历史邻居没有新增时间OOF分数，保留关系、时间及缺分原因。同一图模型版本且有合格独立评分的邻居才显示分数；其余为relation_only/null。不同折分数不混为风险排序，所有邻居按时间差/ID确定性排序。共享实体是启发式关系，不是已验证客户身份。

## 内存与实际故障

**无需释放到6GiB才运行本路线。** 原训练合同最低可用3GiB不改；本路线单独登记CPU推理：系统最低可用1.5GiB、单进程峰值上限1280MiB、单线程、图批次64、DuckDB128MiB。先通过1024笔一致性试验，再运行全量。

| 阶段 | 单进程峰值MiB | 说明 |
|---|---:|---|
| 早期合并JSON试验 | 1937.24 | 超限失败，保留 |
| 1024笔独立UBJ树重放 | 407.14 | 分数差0，不冒充全量 |
| 全88,581笔验证图构建 | 983.81 | 构图成功；后续宽表SQL排序OOM |
| 最终完整恢复/图推理主进程 | 889.41 | 最低可用3.409GiB；复用核验过的图，约111.77秒 |
| 最终全量树工作进程 | 539.68 | Arrow分批读取，避免450列SQL排序；最低可用3.415GiB |
| 5案及重复的新事件主进程 | 858.63 | 最低可用3.489GiB；树工作进程按请求退出 |
| fold3一次性原生格式转换 | 2087.34 | 独立上限2560MiB，最低可用1.628GiB；不属于1280MiB推理分母 |

主进程与树工作进程有短暂重叠；这些是各进程峰值，本轮没有测得精确的进程树同时峰值。不可宣称整个程序只占889MiB。原JSON不覆盖，另存UBJ；原生JSON再导出的SHA与原权重一致。缓存、环境、数据、权重及项目日志继续写E盘。

另修复了两个关键问题：

1. 在线日历特征不一致：最初新事件用了UTC，训练特征实际按香港时区生成小时/星期。全部590,540行核验，两项香港时区差异均0，UTC小时差异590,540。另存明确日历语义的profile复现训练输入，没有改权重。这是人工时间锚的特征复现，不代表真实客户时区。
2. E盘误加载旧API：新接口不存在。验收/启动现明确绑定当前工作树的src及根目录，并核对API模块实际路径；旧失败目录保留。

Ollama起初未运行，首次ready为not_ready。后续启动既有服务器，指向文档规定的E:/ollama，qwen3:4b固定digest匹配，最终ready全通过。GPU发现启动较慢的日志保留；仅查模型清单，没有下载模型或调用生成。

## 接口与界面

- GET /api/v1/coverage/{transaction_id}：covered、out_of_coverage、artifact_unavailable。
- 未覆盖调查在生成前返回409/prediction_out_of_coverage；坏工件不能变成0分。
- POST /api/v1/predictions/prospective：服务计算严格历史特征，使用冻结预处理/权重；不写历史、不创建审批。
- 工作台新增“查询评分覆盖”和“新交易试算”；图邻居明确显示“仅关系/无独立分数”。

新事件拒绝重复ID、标签、客户端自报历史统计、其他事件的未知占位符、矛盾实体、非法类型；支持未知类别/无历史。时间边界、未来注入、坏工件、版本错配及资源停止有小型确定性测试，不把这些机制测试当真实质量成绩。

## 证据与边界

私有证据均在E:/GraphRisk-Agent：

- artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/：完整评分、验证图、构建源码归档、两个profile、日历核验。
- artifacts/stage-09/route5b/20261015-coverage-binary-v1/：两折UBJ及转换证据。
- reports/generated/stage09/route5b/20261015-coverage-verification-v3/：冻结5案、真权重结果、重复检查、真实HTTP、资源及源码归档。
- reports/generated/stage09/route5b/20261015-coverage-delivery-v1/：逐种子一致性、旧记录hash、最终ready、网页AppTest表格、回归摘要和总封存。
- 早期coverage-lowmem-v1/v2、coverage-pilot-v1、coverage-verification-v1/v2保留启动线、JSON超限、SQL OOM、日历不一致、旧API导入等失败。

最终profile SHA：5ecb2126eb282fc759bbbf261e576c6fe3b5f19d3c0185a9dee7d21a821232e4。
独立验收SHA：cddcddacc19533aa18c33b8b956a63e6a6d5448dfa33dd10b8ba2b7a216da1d4。
补充交付SHA：86a12ec9dd31d6ab42e8fcc1088557de957403e4299819fd6565ffd7baceca6e。
训练/标签访问账本前后相同：a3ccd7f930de349183de66fa6deea475c1e341683f856dafa792127f4e7588db。

最终总封存SHA：fae93e6a4ad72a195d773576dd8654998a5f0d9d1f49fb6a03511aefc7ff8a6c，绑定296份源码/配置/测试/证据文件。新进程只读check返回engineering_done；四份本轮核心文档相对链接核验无缺失，git diff --check通过。

AppTest使用真实HTTP产生的公开视图，HTTP报告使用确定性夹具；**不增加真实Agent质量样本**。本轮未运行Docker或实时PostgreSQL回归；旧真实记录/审批离线恢复和持久化合同回归通过，数据库schema未改。容器/实时数据库及跨路线总验收交5B路线7。训练期OOF评分、持续追加索引、高并发/负载未在本路线实现。

## 自己复现

只读核验不训练、不推理、不调用LLM。每个新PowerShell窗口先进入E盘环境：

```powershell
. E:\GraphRisk-Agent\scripts\enter_project_env.ps1
$coverageSource = 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$env:PYTHONPATH = "$coverageSource\src;$coverageSource"
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location E:\GraphRisk-Agent
uv run --offline --no-sync python "$coverageSource\scripts\check_stage09_coverage.py"
```

在两个窗口分别启动新API和工作台：

```powershell
& 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\scripts\run_stage09_coverage_service.ps1' -Component api
```

```powershell
& 'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\scripts\run_stage09_coverage_service.ps1' -Component workbench
```

端口8000/8501有旧服务时，先在对应旧窗口Ctrl+C。打开 http://127.0.0.1:8501 ，输入3400378或3488959，先点“查询评分覆盖”。“开始新调查”会调用本地LLM，不属于只读核验。

“新交易试算”可粘贴私有frozen-cases.json内某一项的event对象；会运行CPU真权重，不调用LLM、不追加历史、不审批。不要粘贴整个cases文件。

旧stage05.yaml、stage09.yaml、stage09_quality_4b.yaml保留，回滚时指定旧配置。恢复旧会话仍须原数据库/检查点；内存模式重启不可恢复的边界未变。

## 学习

按[LB6五次30分钟练习](../docs/plans/stage-09-route-5b-learning.md)，解释覆盖率与准确率、训练邻居缺分、同秒排除、在线/离线输入诊断及进程内存口径。工程完成不等于本人学习完成，LB6待本人核验。
