# Stage08 路线 6：有界问答、引用核验与拒答

2026-09-30：工程合同已验收。独立项目新增 `answering.py` 和 31 项问答/故障测试；合计 82 项独立测试通过。最后一轮 4 个可回答开发样例中，真实 qwen2.5:0.5b 回答通过 2 个，另 2 个被核验器拦截。另有 4 个拒答分支按预期停止。不能将合同通过写成模型质量全部合格；30 问正式评估属于路线 7。

## 工作流与你要理解的边界

固定步骤：`parse → retrieve → screen → generate → verify → finish`。任何失败返回 `status=abstained`、`answer=null` 和稳定 reason；没有重试、第二次检索或自动工具动作。

1. **parse**：按本项目关系 schema 提取主体、关系链、日期和是否需要交接背景。支持正式责任团队、直接依赖、依赖服务的责任团队、手册维护团队及草案/取消背景。实体采用原名或 ID；不支持任意自然语言、别名、条件推理或跨多个主体的开放查询。明确的执行、密钥导出、系统恢复/SLA 等请求不属于范围。关键词检查是保守路由，不能当成通用提示注入防火墙。
2. **retrieve**：复用路线 5 的 text/graph/hybrid 三模式，只调用一次。先过滤日期后排名，语料、模型、索引版本仍按路线 5 验证。
3. **screen**：校验实际返回的 chunk 原文/hash/版本/日期和路径。只有当前有效且被检索到的片段可以作为证据。text 模式也可从这些片段的已审核关系中重建路径，但不能补入没检索到的来源；它不是完全不使用关系验证的纯文本问答，路线 7 做消融时必须披露这个共同验证层。候选中找不到唯一的完整关系链，直接拒答。
4. **generate**：固定本地模型从提供的关系和引用 ID 中选择，输出结构化 JSON。它不自由撰写最终事实段落，不拥有工具调用权限；提示词无黄金答案。问题、文档都是数据。未参与正式关系/背景的手册注入附录不进入模型上下文。
5. **verify**：JSON 只能包含指定字段。拒绝未知/重复引用、漏引、错关系、错方向、错次序、额外自由文本以及不完整拒答。必须覆盖整条关系路径和必要冲突背景；不能以“引用真实”代替“引用支持当前关系”。生成后重新核对冻结语料，防止生成期间来源变化。
6. **finish**：已核验的原子关系按固定模板渲染中文，并返回 claims、citations 和 path。片段原文、来源文件、版本和生效区间可追溯。`rendering=verified_relation_templates_not_free_model_prose` 明确说明渲染方式。拒答时不返回半份答案。

本实现是**受限关系问答、模型证据选择与确定性核验**，不是任意企业长文自由问答，也不证明小模型具备独立多跳推理能力。正式图源于路线 4 的人工审核资料，图模型 GraphSAGE 未参与这个独立知识助手。

## 模型输出合同

```json
{
  "decision": "answer",
  "edge_ids": ["e-gateway-owner-v1"],
  "citation_chunk_ids": ["service-gateway-v1#owner"],
  "reason": "supported"
}
```

拒答必须 `decision=abstain`、两个列表为空、`reason=insufficient_evidence`。模型引用列表涵盖路径与背景的全部必要片段；开发早期的 `context_chunk_ids` 字段已经被统一引用字段替代，旧实测记录保留在报告中。JSON Schema 限制形状，代码进一步核验语义和出处；格式正确仍可能被拒绝。

交接冲突必须同时检索到变更关联服务的 target 段与 proposal 段，且来源状态为 draft/cancelled，才能解释提案没有覆盖正式 owner；关系和背景不能只凭名字相似连接。graph 模式只返回正式事实时会拒答该冲突问题。当前模型曾选错关系，被拒绝是安全门禁通过，也是模型回答质量失败，两个结论同时保留。

## 固定预算与审计

| 约束 | 当前合同 |
|---|---|
| 步数 / 检索 / 生成 | 最多 6 / 1 / 1；重试 0 |
| 时间 | 整案 125 秒；模型请求链最多 60 秒；embedding 继承剩余整案预算 |
| 输入 | 提示词最多 3000 UTF-8 字节，发送前另留 128 字节对应的保守余量；实际输入 Token 返回后检查 ≤3328 |
| 输出 / 上下文 | 最多 512 Token / 4096；temperature=0、seed=0 |
| 图 | 最多两跳、128 次证据边扩展；引用最多 6 段 |
| 费用 / 动作 | 本地固定模型，付费预算 0、付费调用 0、外部动作 0 |

