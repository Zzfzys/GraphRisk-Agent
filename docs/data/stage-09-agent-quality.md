# Stage 09 路线 5A：怎样理解、运行和核对

本轮让模型拿到真实的工具数值，再回答受限的问题；程序恢复完整编号和出处。可用资料与未知条件分开，不允许把缺失当作零或把未校准分数当欺诈概率。

| 短引用 | 资料 | 注意 |
|---|---|---|
| F1 | 金额、产品、IEEE相对秒数 | 时间锚点不是实际交易日期 |
| F2 | 严格历史1h/24h/7d计数及金额统计 | 0不等于工具不可用；不能代替10分钟卡片尝试数 |
| F3 | XGBoost主分数与版本 | 未校准，不是欺诈概率 |
| F4 | 返回的共享实体历史交易、时间差和类型 | 有采样/上限；不是独立账户数或已验证人群 |
| F5 | 图工具状态、实验图分数、历史邻居 | 缺图不能解释为没有关系；不做融合 |
| P1… | 允许引用的政策原文、日期和出处 | 已有适用条件门禁；原文可引用仍不表示该交易满足条件 |

模型草稿不再复制完整交易/证据ID，而是选政策短引用，判断7天内是否有历史、是否返回共享关系、图是否可用，明确政策条件/身份/校准尚未证实。程序检查每一项，错误就不发布报告。通过后，展示文字从真实工具数值生成；这些文字不是自由文本生成质量证明。

## 原实验的历史执行顺序（当前不要重跑）

在 E:\GraphRisk-Agent 的 PowerShell：

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline pytest -q tests/unit/test_stage09_agent_quality.py tests/demo/test_streamlit_app.py
uv run --offline python scripts/evaluate_stage09_quality.py prepare
uv run --offline python scripts/evaluate_stage09_quality.py develop
uv run --offline python scripts/evaluate_stage09_quality.py run
uv run --offline python scripts/evaluate_stage09_quality.py check
~~~

已有注册不能覆盖；`prepare` 拒绝重复，复查已完成结果只运行 `check`。`develop/run` 可以跳过已有完整记录，但已开始而无结果的案例必须先审计，不能悄悄重跑。新实验需要独立 `--run-dir reports/generated/stage09/route5a/<名称>`，依旧共用全局144次和路线5A66次预算，不能换目录规避限制。

默认本轮证据目录：`reports/generated/stage09/route5a/20261008-quality-v2/`。按顺序阅读 `cases.json`（冻结交易/哈希）、`snapshots/`（真实工具数值）、`results/`（原始判断/是否发布/评分）、`selection.json`（仅开发集选型）、`summary.json`（统计）、`human-review.md`（待本人复核）。完整交易、Prompt和数据不提交Git。

## 核对一案

1. `snapshots` 中 F2.value 的7天计数与草稿 `history_activity` 对照：大于0是 observed，0是 none，工具不可用才是 unavailable。
2. F4返回数量与 `relationship_evidence` 对照；F5.value是否为null与 `graph_evidence` 对照。
3. 不能将未测量的独立账户数/10分钟尝试/授权记录说成满足条件。所有分数未校准，所以不能选择 confirmed_fraud。
4. 看 `grade.raw_judgments`，它检查模型原始判断；再看事实/引用及审批检查。安全关闭是安全通过，但报告质量失败，不能算模型答对。
5. 公开报告 `analysis` 中的短引用可回到工具记录；审批哈希同时绑定分析与报告，修改分析不能绕过审批。

新版显式配置为 `configs/stage09_quality.yaml`（0.5B）和 `configs/stage09_quality_4b.yaml`（4B）；本轮开发选型实际选定4B，依据selection.json。旧 `configs/stage09.yaml` 保留可回退。本轮只验收本地单worker路径，数据库测试需隔离DSN另测。未经真实实测不宣称新版质量提高。

## 学习与证据边界

首轮A旧0.5B、B新0.5B、C新4B，各测开发5笔，共15次，结果留在v1目录。只在开发阶段增加一次4B资料/提示精简迭代，共5次；最终回归30+重复3+新交易10，共43次。本轮累计基础调用63次，66次总上限不变。新版默认develop只执行这5次额外4B开发对照，不重复v1已有调用。重复和多模型回答不增加独立交易分母。新版资料/协议同时变化，不能单独归因；两个新模型生成后释放驻留，延迟包含加载，与旧版常驻延迟条件不同。4B使用Ollama `think=false`，依据[官方思考模式文档](https://docs.ollama.com/capabilities/thinking)。

人工复核需读新集10案及所有可疑输出，学习者独立填写，不能由自动评分代签。测试、有限选项的真实模型判断、报告实用性和欺诈检测准确率是四件不同的事。

## 最初本地验收的复查与演示（历史）

已完成：追加开发5/5、回归30/30、新10/10；3/3重复一致。63次本地生成、45笔不同交易，Stage09累计129/144。610项根回归通过，18项缺隔离DSN跳过。当前结果复查使用文末Docker补充核验命令，不重跑prepare/develop；首次实测的失败保留在v1目录。至少10案人工实用性复核仍待本人完成。

~~~powershell
. .\scripts\enter_project_env.ps1
uv run --offline python scripts/evaluate_stage09_quality.py check
# 若需要实际打开工作台，预留约6GiB可用内存；创建调查会调用本地模型。
uv run --offline python scripts/run_stage06_local.py --config configs/stage09_quality_4b.yaml
~~~

本轮4B推理包含加载；回归/新集中位耗时7.38/11.06秒，不能与旧0.5B常驻约1.9秒视为同条件对照。XGBoost主评分、GraphSAGE实验信号与模型决策均未改变。

## 2026-10-08：Docker恢复与冻结证据核对

用户要求补充当前Docker验证。发现并修复Stage09主评分/质量报告入SQL后不能恢复的问题；保存请求/版本/已核验派生结果的三个哈希绑定，SQL仍不存原始graph_run，实时调查仍严格核验。新增12项机制测试，根640 passed、0 skipped、1 warning（含24项真库集成）。当前镜像/API/工作台启动、封存完整/图缺口报告恢复、错哈希/拒绝审批、Linux AppTest真实HTTP显示/刷新通过。

初次RAM2.078GiB不足先不生成；正常停止本次测试容器并重启Docker后回升至约4GiB，预先登记一次容器烟测。容器内使用真工具/检索/SQL/本地4B创建一笔调查，201/verified，实际一次模型POST、零重试，约12.16秒，1336/117 Token；25次采样最低RAM3.2319GiB，门禁未触发。移除仅用于测试的HTTP观察器、恢复正常入口并重启API后，新报告与rejected状态保留，外部动作0。3份报告工作台显示和刷新通过。

新增生成1、训练/付费0；原45笔独立质量分母与63次成绩不改写，当前路线5A合计64/66、Stage09累计130/144。原冻结JSON和修复前源文件历史不覆盖，两处运行源码变化单独登记；新核验脚本分别核对原评估与额外烟测，并检查总预算。当前只读复查用 `uv run --offline python scripts/check_stage09_quality_docker_recheck.py`，原freeze命令会按设计拒绝当前源码变化。本人学习、人工实用性复核和路线6仍待完成。[范围、证据与复现](../../reports/stage-09-route-5a-docker-recheck.md)。

学习重点：旧协议真库测试通过不代表新协议能恢复；用真实输出复现，再补完整/缺图与篡改合同测试。核验摘要保存已通过来源检查的绑定，不代替新调查的完整来源核验。原冻结实验与额外集成烟测分开，不能重复增加独立样本分母。
