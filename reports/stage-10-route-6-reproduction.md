# Stage10路线6验收：干净检出与公开／真实工件复现

2026-10-18：**工程Done；L6本人Pending；Stage10整体In Progress。** 当前真实Agent仍首次6/10 Fail，GNN不晋级。复现成功不改变模型质量结论。

完成新Git检出与两个新环境，不复用旧.venv；公开模式无需受限数据，真实模式明确复用授权数据与冻结工件。操作和90分钟学习见[复现指南](../docs/data/stage-10-clean-reproduction.md)。

## 版本与证据

- 实际测试提交a3f1acc5c5a95e0521f2318a515da956e0fabbaa；本地Git clone，610追踪文件核对。没有联网clone或当前提交远程CI声明。
- 新检出：E:/GraphRisk-Agent/artifacts/stage-10/route6/20261018-reproduction-v1/checkout；两个.venv位于主项目与独立项目各自目录。
- 私有证据：E:/GraphRisk-Agent/reports/generated/stage10/route6/20261018-reproduction-v1/。包含setup/install/import/public-result、CPU junit/summary、real-v3、replay、independent结果、失败诊断及最终acceptance。
- uv0.12.18，Python3.11.16；离线安装复用E:/GraphRisk-Agent/.uv-cache与.uv-python，锁文件不变。本轮未验证在线冷缓存安装或从原始CSV重训。

## 验收项与实际结果

| 条件 | 证据与结论 |
|---|---|
| 新检出与新环境 | 初始无.venv/.env/.secrets/私有模型，610文件对Git核对；主环境147 distributions；graphrisk/api/Torch/Streamlit从新路径导入 |
| 公开核心运行 | fixture50/50、real_samples=0；合成5000行和特征流水线通过，无作者私有数据输入 |
| 适用自动回归 | 新环境CPU915 passed/1平台symlink skip/0失败/错误；四个DB文件明确排除，不宣称26真库本轮重跑 |
| 真实模式前置 | profile的35文件/版本绑定、177162历史覆盖与原封存验收闭环通过；本机固定Ollama tags可用，ready组件全true，无生成POST |
| 真实工具与权重 | 五个真实工具全部ok并与原报告分数一致；一笔冻结prospective重放，树差0、图差1.086543e-8≤1e-6，不读标签/不改历史 |
| 资源 | CPU合同保持1.5GiB可用/1280MiB进程；本轮最低3.251GiB、主进程峰值800.57MiB，子进程另记 |
| 当前报告/页面复现 | 使用已存增强case01/case04，真实loopback HTTP与AppTest九项行为通过；201/503保持、拒绝/刷新/幂等与报告hash保持；服务关闭 |
| 独立最小环境 | 仅enterprise-graphrag distribution；Torch/GraphRisk不可导入；语料24/58/22/40/30；91核心测试通过、3可选UI测试skip，无真实问答调用 |
| 预算与公开边界 | 三旧账本SHA不变；新LLM/真实fit/付费0；142/144剩2；数据/权重/行级证据和环境均留忽略目录 |

CPU与独立核心合计1006项通过、4项skip，按层分别说明；它们不是1035历史CI的新增独立功能数量，也不与fixture50/五工具/九行为相加。AppTest的completion.checks包含9行为加3执行方式标志，共12字段，不能写12测试。

## 发现的问题与处理

直接用新检出校验历史源码，92个登记文件因LF/CRLF差异触发registered_experiment_source_changed；旧实验原始字节hash拒绝是正确行为。保留首次失败和差异清单，没有改旧manifest、代码、模型或成绩。改用封存source-snapshot的396文件做历史严格字节核验，并逐文件证明它与新检出只差换行；实际执行仍是新检出代码。三份后处理分析文件来自Git，作者工作区隐藏源码未作为运行输入。

第二次私有辅助脚本把核验快照放在generated内，被inventory排除，亦保留失败；最终快照移到本轮artifacts目录并通过。两次均止于只读历史校验、没有权重推理或模型生成。最终real-v3和replay才是成功证据。此处理未修复旧历史CLI对任意检出换行的直接适配；该限制和快照额外输入写入指南。

public-result.json早期两条缓存说明缩写为E:/.uv-cache和E:/.uv-python；这是说明字符串省略项目名，实际命令/env/setup/install均记录完整E:/GraphRisk-Agent路径。原输出保留，本报告作明确更正，不声称使用了E盘根目录缓存。

## 验证范围与下一步

应用Python、Prompt、依赖锁、模型和数据合同未修改；新增文档与运行证据，不为换行问题改历史运行逻辑。未新建镜像/容器/数据库，未重复真库故障注入、Linux安装、冷缓存联网安装、原始数据全量重训、真实LLM生成或新浏览器截图。已有历史CI/真库和路线5图片按其精确版本复用，不冒充本轮结果。

已同步实现路线、学习路线、阶段总纲、总体计划、索引、交接、README、启动与限制/证据台账。最终文档本地提交与bundle绑定在私有acceptance，尚未推送；本轮回归对应a3f1acc应用源码，不冒称最终文档提交已有远程CI。

下一步路线7：求职条目、面试演练与至少10案本人实用性复核；路线8总验收/推送CI仍未实施。L0～L8本人Pending保持；stage10-agent提醒继续等待工程总审计后再提醒独立Agent质量专项，不消耗余量，不提前启动质量或GNN检索专项。
