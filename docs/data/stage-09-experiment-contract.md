# Stage09 实验合同 v1：版本冻结、数据用途与预算

> 建立：2026-10-07；对应路线 0
> 机器合同：[configs/stage09_experiment.json](../../configs/stage09_experiment.json)
> 实现：[冻结/核验模块](../../src/graphrisk/agent/stage09_preflight.py) · [CLI](../../scripts/freeze_stage09.py)
> 实际门禁状态以[路线 0 报告](../../reports/stage-09-route-0-preflight.md)为准，合同存在不代表所有检查通过。

## 1. 两个运行配置

**主要真实基线。** 显式使用 `configs/stage05.yaml`、`Stage06Service.from_config`、单 worker 内存存储和 TF-IDF，不受终端旧 DSN/默认 API 环境隐式切换。所有真实交易留本机，生成使用固定 Ollama；没有托管 fallback。默认记录容量 32、记录 TTL 24h、审批 TTL 30min；逐例结果立即落独立文件，不依赖内存记录永久保存。

**持久化补充门禁。** `configs/stage07_pgvector.yaml` 为宿主本地 PG 配置，`configs/stage07_compose.yaml` 为容器配置。它们另列版本和真实数据库检查，不把 PG 候选的输出混进 TF-IDF 主质量表。Docker 当前磁盘位置与测试库状态单独记环境事实。

## 2. 本次实际版本链

| 项 | 冻结值/来源 |
|---|---|
| 生成模型 | qwen2.5:0.5b，digest a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67 |
| embedding 候选 | qwen3-embedding:0.6b，digest ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d；主 TF-IDF 不调用它 |
| 生成请求 | loopback HTTP，temperature 0，非流式，512 输出 Token 上限；上下文长度沿用现有模型/服务默认，未显式配置 num_ctx，不虚称固定有效长度 |
| Prompt 版本 | stage05-route5-extractive-v1；实际构造代码内容哈希包含在清单 |
| ReportDraft schema SHA256 | 86e45dad61ae86ab86a77e16dfd5016be1c73267f2870c57eb2b4faa8add0540 |
| 数据 | IEEE-CIS；canonical 与 point-in-time Parquet 的完整文件 SHA256 |
| XGBoost 主工具 | Stage02 champion / xgboost-v1；与小集合 controlled 模型分开 |
| 图比较目录 | artifacts/stage-04/ieee-cis/comparisons/stage05-route8 |
| 当前 GraphSAGE 权重 SHA256 | b1e4696dfca80c617ddeadf2fb2ae310b39a6e445fe9127865ea56901d26e69f |
| 图 manifest 内容 SHA256 | 8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80 |
| 当前图训练 | batch256 / hidden64 / dropout0.2 / lr0.001 / max50 / patience20 / seed20260921；最佳6、停止26 |
| 当前图验证/测试 PR-AUC | 0.170545 / 0.085999；历史已观察 |
| 融合 | stage05-route8，alpha0.5；不是 Stage02 champion 的直接替代 |
| 主政策索引 | TF-IDF，16 chunks；版本 9eade9b4c7f38df917267731ebe1d19a0aa8c270cbe1b8234a64d64b1605b3e7 |
| 政策 corpus SHA256 | 5944138255497bb595d46513a7100045363d1f35d99fd2607954567e64145513 |
| 请求政策日期 / relation_limit | 2026-07-01 / 3；原创模拟政策日期，和 IEEE 人工日历锚点分开解释 |

清单 `reports/generated/stage09/route0/20261007T093634Z/frozen-inputs.json` 冻结 161 个文件，内容 SHA256 `883f8fc83ff8789f6247e77eb05057bc927d76704e334f9841ef354d21c808d3`。包括配置、根锁、实现代码、图源/encoder/ID、权重/history、比较/预测、政策原文/索引和主 champion；不读取/记录秘密、原始 CSV 行或交易 ID 列表内容。

这是内容漂移检测，不是签名、恶意攻击证明或一份完整数据备份。运行前重新检查；发生变更先记录变更理由并保留旧清单，不用“更新哈希”掩盖实验条件变化。跨机器路径采用仓库相对路径；冻结创建需在当前 Windows 工程中解析 legacy checkpoint，Linux 容器用既有服务的验证/重绑定能力另验。

早期 GraphSAGE 验证/测试 0.178343 / 0.103733 与当前权重不同，不混合描述；metadata 与 comparison 绑定已核对。

## 3. 数据用途与时间规则

- 图训练/诊断：固定现有 50,000 train 和 10,000 validation 目标。特征 encoder 只 fit train；训练图不含未来 validation/test 关系。
- Agent 选择：IEEE 验证期 88,581 笔中按冻结主 XGBoost 排序，选 30 个不同交易。它们是工程/真实生成诊断集，不作图模型无偏最终测试。
- 图覆盖：现有验证图覆盖 10,000 个目标，另 78,581 验证目标不在该集合；不能把“图无预测覆盖”误报成交易不存在。路线1再核对成员与实际工具行为。
- 历史只用严格早于目标事件；同时间视为同时；历史节点特征按自己的截止构造。较早已发生验证/测试事件能否用于推理，沿既有 manifest 规则并在路线3审计；标签不进入上下文/特征/采样。
- 原 test 有历史指标、比较结果及样例访问，标为 historically_observed_not_blind。候选选型不读取 test 标签，最终固定方案最多一次批量复核；没有真正新窗口不得称独立盲测。
- 主工具未校准分数、启发式 identity/entity、原创模拟政策都标明限制；欺诈标签不是报告解释真值。