本地生成固定 `qwen2.5:0.5b`，digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`；请求前后核对 digest。使用 Ollama 自带对话模板（raw=false），保留结构化 format，不换模型、不提高上限。输入字节预检不是提供商精确 tokenizer；响应 usage 仍必须校验，缺失或超出上限就拒绝发布。HTTP timeout 加步骤前后截止检查不是对后台推理的强制进程终止，客户端超时后服务端可能仍在计算；本轮发生过冷加载超时，未隐藏。

audit 记录请求 ID、步骤/耗时、调用次数、提供商类别、模型/digest、输入输出 Token、提示词与输出哈希、费用与外部动作计数。正常结果不返回提示词、原始未核验输出、向量、密钥。断连等未得到 usage 的分支记录 null，不能写成用了 0 个 Token；本地 API 费用为 0 不等于没有显存、时间或电力成本。固定 seed/temperature 也不保证跨次输出完全一致，本次实测已经观察到差异。

## 一步一步复现

在 `E:\GraphRisk-Agent` 操作。若 Ollama 尚未运行，本机模型位于 `E:\ollama`，可在单独 PowerShell 窗口启动：

```powershell
$env:OLLAMA_MODELS = 'E:\ollama'
$env:OLLAMA_NOPRUNE = 'true'
$env:OLLAMA_NO_CLOUD = 'true'
ollama serve
```

在另一个窗口确认模型列表包含上述固定模型和路线 5 embedding：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

使用已有路线 5 索引；新机器先按[路线 5 指南](stage-08-graphrag-retrieval.md)构建。输出文件必须换新名字，程序拒绝覆盖现有证据或写入冻结语料。

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.answering --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/index-first.json --mode hybrid --as-of 2026-09-30 --question '星舟网关由哪个团队负责？' --output reports/generated/stage08/route6/my-direct.json

uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.answering --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/index-first.json --mode hybrid --as-of 2026-09-30 --question '轨道订单直接依赖的服务由哪个团队负责？' --output reports/generated/stage08/route6/my-two-hop.json

uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.answering --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/index-first.json --mode hybrid --as-of 2026-09-30 --question '月港推荐服务的责任团队是谁？' --output reports/generated/stage08/route6/my-unknown.json
```

有效答复应为 answered，且每个 claim 均能找到对应 citation；两跳示例的终点是库存服务的正式团队 team-data。未知实体示例应为 abstained/unknown_entity，generation_calls=0，命令退出码为 2。退出码 2 同样用于模型/工具故障，具体查看 reason，不能仅凭非零退出码判断程序崩溃。小模型重复运行可能拒答，不能手动修改 JSON 把它算成成功。

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -v
uv run --offline pytest -q
```

前者单独收集 GraphRAG 的 82 项测试；后者收集 GraphRisk 主项目，本轮 451 passed、18 skipped、1 warning，跳过的是未配置 DSN 的真库测试。假模型只用于精确触发错误和验证合同，与真实本地样例分列。

## L6：四个学习任务（约 90 分钟）

1. **20 分钟：流程和证据。** 跑一次两跳问题，按步骤读 parse_query、screen_evidence、eligible_evidence、validate_draft。手画关系链，将每条 edge_id 对到 chunk_id、原文和日期；说明为何返回 6 段仍可能证据不足。
2. **25 分钟：引用核验。** 阅读 `test_full_path_with_partial_citations_is_rejected`、伪造边/引用及额外自由文本测试。预测“只有一跳的引用”“真实引用但关系错绑”“模型补写一句全部已恢复”各会在哪一步拒绝。不要修改冻结语料来让测试通过。
3. **20 分钟：拒答与安全。** 运行未知实体、过期日期、导出密钥请求，观察模型调用是否为 0。比较模型主动拒答、模型输出被拒绝、检索失败三类 reason。源码不存在写工单、发送消息或改权限的动作入口；不要把规则拦截成功称为模型学会抵抗所有注入。
4. **25 分钟：读真实失败。** 对照[路线 6 报告](../../reports/stage-08-route-6-grounded-answer.md)中最后一轮 2/4 与前一轮 3/4，解释为什么不能挑好的一轮发布。记录 schema、证据、问答质量三层指标，并写下路线 7 应验证的剩余问题。

工程验收不替本人勾选学习项；Stage08 路线 7～8、正式费用对账与阶段总验收仍待完成。
