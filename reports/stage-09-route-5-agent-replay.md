# Stage09 路线5：模型决策接入与真实 Agent 同集重放

> 工程状态：Done（2026-10-08，Asia/Hong_Kong）；质量结论：本地小模型没有改善，失败已保留。
> 个人状态：L5 与路线1至少10案的人类语义复核仍待本人完成；Stage09 总阶段仍 In Progress。
> [75分钟学习与复现讲义](../docs/data/stage-09-agent-replay.md) · [实现路线](../docs/plans/stage-09-implementation-roadmap.md)

本轮把路线4“不晋级GNN”的验证决策接入显式 Stage09 配置，再用路线1的同30笔真实验证交易及3个固定重复重放。主评分继续使用 Stage02 的 xgboost-v1；candidate-4 只提供实验性 GraphSAGE 图分数和历史邻居排序，**没有融合分数**。没有新增训练、模型下载、embedding 或付费调用。

## 接入结果和版本链

- 新配置：configs/stage09.yaml。旧 configs/stage05.yaml 完整保留，仍支持历史实验展示和回滚；启动新版必须显式传新配置，不假称所有旧入口已自动换模型。
- 决策：路线4验证-only、promote_gnn=False；candidate-4（full、cap5、hidden128、lr0.001、dropout0.2、seed20260921）。本轮不重选、不训练、不重新比较测试标签。
- 主评分模型仍是 Stage02 全量训练冠军；旧图工具中的50k受控XGBoost/融合字段不再用于新版评分。不能把主模型与受控模型混为一个。
- GraphRiskService 已有校验与时间图索引被复用，DecisionGraphService 检查新权重、metadata、encoder/manifest、比较元数据和固定验证/测试分数文件；只投影ID和已有分数，不读取fraud标签。
- 20/20覆盖交易新图分数已改变，delta范围 -0.120471～+0.202681，均值 +0.018037；14/20历史邻居顺序改变。30/30主XGBoost工具结果保持一致，10/10未覆盖交易继续明确缺图分数。
- InvestigationReport 和 RunVersions 记录决策哈希/模式/主模型及实验图版本；报告发布和审批前检查绑定。错误主模型文件或版本直接停止；图错配安全关闭图证据、保留能力缺口，不能用旧分数冒充新模型。准备/真实重放要求依赖ready。
- 新版API/UI显示“XGBoost 主风险分数”和“GraphSAGE 实验性分数”；图缺失显示不可用，不显示0、不生成融合值。旧序列化保持兼容。

| 证据 | SHA256 |
|---|---|
| 路线4验证决策 | 4b1a0ccc103a11c532871620bfd07a8dc4500dec7fb3aaf307719fdb309d1853 |
| 新部署profile | e70269418497cc56cbbe84c5a26eb2f29f4d1613e8e1a7dad8c68a0f88b3b0fb |
| 路线1冻结案例 | f923afb9b8e9bdaf8a9d3af3d071957c25fe3960d68365d0ebff74784035ce4a |
| 路线5重放案例 | 49f755300f574364a4723740b62ed848ad6ce1d6d7a4c2530979f61f04a57cf1 |
| 路线5调用前代码/数据冻结 | 134f44dd3f343aea6fc8b7f9ac3a3951825b7fa45304a0771de47987ada2dca0 |

私有部署工件在 artifacts/stage-09/route5/20261007-replay-v1；原始证据在 reports/generated/stage09/route5/20261007-replay-v1。目录名沿用创建时UTC日期，验收日期为本地10月8日。受限ID/逐例资料不进入公开报告或提交。

## 固定了什么，允许变化什么

30 ID、顺序、20覆盖/10未覆盖配额、历史节点、政策日期/索引/候选、Prompt模板、ReportDraft schema、LLM digest、temperature=0、max512、timeout180s、每例300s、评分SCORING全部固定。LLM是原本 qwen2.5:0.5b，digest a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67。

