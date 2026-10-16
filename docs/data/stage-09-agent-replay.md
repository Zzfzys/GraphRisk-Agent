# Stage09 路线5：模型决策接入与配对重放学习讲义

状态：实现/验收见[路线5报告](../../reports/stage-09-route-5-agent-replay.md)；L5 本人练习待完成，不代签。

本轮用同一批30笔真实验证交易检验“模型决策能否正确传到调查系统”。主风险模型仍是 Stage02 的 XGBoost，图模型换为路线4验证选中的 candidate-4（full/cap5/hidden128）；后者没有达到晋级条件，只提供实验性图分数和历史邻居排序。无融合分数，分数均未校准。

## 先理解三个合同（15分钟）

1. **决策与文件。** 路线4只按验证集选型，decision 的哈希绑定 deployment，再由 configs/stage09.yaml 固定 deployment 哈希。主模型、图权重、metadata、encoder、图 manifest、固定分数与旧比较文件必须匹配。改文件名或随意拷贝权重不能构成升级。
2. **工具与发布。** graph/deployment.py 加载新图分数；风险工具仍加载同一个 XGBoost。InvestigationReport 和 RunVersions 带决策哈希、分数模式/模型版本。服务发布及审批前再次检查，图不可用返回缺口，不能显示成0分或完整调查。
3. **配对实验。** 交易ID/顺序、政策日期/索引、生成模型digest、模板、draft schema、生成参数和评分均固定。新工具快照只允许预登记的图证据、依据ID及分数角色说明变化。

特别注意：当前冻结 Prompt 的 EVIDENCE_JSON **没有输入风险分数**，只有交易/来源、证据ID和可选政策等字段。模型分数变化主要由程序显示；LLM 看到的变化是图模型/邻居证据ID。用本轮结果声称“GNN分数提升了LLM判断准确率”是不成立的。

## 亲手比较三对（30分钟）

在本地忽略目录 reports/generated/stage09/route5/20261007-replay-v1/paired.json 选择三类：

- 一笔图覆盖交易：比较旧/新图分数、邻居排序、主XGBoost分数、模型版本和引用出处。解释哪些由确定性模型计算，哪些属于生成输出。
- 一笔图未覆盖交易：确认没有GNN值/融合值，主评分仍保留，部分证据与审批边界保持诚实。
- 一笔失败或固定重复：同时读旧、新原始HTTP响应和报告。分清schema合格、来源绑定合格、LLM原始uncertainty是否缺失、程序补充限制，以及人工语义复核尚未完成。

逐对写：观察 → 对应证据字段/哈希 → 可以支持的结论 → 不能支持的结论。不要只截图 verified 标签；它表示来源引文核验，不能直接等于欺诈判断正确。

## 检查与回滚（20分钟）

在项目 PowerShell 中执行：

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline python scripts\check_stage09_replay.py --output-dir reports\generated\stage09\route5\20261007-replay-v1
uv run --offline pytest tests/unit/test_stage09_deployment.py tests/demo/test_streamlit_app.py -q
~~~

check_stage09_replay 只审计已保存结果，检查代码/配置/案例快照/HTTP参数和共享调用账本，不新增LLM调用；pytest是小型合成机制测试，不计真实质量成绩。旧证据绑定原代码版本，新代码意图变化由独立route5冻结登记，不重写route1/route4原冻结。

新版演示采用显式配置启动：

~~~powershell
uv run --offline python scripts\run_stage06_local.py --config configs\stage09.yaml
~~~

旧版回滚配置是 configs/stage05.yaml。停止自己刚启动的演示进程，再用同一个启动脚本传旧配置。不要覆盖模型工件，不要同时启动两套竞争同端口的服务。页面新版应显示“XGBoost 主风险分数”和“GraphSAGE 实验性分数”，无融合；旧版沿用原来的融合实验展示。

以下仅重新采集30笔真实工具证据并与旧快照比较，**不重跑LLM**。先保证RAM/磁盘/GPU起始门禁并关闭自己不需要的演示进程：

~~~powershell
uv run --offline python scripts\replay_stage09_agent.py rollback --output-dir reports\generated\stage09\route5\20261007-replay-v1
~~~

想新增真实生成必须建立新实验登记与预算，不直接删除 results/started 来重跑。既有 run 命令对完成案例先重新评分再跳过；未完成且已开始的案例必须审核，不会自动重发。全阶段上限144、此阶段66、每例2次，失败发送也计预算。

## 写出结论（10分钟）

回答：

1. 为什么XGBoost作为主评分，但仍可以保留实验性GraphSAGE？
2. 30笔来源绑定合格能证明自由推理准确率吗？固定重复为什么不是更多独立交易？
3. Prompt没有标量分数时，哪些GNN变化真正进入LLM？
4. 把XGBoost单分数说成融合，会在哪些合同/测试被拦截？
5. 代码改了，为什么不能重写旧冻结使它再次显示“无漂移”？
6. API演示自动拒绝只验证审批边界，为什么不算本人完成语义复核？
7. 18个DB skipped说明了什么？本轮内存模式结论能否直接覆盖PostgreSQL持久化？

交付：三对归因表、一次只读check记录、一次机制测试记录、旧配置回滚解释。至少10案的人类语义复核仍见路线1的独立表；L5完成需由学习者填写。

## 下一项实验建议

根据配对失败矩阵另立LLM实验，保留本轮基线：先扩大有效政策候选与问题多样性，再分别研究证据ID复制、原始不确定性表达和政策条件；若增大本地模型或修改Prompt，单独登记模型/资源/参数/预算，不能与本轮GNN变化混合归因。本路线不自动下载模型、不付费、不追加训练。

