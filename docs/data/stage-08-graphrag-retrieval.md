# Stage08 路线 5：检索合同与学习操作

2026-09-30：路线 5 工程完成。独立包运行依赖仍只有标准库；索引构建和 hybrid 查询调用本机 Ollama embedding，text/graph 查询不调用模型。此层只返回候选证据，不生成答案；结构化问答、引用约束与拒答在路线 6 实现。原始语料/问题继续冻结。

## 三种模式如何工作

| 模式 | 输入如何找到证据 | 局限 |
|---|---|---|
| text | 小写英文词/数字与中文二元字串分词；BM25；固定最多 6 段 | 关键词相似不能自动补齐跨文档连接 |
| graph | 按实体全名或 ID 链接，使用有限中文关系提示，沿有出处且当前有效的关系最多走两跳 | 不支持任意别名、自由语义解析；仅遍历正式事实，不会自动附上草案/取消背景 |
| hybrid | BM25、固定本地向量余弦排名和图证据，用 RRF 合并；优先保留完整路径的全部片段，再补排名候选 | 强依赖人工审核图与精确实体链接；本次结果不能单独归功于向量模型 |

先过滤文档和边的 `[effective_from, effective_to)`，再计算文本统计/余弦排名和图遍历。旧版不参与当日排名；草案/取消文档可作带 authority 的文本背景，CHANGES/PROPOSES_OWNER 不作为正式责任关系遍历。图路径可以证明已记录的关系，但不能替缺少的 SLA、未记录依赖或不存在的实体作答。

`retrieval.py` 的 CONTRACT 是版本化参数：Top-K=6、最多 2 跳/3 个种子/3 条完整路径、128 次边扩展、每种排名最多 64 个候选、BM25 k1=1.2/b=.75、RRF k=60、dense cosine 下限 .45。该阈值是预先固定的基线，未据冻结集调参。max_paths 可能截断结果，不能把未返回路径解释为关系不存在。图路径整组优先，因此最终列表不是单纯按 RRF 全局排序。

固定 embedding 为 `qwen3-embedding:0.6b`，digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`，1024 维。文档 embedding 输入为标题+原文，问题为原文问题，无额外任务前缀；禁止截断。L2 归一化后四舍五入 8 位小数，同机两次真实构建字节一致。不同硬件/Ollama 实现不保证浮点逐位一致，应重新测量并保留新版本，不修改旧证据。

索引记录完整来源、分词计数、向量、语料/模型/合同版本。索引版本是 payload 的 SHA256，文件 SHA256 另记；path ID 来自有序 edge ID 列表的哈希。每次查询重新核对索引校验和、冻结来源、段清单、模型 digest/维度、向量有限值和单位范数。校验和用于发现损坏，不是攻击者无法伪造的数字签名。

embedding 限本机 `127.0.0.1:11434`，禁代理和重定向；请求前后核对 digest，每批最多 8 段，单文本最多 4000 字符，响应最多 8MB，单次 embedding 与查询预算 60 秒。HTTP socket 超时结合调用前后截止时间检查，不宣称对阻塞线程有强制终止机制。hybrid embedding 失败返回 error，不静默改用 text。text/graph 在已有合法索引时可离线查询。

`status=ok` 只代表有候选，`no_match` 只代表本次检索无候选，`error` 为校验/服务失败；这些都不是 LLM 问答正确率或拒答安全性。返回值没有向量、密钥或黄金答案；有 chunk_id、原文/hash、版本/有效期/authority、source_file、path_id、边及出处。RRF 分数不是风险分数或答案概率。

## 从仓库根目录复现

以下 PowerShell 命令均在 `E:\GraphRisk-Agent` 运行，Ollama 已安装指定模型。保留原证据，用新的输出文件名；存在时命令会拒绝覆盖。不要在冻结 data/corpus-v1 内写生成物。

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.uv-python'
uv sync --offline --locked --project projects/enterprise-graphrag
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.corpus --dataset projects/enterprise-graphrag/data/corpus-v1

uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.retrieval build --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/my-index-a.json
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.retrieval build --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/my-index-b.json
Get-FileHash reports/generated/stage08/route5/my-index-a.json, reports/generated/stage08/route5/my-index-b.json

uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.retrieval query --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/my-index-a.json --mode hybrid --as-of 2026-09-30 --query '星舟网关直接依赖的服务由哪个团队负责？'
```

将 `--mode hybrid` 分别换为 `text`、`graph`，比较返回 chunk_id、paths 和 candidate_counts。graph/hybrid 的本例应看到 `e-gateway-dependency-v1 → e-identity-owner-v1`，终点 team-security；返回路径两条边都必须在 chunks 中有对应原文。

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.evaluate_retrieval --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route5/my-index-a.json --output reports/generated/stage08/route5/my-evaluation.json
uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -v
uv run --offline pytest -q
```

独立项目 51 项测试含原路线 4 的 26 项及本轮 25 项；测试里的假向量只验证合同，报告三模式质量使用真实本地索引/真实查询 embedding。根 pytest 不收集独立项目 unittest，应分别运行。无数据库 DSN 时根回归会跳过 18 项真库测试，本轮没有补测数据库。

评估器才读取 questions.json 的 expected；retriever 只接收问题、日期、模式，embedder 只接收公开文本。读取冻结清单做文件哈希校验不等于把黄金内容放入索引。报告保留 90 条完整观测、代码/合同/数据/索引哈希和耗时。可回答 22 题计算完整证据覆盖率与微平均必要片段召回率；无答案/注入各 4 题只记录候选、攻击段是否召回，不冒充问答评估。30 题现已用于检索诊断，不能再声称它们是从未观察的最终泛化测试集；路线 7 继续冻结、不据此改题，另行新增测试集需单独版本。

## L5：按顺序完成四个小练习（约 90 分钟）

1. **15 分钟，日期。** 先预测，再将“轨道订单由哪个团队负责”分别按 2026-06-30 和 2026-07-01 查询。前者 application，后者 platform；解释为何换版本当天旧关系应被排除，不能先取 Top-K 再过滤。
2. **25 分钟，两跳。** 复现报告 graphrag-09，手画“星舟网关 → 身份服务 → 安全组”，逐边抄下 edge_id、chunk_id、原文和日期。对比 text 的 6 段为何缺身份服务的 owner，graph/hybrid 如何补齐；不要把网关 owner 当依赖服务 owner。
3. **25 分钟，文本的价值。** 比较 graphrag-21/22 三种模式：graph 找到正式 owner，但没有交接草案/取消片段；解释为何完整答复仍需文本背景。读 bm25、normalize、graph_paths、retrieve 中的排名合并，手算第 1/2 名 RRF 贡献 1/61 与 1/62。余弦相似度、RRF 分数、可追溯关系与事实正确性分别意味着什么？
4. **25 分钟，失败与评估。** 查询 2025-12-31；查询未记录的 SLA。前者 no_match，后者可能 ok 且有相关片段，但都不足以作答。阅读 test_retrieval.py/test_retrieval_evaluation.py 的索引篡改、错误 digest/维度和只命中一跳测试；仅在临时副本破坏索引，不改冻结资料。

个人笔记仍由本人填写：预测、命令/输入、观察结果、出处、解释。工程完成不替你勾选个人学习。

[真实结果及 90 条候选表](../../reports/stage-08-route-5-retrieval.md) · [实现路线](../plans/stage-08-implementation-roadmap.md) · [学习路线](../plans/stage-08-learning-roadmap.md)
