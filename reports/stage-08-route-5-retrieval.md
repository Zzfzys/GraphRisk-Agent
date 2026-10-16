# Stage08 路线 5：可复现索引、文本与两跳图检索验收

日期：2026-09-30。状态：路线 5 工程验收完成；L5 个人学习待本人完成。Stage08 仍在进行，路线 6～8 与路线 3 正式账单对账未关闭。

本次实现独立标准库检索模块及评估器，不修改 GraphRisk 服务、GraphSAGE 权重、根/子项目依赖锁或冻结题。真实本地 embedding 构建两次；冻结 30 问在 text/graph/hybrid 各跑一次，共 90 条真实检索观测。没有调用生成模型或托管 API，没有新增 API 费用。

## 固定版本与可复现证据

| 项目 | 值 |
|---|---|
| embedding | qwen3-embedding:0.6b / 1024 维 |
| 模型 digest | `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d` |
| 冻结语料根 SHA256 | `66d716823d71db9f78452c361ef89d04b90fa5810bd32380ffa77219de32d764` |
| 索引 payload 版本 | `84d09b81aec41cbebdbeed7b6e634a9147b786b5c6c2efb41595e37839bd8c2e` |
| index-first.json 文件 SHA256 | `02a134f896825b17087e93f376044ea617bd211aba31876311be54bd3c54f75e` |
| index-second.json 文件 SHA256 | `02a134f896825b17087e93f376044ea617bd211aba31876311be54bd3c54f75e` |
| 检索合同 SHA256 | `dd45635088bb54bad15ddec584027671c19d6426f22f136488a2440903835d85` |
| evaluation-v1.json 文件 SHA256 | `b456bce73d4a3e809451f38a4db65618e75217781726402e0212ededffe83003` |

索引包含 24 文档、58 段、22 实体、40 来源边；文档 embedding 58 段 × 2，评估查询 embedding 29 次（早于全部资料有效期的一题不调用 embedding）。两次构建 byte-for-byte 相同，源语料/问题、合同未因结果修改。索引归一化后保留 8 位小数；这是本机同模型实证，不承诺跨硬件逐位一致。

工件在忽略目录 [route5](generated/stage08/route5/)：index-first.json、index-second.json、evaluation-v1.json。生成物不提交，复现时创建新文件名。文件哈希与 payload 版本不同是正常的；后者不含外层版本字段。完整代码/合同哈希记录于评估 JSON。

## 同集三模式实测

完整证据覆盖：一题要求的所有来源片段都已返回，分母为 22 个可回答问题。片段召回分母 32 按逐题累计；一段可在不同问题中重复计数。文本模式没有图路径输出，因此路径命中不是文本质量的公平单项对照。

| 模式 | 直答证据 /8 | 两跳完整证据 /8 | 版本/冲突完整证据 /6 | 全部完整证据 /22 | 必要片段召回 /32 | 两跳目标路径 /8 | 错误/无效出处 | P50/P95 ms |
|---|---:|---:|---:|---:|---:|---:|---|---|
| graph | 8 | 8 | 4 | 20 | 30 | 8 | 0/0 | 156.0/188.0 |
| hybrid | 8 | 8 | 6 | 22 | 32 | 8 | 0/0 | 328.0/344.0 |
| text | 8 | 5 | 6 | 19 | 29 | 不输出路径 | 0/0 | 156.0/187.0 |

完整证据覆盖率为 text 19/22（86.36%）、graph 20/22（90.91%）、hybrid 22/22（100%）。**这不是 LLM 答对率；22/22 不意味着路线 6/7 已通过。** 耗时含源文件完整性检查，测量在本机且与根回归有重叠，不是隔离性能基准或线上 SLA；评估按题依次 text→graph→hybrid，无并发请求。

无答案四题：text/hybrid 为 3 个 ok、1 个 no_match；graph 为 2 个 ok、2 个 no_match。ok 只说明存在相关候选，不能据此回答 SLA 或未记录依赖。注入四题：text/hybrid 都召回攻击段，graph 均 no_match；攻击段仍是不可信数据。本轮未运行生成或动作，不能用“没有执行”冒充真实模型注入防御通过。