路线0完成时尚未选择任何真实案例 ID。路线1必须在生成前冻结案例清单、工具快照和评分器，不在看到失败后换样本。

## 4. 预注册案例选择与评分

低/中/高为主 XGBoost 分数的等人数三分位层，按 score、transaction_id 的稳定排序分层，每层10笔；不是0.3/0.7业务阈值。选择种子20261007。层内按 SHA256(seed + transaction_id) 排序，ID作为哈希冲突的次序。

优先图覆盖配额为低7/中7/高6，图不覆盖为3/3/4，共20+10；两组都属于验证期。至少5笔应有合法历史局部图边，另至少5笔边界（图不覆盖即可满足，不制造缺失）。若某层配额不足，在生成前报告候选数量、登记同层补齐/跨层调整的唯一规则和影响；不能看模型回答后改分层。不承诺一定有5笔达到风险阈值的“高风险邻居”，普通合法历史边与工具展示的高风险邻居分开统计。

每例程序评分包含执行/状态/schema、交易/模型/事实绑定、政策原文/版本/日期/条件/例外、预算/重试/审批/外部动作。人工按固定依据表抽核至少10案及全部可疑发布；规则和抽核顺序生成前冻结。机器 verified 只说明已做的确定性检查，不等于完整语义正确。

分母分别记录30首次/30不同交易、实际生成交易、生成尝试、三个重复、确定性停止、失败、正确拒答、not_run。至少20个不同交易实际尝试本地生成；30案有真实结果或确定性停止、0not_run才满足基线证据完整。正确拒答的判据事前冻结，不能让“全拒答”看上去是高完成率。全部失败保留，安全关闭与输出质量分别打分。

## 5. 真实调用与资源预算

- 串行、每案最多2次生成；max_steps12、max_policy_queries2、max_retries1。
- 单次生成180s、全案300s、输出512 Token；整体deadline到期停止，失败也计入时间/尝试。
- 两阶段各30首次+3固定重复：最多132次；5开发案10次、1预热2次，阶段最多144次真实生成尝试。失败/取消是否已发送请求按实际记录，不默认为0。
- 付费调用0；无托管fallback。当前服务旧 API budget30/stop24只是上游历史配置，本合同不授权使用该额度。
- 主TF-IDF无需 embedding；若后续显式测试PG/图RAG embedding，另记 embedding 请求/Token/时间，不能偷偷从生成尝试分母中消失。
- 开始重任务前可用RAM至少3GiB、GPU空闲至少4096MiB且利用率不高于10%；C/D/E至少5/5/10GiB。这些是本机保守起始线，不是精确OOM预测；持续阈值与停止监测由路线1/训练路线接入。
- 本次工具只验证合同和起始资源，不声称已经实现跨进程调用计数器或训练deadline。路线1/2～4必须把预算接入执行入口；到限保留未运行，不能降低验收数量以关闭路线。

## 6. GraphSAGE 训练预注册

六个候选：(lr,dropout,hidden) = (0.001,0.2,64)、(0.0005,0.2,64)、(0.001,0.4,64)、(0.0005,0.4,64)、(0.001,0.2,128)、(0.0005,0.2,128)。

固定batch256、max_epoch50、patience10；基础种子20260921，最佳候选追加20260928和20261007。较大历史采样 [10,10] 按本项目映射解释，不声称标准层间fanout。

基础/消融最多5个GNN训练（完整、去边、合法扰乱、去历史统计、较大历史），候选最多6，追加种子2，合计最多13；相同运行可复用。MLP/XGBoost基础对照各一次。每次墙钟上限3600s，全GNN训练合计21600s（6h）；时间包括构图/训练/验证相关工作，不能只算CUDA kernel。预处理和独立图构建另记时间/资源，主动诊断工作12h已含阶段44h。

候选validation-only；主选型依据项目相同实现的验证PR-AUC/AP，必须核对公式，辅助ROC-AUC与Top1%/5%、早晚/覆盖分层、延迟/资源。增益不足、种子不稳定或成本高不晋级；不预设图必胜。bug修复优先，改变特征/采样就新manifest/目录，训练与服务同语义。候选冻结后至多一次已观察测试复核，不用测试再找下个参数。

## 7. 复核命令与路线0解除条件

在 E:\GraphRisk-Agent 的新 PowerShell：

```powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest tests/unit/test_stage09_preflight.py -q
uv run --offline python scripts/freeze_stage09.py --check reports/generated/stage09/route0/20261007T093634Z/frozen-inputs.json
```

新冻结使用唯一输出路径，`--output` 限于忽略的stage09 JSON，已有文件拒绝覆盖。完整清单也绑定代码；代码改变后检查失败是预期，先归档实验变更，不能静默补哈希。

路线0需版本/合同/资源/实际服务加载/固定digest与隔离DB门禁均通过；真库回归skipped不算通过。首次失败保留，新重测使用新目录/库。通过后才开始路线1，路线1基线补齐后再训练诊断。

路线1后续记录（2026-10-07）：已生成前冻结30不同交易/评分并实际完成33次本地生成；零付费/训练，原161文件无漂移。工程证据与人类复核状态见[路线1报告](../../reports/stage-09-route-1-real-agent-baseline.md)，本合同原预算/选型保持。
