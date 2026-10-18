# L3：读懂项目成绩单（105分钟）

材料已准备；**本人完成状态Pending**。分三天，每天35分钟，读[最终评估](../../reports/stage-10-final-evaluation.md)及[主张索引](stage-10-final-claims.md)。不需要为学习新增模型调用。

## 第一天：数据和指标（35分钟）

画出train→早停→校准→外层的时间顺序，注明原历史test。回答：哪些段训练参数，哪些选轮次/alpha，哪些确认？为什么下一折能使用前折已发生交易，而不能让本折训练读自己的未来？

计算三折XGB AP均值：(0.502215621959087+0.6129459288186164+0.5179387641545279)/3，约0.544367。不要把它说成54.4%交易判断正确。再解释Top1%召回24.7247%：少量高风险交易队列覆盖的欺诈比例，而不是队列准确率。

产出：自己的时间线、AP/ROC/Top1%召回各一句话；从数据卡指出人工时间锚、身份启发式、匿名字段时点三个限制。

## 第二天：模型选择和负结果（35分钟）

手算XGB0.2、GNN0.8、alpha0.75的融合为0.35；alpha1时结果为0.2。说明三个alpha1为何让融合AP与XGB相同，为什么不能写“GNN提高风控效果”。

看模型卡：图的最佳轮次触及25，输入与宽表不同。分别写出“目前冻结方案未带来增益”和“所有图模型都没用”之间的证据差距。解释按种子平均指标与先平均分数再评分为什么不同。

产出：一条保留XGB主评分的决策说明，以及未来实验仍需回答的两个问题；本轮只写问题，不启动调参。

## 第三天：Agent和交付表达（35分钟）

比较首次6/10、重复2/2、模型HTTP12/12、调查HTTP8/12：各自分母是什么，为什么都对但不能互换？解释“4次被拦截”既支持安全门禁有效，又说明质量未达标。

比较GraphRAG hybrid18/22与graph18/22，指出text16/22及模拟既见集限制。再说明50/50机制测试为何不能证明50笔真实LLM回答正确。

给每句标claim ID：可写“实现带证据核验与人工审批的有界调查流程；当前10笔首次6笔通过（E04）”；可写“固定三折确认后保留XGB，GNN未晋级（E01/E02）”；可写“GraphRAG在小型模拟既见集18/22，与graph持平（E07）”。不可写“Agent准确率100%”“GNN已调至最优”“达到企业生产SLA”。

产出：能写/不能写各三句话，以及本人对至少一个失败的解释。材料存在不代表你已完成，待本人反馈后再更新学习状态。

## 可选只读核验

在PowerShell执行以下已有入口，只重读保存结果，不发送模型请求：

```powershell
Set-Location E:\GraphRisk-Agent
. .\scripts\enter_project_env.ps1 | Out-Null
$source='C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$env:PYTHONPATH="$source\src;$source;$source\scripts"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:UV_PROJECT_ENVIRONMENT='E:\GraphRisk-Agent\.venv'
uv run --offline --no-sync python "$source\scripts\read_stage10_results.py" check --output-dir E:/GraphRisk-Agent/reports/generated/stage10/route2/20261017-real-v2
```

预期quality.status=failed、joint_pass=6、first_denominator=10、completed_runs=12、global_used=142、remaining=2、new_requests_during_analysis=0。这里出现failed是正确保留的模型质量结果；命令异常退出或绑定不匹配才是复核问题。不要用旧执行器重发12条。