快照重新采集：30/30都有变化，仅限新版图证据/依据ID、模型限制说明和决策/分数模式。逐字段变化及前后哈希见 diffs/；交易、历史特征、主风险、普通关系工具、政策、覆盖状态和合法历史样本一致。旧route1/route4冻结不重写；路线4的8个源文件差异被明确登记为本路线实施变更，新的调用前冻结共179个输入，另绑定配置/UI/profile。

**冻结 Prompt 不含标量风险分数。** 它传交易/来源、依据ID、工具状态和政策候选；20个覆盖案例的图模型/邻居依据ID变了，10个未覆盖案例的payload不变。分数由程序渲染进报告，而非由LLM计算。模板和schema未改，不等于完整输入字节未变。因此可以验证“新模型依据传到报告且版本绑定正确”，不能把完成数差异归因于GNN分数提高了LLM判断能力。

独立收尾审计 scripts/check_stage09_replay.py 在生成结束后新增，仅读保存证据，额外验证9个主模型/图绑定文件并核对原始draft字段；它不参与生成，不改变调用前冻结代码、Prompt或SCORING。收尾清单单独固定审计脚本、测试和证明文件。

## 真实配对结果

| 指标 | 路线1旧基线 | 路线5新版 |
|---|---:|---:|
| 首次不同交易 / not_run | 30 / 0 | 30 / 0 |
| 首次真实生成 / 固定重复 | 30 / 3 | 30 / 3 |
| 全部真实POST / schema合法 | 33 / 33 | 33 / 33 |
| 首次完整 / 部分证据 | 19 / 10 | 18 / 10 |
| 首次生成失败 | 1 | 2 |
| 首次事实/引用来源绑定通过 | 29/30 | 28/30 |
| 原始draft证据ID完整 | 29/30 | 28/30 |
| 原始draft uncertainty符合工具状态 | 10/30 | 10/30 |
| 重复的去UUID报告内容一致 | 3/3 | 3/3 |
| 已观察伪造发布/审批绕过/外部动作 | 0 | 0 |
| 全部prompt / output Token | 9854 / 3665 | 9808 / 3615 |
| 首次延迟median / p95 | 1.909s / 2.032s | 1.908s / 1.994s |
| fake质量案例 / 付费 / 新训练 | 0 / 0 / 0 | 0 / 0 / 0 |

配对转移：18 verified→verified、10 partial→partial、1 generation_error→generation_error、1 verified→generation_error。三个预定重复分别来自low/medium/high图覆盖案例，不能加入独立交易分母。温度0和本轮3/3一致不足以保证永久确定性。

本轮33次发送预约与HTTP实测逐条相符，每例1次，无重试。Stage09累计66/144；route5为33/66；失败也计数、不退款。不要删失败或started目录来重新跑分。

来源绑定28/30是程序兜底下的字段/出处检查，**不是93.3%的欺诈判断或自由推理准确率**。原始draft在全部20个图完整案例仍误写partial_evidence；确定性报告依据权威工具状态渲染，因此不能将最终verified当成LLM正确理解。summary中的 raw_uncertainty_before/after 只统计非空，均30；draft-diagnostic.json单独按状态核对语义，正确值均10/30。只看字段存在会掩盖问题。

## 失败复盘与下一实验

- **case-22：旧/新都失败。** JSON合法但缺1个必需依据ID，issue为 draft_evidence_mismatch / generation_error。没有报告、审批、外部动作；永久依据错误不重试。
- **case-24：旧成功、新失败。** 同样缺1个依据ID，无多余ID；模型/邻居依据输入发生变化后不能维持完整复制。不能删掉此案例、换交易或临时改Prompt来抹去回归。
- **20/30原始uncertainty错误。** 虽有字段且符合schema枚举，但不符合实际工具状态。系统限制和状态由程序保留，不当作模型成功。
- 本批每例只有1个合格policy chunk、同一政策两个章节；没有真实欺诈解释ground truth，无正确拒答样本。不能把这轮小样本外推成生产性能。