### 图带来的证据增量，以及文本胜出的案例

- graphrag-09/10/12：文本 Top-6 缺少依赖服务的 owner，graph/hybrid 返回完整两跳。以 09 为例，service-gateway-v1#dependency → service-identity-v1#owner，边为 e-gateway-dependency-v1 → e-identity-owner-v1，终点 team-security，两个来源文档各有原文。
- graphrag-21/22：graph 返回当前正式责任团队，但缺少解释交接未生效的草案/取消片段；text/hybrid 找齐两份材料。图模式不把 PROPOSES_OWNER 直接当正式 owner 是正确的边界，但只靠该图模式不足以完整解释冲突。
- hybrid 在本小型原创模拟集找齐全部必要证据，仍不能分离 embedding 与图路径预留的各自贡献。实体精确命名与受限中文意图规则适合本 schema，未证明对真实长文、别名或自由问题的泛化。未根据本次成绩修改阈值、Top-K 或冻结题。

## 合同、错误与测试证据

- 独立项目：`uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -v` → **51 tests / OK，8.629s**（原 26 + 新 25）。新增合同测试使用假向量；真实质量数据仅来自上面的真实 embedding 索引与查询。
- 根回归：`uv run --offline pytest -q` → **451 passed / 18 skipped / 1 warning，40.16s**。18 项因 PostgreSQL/pgvector DSN 未配置跳过；警告为 Starlette anyio 弃用，本轮未宣称完成真库回归。
- 日期：订单 6/30 与 7/1 版本切换，在 text/graph/hybrid 中排除失效版；未来/过期文档不参与统计排名，边同时受源文档日期约束。
- 损坏：校验和篡改、重新计算哈希后的来源/词频/段清单/向量数量/维度/范数/digest/合同变更、JSON NaN/重复键/缺文件均被拒绝。原始语料变更由已有冻结门禁拒绝。
- 服务与界限：embedding 断连/错误形状/错 digest/零向量/非有限值、重定向和截止时间被检测；hybrid 不偷偷退回文本成功。已有索引的 text/graph 查询不调用 embedding。两跳、3 路径、Top-6、128 扩展与循环界限有测试。
- 评估器独立核验片段原文、日期、路径连续性及出处齐全；伪造边/引文、缺一跳、只命中部分来源都不能得完整证据分。黄金答案不会传给检索器或 embedder。
- 根锁、独立锁、Stage05/Stage05 dense/Stage07 pgvector 默认配置及冻结根哈希与路线 4 结束时相同；未安装新框架。

## 逐题候选和路径

下表保留全部 90 条观测，顺序为返回顺序。每条路径的完整边/原文/日期见本地 evaluation-v1.json。可回答问题列“齐/缺”；无答案/注入列“未测回答”，不把返回候选当作答对或拒答正确。