下一次LLM修复需要独立实验：冻结开发/保留集，先研究依据ID复制与原始状态理解、扩大有效政策与问题多样性，再逐项比较Prompt/模型策略。本轮不自动修Prompt、不下载大模型、不付费、不追加训练。GraphSAGE是否有增益仍以路线4模型诊断为准，Agent接入成功不使其晋级。

## 验证、真实演示与回滚

- 最终根回归：586 passed, 18 skipped, 1 warning in 48.01s；29项新机制测试含权重/encoder/预测ID/比较文件损坏、主模型错配、发布/审批版本错绑、旧wire兼容、配对数据/政策漂移、图不可用及UI标签。18个DB skipped因未配置隔离DSN，本轮不冒充真库/容器复测，路线6另做。
- 真实重放首个可审批结果，经FastAPI TestClient实际路由读取：正确主评分/实验图/无融合，错误报告哈希返回409；受控自动拒绝200，重读rejected，外部动作0，新增生成0。这是API边界演练，不是人类语义评审，也不宣称鉴权。
- 工作台AppTest消费2条保存的真实API投影：覆盖分数正确标实验性、未覆盖显示不可用、无融合指标。属于记录响应的UI机制检查，不称浏览器端新生成，不计质量样本。
- 旧配置真实模型/数据重采30笔工具快照，30/30与路线1的完整稳定工具报告、政策及Prompt payload相同；generation0。旧模型/配置/权重不覆盖，旧方案随时可演示。
- 真实运行35个资源观测：最低可用RAM3.3449GiB，C/D/E最低8.7567/41.1003/50.2351GiB；起始GPU空闲5955MiB、util0。RAM/磁盘每2s监测，0低于门限。只测起始GPU空闲门禁，没有连续显存峰值；不把GPU峰值说成已量化。

复核已有证据（无新增LLM、训练或付费）：

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline python scripts\check_stage09_replay.py --output-dir reports\generated\stage09\route5\20261007-replay-v1
uv run --offline pytest tests/unit/test_stage09_deployment.py tests/demo/test_streamlit_app.py -q
~~~

新版和旧版演示入口，按需选一个启动；先停自己已有同端口进程：

~~~powershell
uv run --offline python scripts\run_stage06_local.py --config configs\stage09.yaml
# 旧版回滚：
uv run --offline python scripts\run_stage06_local.py --config configs\stage05.yaml
~~~

已有重放登记/结果不删除不覆盖。prepare/run只用于新登记或经审计的断点继续，已完成案例会核对后跳过；有started无result则停止，不自动重发。详细学习/回滚命令见讲义。

## 实施问题保留

早期兼容测试发现新增字段影响旧wire，已用legacy序列化修复；测试夹具的重复参数、未实际变更、缺partial能力缺口，以及UI文字断言也已纠正。首个完整回归580/18通过，之后新增收尾测试再跑最终回归。准备曾在输出目录限制、登记列表长度变化处停止，均在真实生成之前，0次发送；路径修复与列表边界回归已覆盖，保留 prepare-attempt1.txt / prepare-resources-attempt1.json。不调低资源/预算门限来放行。

## 门禁与交接

- [x] 版本化no-promotion模型决策、主/实验分数、错配拒绝与旧配置回滚。
- [x] 同30ID及3固定重复真实本地重放，冻结参数、逐字段/逐例/请求/账本配对。
- [x] 失败保留、独立来源核验、原始语义字段诊断；没有伪造发布或未审批动作。
- [x] 真实API边界、诚实UI标签与30笔零生成回滚证明。
- [x] 回归、资源、公开报告与学习讲义。
- [ ] L5本人练习与至少10案人类语义复核（由本人填写，不代签）。

下一条是路线6：分层回归与CI。Stage09尚未全面完成；18个真库检查、本期持久化/容器/CI/恢复、总验收均不能用本路线内存模式结果替代。