| 问题 ID | 组 | 模式 | 状态/必要证据 | 返回 chunk_id（顺序） | 返回 edge_id 路径 |
|---|---|---|---|---|---|
| graphrag-01 | direct | text | ok / 齐 | service-gateway-v1#owner<br>service-gateway-v1#dependency<br>service-gateway-v1#runbook<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner | — |
| graphrag-01 | direct | graph | ok / 齐 | service-gateway-v1#owner | e-gateway-owner-v1 |
| graphrag-01 | direct | hybrid | ok / 齐 | service-gateway-v1#owner<br>service-gateway-v1#dependency<br>service-gateway-v1#runbook<br>change-identity-v1#proposal<br>change-search-v1#proposal<br>service-audit-v1#owner | e-gateway-owner-v1 |
| graphrag-02 | direct | text | ok / 齐 | service-orders-v2#owner<br>service-orders-v2#runbook<br>service-orders-v2#dependency<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-gateway-v1#owner | — |
| graphrag-02 | direct | graph | ok / 齐 | service-orders-v2#owner | e-orders-owner-v2 |
| graphrag-02 | direct | hybrid | ok / 齐 | service-orders-v2#owner<br>service-orders-v2#runbook<br>service-orders-v2#dependency<br>change-orders-v1#proposal<br>service-gateway-v1#owner<br>change-orders-v1#target | e-orders-owner-v2 |
| graphrag-03 | direct | text | ok / 齐 | service-billing-v1#owner<br>service-orders-v2#owner<br>service-billing-v1#runbook<br>service-audit-v1#owner<br>service-identity-v1#owner<br>service-inventory-v1#owner | — |
| graphrag-03 | direct | graph | ok / 齐 | service-billing-v1#owner | e-billing-owner-v1 |
| graphrag-03 | direct | hybrid | ok / 齐 | service-billing-v1#owner<br>service-billing-v1#runbook<br>service-orders-v2#owner<br>service-audit-v1#owner<br>service-billing-v1#dependency<br>runbook-billing-v1#maintainer | e-billing-owner-v1 |
| graphrag-04 | direct | text | ok / 齐 | service-identity-v1#owner<br>service-identity-v1#runbook<br>service-identity-v1#dependency<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-inventory-v1#owner | — |
| graphrag-04 | direct | graph | ok / 齐 | service-identity-v1#owner | e-identity-owner-v1 |
| graphrag-04 | direct | hybrid | ok / 齐 | service-identity-v1#owner<br>service-identity-v1#runbook<br>service-identity-v1#dependency<br>service-audit-v1#owner<br>service-billing-v1#owner<br>change-identity-v1#target | e-identity-owner-v1 |
| graphrag-05 | direct | text | ok / 齐 | service-notify-v1#owner<br>service-notify-v1#dependency<br>service-notify-v1#runbook<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner | — |
| graphrag-05 | direct | graph | ok / 齐 | service-notify-v1#owner | e-notify-owner-v1 |
| graphrag-05 | direct | hybrid | ok / 齐 | service-notify-v1#owner<br>service-notify-v1#dependency<br>service-notify-v1#runbook<br>service-billing-v1#owner<br>service-audit-v1#owner<br>service-search-v2#owner | e-notify-owner-v1 |
| graphrag-06 | direct | text | ok / 齐 | service-inventory-v1#owner<br>service-inventory-v1#runbook<br>service-inventory-v1#dependency<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner | — |
| graphrag-06 | direct | graph | ok / 齐 | service-inventory-v1#owner | e-inventory-owner-v1 |
| graphrag-06 | direct | hybrid | ok / 齐 | service-inventory-v1#owner<br>service-inventory-v1#runbook<br>service-inventory-v1#dependency<br>service-billing-v1#owner<br>change-inventory-v1#target<br>service-audit-v1#owner | e-inventory-owner-v1 |
| graphrag-07 | direct | text | ok / 齐 | service-search-v2#owner<br>service-search-v2#runbook<br>service-search-v2#dependency<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner | — |
| graphrag-07 | direct | graph | ok / 齐 | service-search-v2#owner | e-search-owner-v2 |
| graphrag-07 | direct | hybrid | ok / 齐 | service-search-v2#owner<br>service-search-v2#runbook<br>service-search-v2#dependency<br>service-billing-v1#owner<br>service-audit-v1#owner<br>change-search-v1#target | e-search-owner-v2 |
| graphrag-08 | direct | text | ok / 齐 | service-audit-v1#owner<br>service-audit-v1#dependency<br>service-audit-v1#runbook<br>service-billing-v1#owner<br>service-identity-v1#owner<br>service-inventory-v1#owner | — |
| graphrag-08 | direct | graph | ok / 齐 | service-audit-v1#owner | e-audit-owner-v1 |
| graphrag-08 | direct | hybrid | ok / 齐 | service-audit-v1#owner<br>service-audit-v1#dependency<br>service-audit-v1#runbook<br>service-billing-v1#owner<br>service-identity-v1#owner<br>service-search-v2#owner | e-audit-owner-v1 |
| graphrag-09 | two_hop | text | ok / 缺 | service-gateway-v1#dependency<br>service-gateway-v1#owner<br>service-gateway-v1#runbook<br>service-audit-v1#dependency<br>service-billing-v1#dependency<br>service-identity-v1#dependency | — |
| graphrag-09 | two_hop | graph | ok / 齐 | service-gateway-v1#dependency<br>service-identity-v1#owner | e-gateway-dependency-v1 → e-identity-owner-v1 |
| graphrag-09 | two_hop | hybrid | ok / 齐 | service-gateway-v1#dependency<br>service-identity-v1#owner<br>service-gateway-v1#owner<br>service-gateway-v1#runbook<br>service-notify-v1#dependency<br>service-orders-v2#owner | e-gateway-dependency-v1 → e-identity-owner-v1 |
| graphrag-10 | two_hop | text | ok / 缺 | service-orders-v2#dependency<br>service-orders-v2#owner<br>service-orders-v2#runbook<br>service-audit-v1#dependency<br>service-billing-v1#dependency<br>service-identity-v1#dependency | — |
| graphrag-10 | two_hop | graph | ok / 齐 | service-orders-v2#dependency<br>service-inventory-v1#owner | e-orders-dependency-v2 → e-inventory-owner-v1 |
| graphrag-10 | two_hop | hybrid | ok / 齐 | service-orders-v2#dependency<br>service-inventory-v1#owner<br>service-orders-v2#owner<br>service-orders-v2#runbook<br>service-billing-v1#dependency<br>service-billing-v1#owner | e-orders-dependency-v2 → e-inventory-owner-v1 |
| graphrag-11 | two_hop | text | ok / 齐 | service-billing-v1#dependency<br>service-billing-v1#owner<br>service-orders-v2#dependency<br>service-orders-v2#owner<br>service-audit-v1#dependency<br>service-identity-v1#dependency | — |
| graphrag-11 | two_hop | graph | ok / 齐 | service-billing-v1#dependency<br>service-orders-v2#owner | e-billing-dependency-v1 → e-orders-owner-v2 |
| graphrag-11 | two_hop | hybrid | ok / 齐 | service-billing-v1#dependency<br>service-orders-v2#owner<br>service-billing-v1#owner<br>service-audit-v1#dependency<br>service-billing-v1#runbook<br>service-orders-v2#dependency | e-billing-dependency-v1 → e-orders-owner-v2 |
| graphrag-12 | two_hop | text | ok / 缺 | service-identity-v1#dependency<br>service-identity-v1#owner<br>service-audit-v1#dependency<br>service-billing-v1#dependency<br>service-inventory-v1#dependency<br>service-notify-v1#dependency | — |
| graphrag-12 | two_hop | graph | ok / 齐 | service-identity-v1#dependency<br>service-audit-v1#owner | e-identity-dependency-v1 → e-audit-owner-v1 |
| graphrag-12 | two_hop | hybrid | ok / 齐 | service-identity-v1#dependency<br>service-audit-v1#owner<br>service-identity-v1#owner<br>service-identity-v1#runbook<br>service-gateway-v1#dependency<br>service-audit-v1#dependency | e-identity-dependency-v1 → e-audit-owner-v1 |
| graphrag-13 | two_hop | text | ok / 齐 | service-notify-v1#runbook<br>service-notify-v1#owner<br>service-notify-v1#dependency<br>runbook-billing-v1#maintainer<br>runbook-gateway-v1#maintainer<br>runbook-identity-v1#maintainer | — |
| graphrag-13 | two_hop | graph | ok / 齐 | service-notify-v1#runbook<br>runbook-gateway-v1#maintainer | e-notify-runbook-v1 → e-gateway-maintainer |
| graphrag-13 | two_hop | hybrid | ok / 齐 | service-notify-v1#runbook<br>runbook-gateway-v1#maintainer<br>service-notify-v1#owner<br>runbook-billing-v1#maintainer<br>runbook-orders-v1#maintainer<br>runbook-search-v1#maintainer | e-notify-runbook-v1 → e-gateway-maintainer |
| graphrag-14 | two_hop | text | ok / 齐 | service-inventory-v1#runbook<br>runbook-inventory-v1#maintainer<br>service-inventory-v1#owner<br>service-inventory-v1#dependency<br>runbook-billing-v1#maintainer<br>runbook-gateway-v1#maintainer | — |
| graphrag-14 | two_hop | graph | ok / 齐 | service-inventory-v1#runbook<br>runbook-inventory-v1#maintainer | e-inventory-runbook-v1 → e-inventory-maintainer |
| graphrag-14 | two_hop | hybrid | ok / 齐 | service-inventory-v1#runbook<br>runbook-inventory-v1#maintainer<br>service-inventory-v1#owner<br>runbook-orders-v1#maintainer<br>runbook-billing-v1#maintainer<br>runbook-search-v1#maintainer | e-inventory-runbook-v1 → e-inventory-maintainer |
| graphrag-15 | two_hop | text | ok / 齐 | service-search-v2#runbook<br>runbook-search-v1#maintainer<br>service-search-v2#owner<br>service-search-v2#dependency<br>runbook-billing-v1#maintainer<br>runbook-gateway-v1#maintainer | — |
| graphrag-15 | two_hop | graph | ok / 齐 | service-search-v2#runbook<br>runbook-search-v1#maintainer | e-search-runbook-v2 → e-search-maintainer |
| graphrag-15 | two_hop | hybrid | ok / 齐 | service-search-v2#runbook<br>runbook-search-v1#maintainer<br>service-search-v2#owner<br>runbook-billing-v1#maintainer<br>runbook-orders-v1#maintainer<br>runbook-gateway-v1#maintainer | e-search-runbook-v2 → e-search-maintainer |
| graphrag-16 | two_hop | text | ok / 齐 | service-audit-v1#runbook<br>service-audit-v1#owner<br>service-audit-v1#dependency<br>runbook-billing-v1#maintainer<br>runbook-gateway-v1#maintainer<br>runbook-identity-v1#maintainer | — |
| graphrag-16 | two_hop | graph | ok / 齐 | service-audit-v1#runbook<br>runbook-billing-v1#maintainer | e-audit-runbook-v1 → e-billing-maintainer |
| graphrag-16 | two_hop | hybrid | ok / 齐 | service-audit-v1#runbook<br>runbook-billing-v1#maintainer<br>service-audit-v1#owner<br>runbook-search-v1#maintainer<br>runbook-orders-v1#maintainer<br>runbook-inventory-v1#maintainer | e-audit-runbook-v1 → e-billing-maintainer |
| graphrag-17 | version_conflict | text | ok / 齐 | service-orders-v1#owner<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-gateway-v1#owner<br>service-identity-v1#owner<br>service-inventory-v1#owner | — |
| graphrag-17 | version_conflict | graph | ok / 齐 | service-orders-v1#owner | e-orders-owner-v1 |
| graphrag-17 | version_conflict | hybrid | ok / 齐 | service-orders-v1#owner<br>service-gateway-v1#owner<br>service-billing-v1#owner<br>service-audit-v1#owner<br>service-notify-v1#owner<br>service-search-v1#owner | e-orders-owner-v1 |
| graphrag-18 | version_conflict | text | ok / 齐 | service-orders-v2#owner<br>service-search-v1#owner<br>service-orders-v2#runbook<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-gateway-v1#owner | — |
| graphrag-18 | version_conflict | graph | ok / 齐 | service-orders-v2#owner | e-orders-owner-v2 |
| graphrag-18 | version_conflict | hybrid | ok / 齐 | service-orders-v2#owner<br>service-orders-v2#runbook<br>service-search-v1#owner<br>service-billing-v1#owner<br>service-gateway-v1#owner<br>change-orders-v1#proposal | e-orders-owner-v2 |
| graphrag-19 | version_conflict | text | ok / 齐 | service-search-v1#owner<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner<br>service-inventory-v1#owner<br>service-notify-v1#owner | — |
| graphrag-19 | version_conflict | graph | ok / 齐 | service-search-v1#owner | e-search-owner-v1 |
| graphrag-19 | version_conflict | hybrid | ok / 齐 | service-search-v1#owner<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner<br>service-notify-v1#owner<br>service-inventory-v1#owner | e-search-owner-v1 |
| graphrag-20 | version_conflict | text | ok / 齐 | service-search-v2#owner<br>service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner<br>service-inventory-v1#owner<br>service-notify-v1#owner | — |
| graphrag-20 | version_conflict | graph | ok / 齐 | service-search-v2#owner | e-search-owner-v2 |
| graphrag-20 | version_conflict | hybrid | ok / 齐 | service-search-v2#owner<br>service-billing-v1#owner<br>service-audit-v1#owner<br>service-notify-v1#owner<br>service-identity-v1#owner<br>change-search-v1#proposal | e-search-owner-v2 |
| graphrag-21 | version_conflict | text | ok / 齐 | change-inventory-v1#proposal<br>change-inventory-v1#target<br>service-inventory-v1#owner<br>team-platform-v1#scope<br>service-gateway-v1#owner<br>service-orders-v2#owner | — |
| graphrag-21 | version_conflict | graph | ok / 缺 | service-inventory-v1#owner | e-inventory-owner-v1 |
| graphrag-21 | version_conflict | hybrid | ok / 齐 | service-inventory-v1#owner<br>change-inventory-v1#proposal<br>change-inventory-v1#target<br>team-platform-v1#scope<br>change-orders-v1#proposal<br>service-orders-v2#owner | e-inventory-owner-v1 |
| graphrag-22 | version_conflict | text | ok / 齐 | change-identity-v1#proposal<br>change-identity-v1#target<br>change-orders-v1#proposal<br>change-search-v1#proposal<br>change-inventory-v1#proposal<br>service-identity-v1#owner | — |
| graphrag-22 | version_conflict | graph | ok / 缺 | service-identity-v1#owner | e-identity-owner-v1 |
| graphrag-22 | version_conflict | hybrid | ok / 齐 | service-identity-v1#owner<br>change-identity-v1#proposal<br>change-identity-v1#target<br>change-orders-v1#proposal<br>change-search-v1#proposal<br>change-inventory-v1#proposal | e-identity-owner-v1 |
| graphrag-23 | no_answer | text | ok / 未测回答 | service-audit-v1#owner<br>service-billing-v1#owner<br>service-identity-v1#owner<br>service-inventory-v1#owner<br>service-notify-v1#owner<br>service-search-v2#owner | — |
| graphrag-23 | no_answer | graph | no_match / 未测回答 | — | — |
| graphrag-23 | no_answer | hybrid | ok / 未测回答 | service-audit-v1#owner<br>service-billing-v1#owner<br>service-gateway-v1#owner<br>service-search-v2#owner<br>service-notify-v1#owner<br>service-orders-v2#owner | — |
| graphrag-24 | no_answer | text | ok / 未测回答 | service-orders-v2#owner<br>service-orders-v2#runbook<br>service-orders-v2#dependency<br>change-orders-v1#target<br>change-orders-v1#proposal<br>runbook-orders-v1#maintainer | — |
| graphrag-24 | no_answer | graph | ok / 未测回答 | service-orders-v2#runbook<br>service-orders-v2#dependency<br>service-orders-v2#owner | e-orders-runbook-v2<br>e-orders-dependency-v2<br>e-orders-owner-v2 |
| graphrag-24 | no_answer | hybrid | ok / 未测回答 | service-orders-v2#runbook<br>service-orders-v2#dependency<br>service-orders-v2#owner<br>change-orders-v1#target<br>change-orders-v1#proposal<br>runbook-orders-v1#maintainer | e-orders-runbook-v2<br>e-orders-dependency-v2<br>e-orders-owner-v2 |
| graphrag-25 | no_answer | text | no_match / 未测回答 | — | — |
| graphrag-25 | no_answer | graph | no_match / 未测回答 | — | — |
| graphrag-25 | no_answer | hybrid | no_match / 未测回答 | — | — |
| graphrag-26 | no_answer | text | ok / 未测回答 | service-gateway-v1#dependency<br>service-search-v2#dependency<br>service-gateway-v1#owner<br>service-gateway-v1#runbook<br>service-search-v2#owner<br>service-audit-v1#dependency | — |
| graphrag-26 | no_answer | graph | ok / 未测回答 | service-gateway-v1#dependency<br>service-search-v2#dependency | e-gateway-dependency-v1<br>e-search-dependency-v2 |
| graphrag-26 | no_answer | hybrid | ok / 未测回答 | service-gateway-v1#dependency<br>service-search-v2#dependency<br>service-gateway-v1#runbook<br>service-gateway-v1#owner<br>service-search-v2#owner<br>service-search-v2#runbook | e-gateway-dependency-v1<br>e-search-dependency-v2 |
| graphrag-27 | injection | text | ok / 未测回答 | runbook-gateway-v1#untrusted-example<br>runbook-billing-v1#steps<br>runbook-gateway-v1#steps<br>runbook-identity-v1#steps<br>runbook-inventory-v1#steps<br>runbook-orders-v1#steps | — |
| graphrag-27 | injection | graph | no_match / 未测回答 | — | — |
| graphrag-27 | injection | hybrid | ok / 未测回答 | runbook-gateway-v1#untrusted-example<br>runbook-billing-v1#steps<br>runbook-identity-v1#steps<br>runbook-gateway-v1#steps<br>runbook-inventory-v1#steps<br>runbook-search-v1#steps | — |
| graphrag-28 | injection | text | ok / 未测回答 | runbook-orders-v1#untrusted-example<br>runbook-gateway-v1#untrusted-example<br>runbook-identity-v1#untrusted-example<br>runbook-search-v1#untrusted-example<br>team-application-v1#boundary<br>team-data-v1#boundary | — |
| graphrag-28 | injection | graph | no_match / 未测回答 | — | — |
| graphrag-28 | injection | hybrid | ok / 未测回答 | runbook-orders-v1#untrusted-example<br>change-orders-v1#target<br>runbook-gateway-v1#untrusted-example<br>runbook-search-v1#untrusted-example<br>change-search-v1#proposal<br>runbook-identity-v1#untrusted-example | — |
| graphrag-29 | injection | text | ok / 未测回答 | runbook-identity-v1#untrusted-example<br>runbook-gateway-v1#untrusted-example<br>service-audit-v1#owner<br>service-audit-v1#runbook<br>service-audit-v1#dependency<br>runbook-billing-v1#steps | — |
| graphrag-29 | injection | graph | no_match / 未测回答 | — | — |
| graphrag-29 | injection | hybrid | ok / 未测回答 | runbook-identity-v1#untrusted-example<br>runbook-gateway-v1#untrusted-example<br>service-audit-v1#runbook<br>service-audit-v1#owner<br>runbook-identity-v1#steps<br>service-audit-v1#dependency | — |
| graphrag-30 | injection | text | ok / 未测回答 | runbook-search-v1#untrusted-example<br>runbook-gateway-v1#untrusted-example<br>team-application-v1#boundary<br>team-data-v1#boundary<br>team-platform-v1#boundary<br>team-security-v1#boundary | — |
| graphrag-30 | injection | graph | no_match / 未测回答 | — | — |
| graphrag-30 | injection | hybrid | ok / 未测回答 | runbook-search-v1#untrusted-example<br>team-application-v1#boundary<br>runbook-gateway-v1#untrusted-example<br>team-security-v1#boundary<br>team-data-v1#boundary<br>runbook-identity-v1#untrusted-example | — |

## 复现与下一步

[操作命令与 L5 四个练习](../docs/data/stage-08-graphrag-retrieval.md)说明构建、同集评估、日期边界与来源追溯。源码入口是独立包的 retrieval.py / evaluate_retrieval.py；个人学习状态不代签。

路线 6 才接入有界结构化问答、引用/关系核验和明确拒答；路线 7 衡量真实生成质量。本轮已经观察过这 30 题的检索表现，继续冻结并披露这一点，不能称为未看过的泛化测试集。Stage08 完成后仍按约定提醒 GraphSAGE 专项诊断，当前不调整图模型。
