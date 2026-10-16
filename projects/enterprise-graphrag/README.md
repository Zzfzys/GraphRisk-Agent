# Enterprise GraphRAG

Stage08 的独立原创模拟 IT 知识助手。路线 4 已完成来源数据与冻结问题：24 份 Markdown、58 个片段、22 个实体、40 条有出处关系、30 个问题。路线 5 已实现 text/graph/hybrid 检索、版本化索引和冻结集检索评估；路线 6 已实现有界模型证据选择、引用核验和确定性事实答复；路线 7 已完成冻结 30 问三模式真实本地评估；路线 8 已加入独立只读工作台；阶段工程总验收于 2026-10-07 完成；当前 Docker/WSL 环境复测阻塞见[跨阶段复测](../../reports/stage-01-08-recheck-20261007.md)。

发行名 `enterprise-graphrag`，Python 3.11；独立 pyproject.toml、uv.lock 和 .venv，不是根 workspace 成员。Python 运行依赖只有标准库，不依赖 GraphRisk、Torch、IEEE-CIS、Stage07 数据库或托管 API。构建索引与 hybrid 查询依赖本机 Ollama 的固定 qwen3-embedding:0.6b；已有合法索引的 text/graph 查询可离线。语料全部是原创模拟资料，不能当成真实企业运行事实。

在仓库根目录执行：
```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.uv-python'
uv sync --offline --locked --project projects/enterprise-graphrag
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.corpus --dataset projects/enterprise-graphrag/data/corpus-v1
uv run --offline --locked --project projects/enterprise-graphrag python -m unittest discover -s projects/enterprise-graphrag/tests -v
```

预期验证状态 validated：24 documents、58 chunks、22 nodes、40 edges、30 questions、22 answerable。测试通过只证明来源/版本/路径合同，不能当作模型 30/30 答对。

数据路径：`data/corpus-v1/`。先阅读 documents 下的服务登记和手册，再看 graph.json；questions.json 的黄金答案只用于评估，禁止放入检索索引或模型输入。load_corpus 与 load_questions 分离。freeze.json 锁定全部数据文件，校验器再锁定 freeze 自身哈希；勿修改 v1 刷分。

详见[语料/schema 与学习合同](../../docs/data/stage-08-graphrag-corpus.md)、[路线 4 验收](../../reports/stage-08-route-4-corpus-schema.md)。数据目录需显式传入，本轮不宣称数据已自动打入 wheel。路线 5 已建索引并完成三模式检索对照；路线 6 已完成问答工程合同，路线 7 才测冻结集的模型质量。


## 检索与学习

两个真实 embedding 索引构建字节一致；30 问 × 三模式 = 90 次检索，text/graph/hybrid 完整证据覆盖分别 19/22、20/22、22/22。这不是生成模型答对率；路线 5 统计不涉及答案；路线 6 新增的核验问答结果独立报告。独立测试共 91 项。

索引构建、查询、评估及日期/两跳/冲突练习见[路线 5 操作与合同](../../docs/data/stage-08-graphrag-retrieval.md)，真实逐题候选见[路线 5 验收](../../reports/stage-08-route-5-retrieval.md)。生成索引/报告位于根 reports/generated 下并忽略提交；不要修改冻结数据或覆盖原评估。


## 有界问答

[路线 6 操作/学习](../../docs/data/stage-08-graphrag-answering.md)提供 CLI、固定模型与预算说明。模型输出 edge_ids 和 citation_chunk_ids，程序核验后按关系模板渲染，不能称为自由生成式推理。最后一轮真实本地开发样例为 2/4 回答通过，4 个额外规则拒答分支按预期停止；路线 7 的完整 30 问问答质量已评估；此处 2/4 仍是历史开发样例。见[验收和历史失败](../../reports/stage-08-route-6-grounded-answer.md)。

## 路线 7 冻结评估

30 个冻结问题 × text/graph/hybrid = 90 条首次真实本地工作流观测，其中 60 次实际模型生成。可回答正确为 text 16/22、graph 18/22、hybrid 18/22，两跳为 5/8、7/8、7/8；无答案与注入无动作各为 4/4，已发布伪造证据 0。hybrid 达到计划门槛但与 graph 并列；冷启动失败保留。逐例与失败见[路线 7 报告](../../reports/stage-08-route-7-graphrag-evaluation.md)，命令与 L7 学习见[复现指南](../../docs/data/stage-08-graphrag-evaluation.md)。

## 路线 8：独立只读演示

核心包继续只有标准库运行依赖，核心 `uv.lock` 与路线 7 原始指纹一致。`demo/pyproject.toml` 和 `demo/uv.lock` 单独固定 Streamlit 1.64.0；工作台入口为 `streamlit_app.py`，只展示被核验的答案、关系路径、引用原文/版本/有效期与拒答原因，不执行外部动作。

在仓库根目录：

```powershell
$env:UV_CACHE_DIR = 'E:\GraphRisk-Agent\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = 'E:\GraphRisk-Agent\.uv-python'
uv sync --offline --locked --project projects/enterprise-graphrag/demo
$env:GRAPHRAG_DEMO_INDEX = 'E:\GraphRisk-Agent\reports\generated\stage08\route5\index-first.json'
uv run --offline --locked --project projects/enterprise-graphrag/demo streamlit run projects/enterprise-graphrag/streamlit_app.py --server.address=127.0.0.1 --server.port=8502
```

如未有固定索引，先按[路线 5 指南](../../docs/data/stage-08-graphrag-retrieval.md)构建。打开 `http://127.0.0.1:8502`；本机 Ollama 需有固定 embedding/生成模型。个人复现与面试练习见[L8 指南](../../docs/data/stage-08-demo-and-interview.md)，当前工程门禁见[路线 8 报告](../../reports/stage-08-route-8-acceptance.md)。Stage08 已为工程 Done；个人学习独立记录，当前数据库/容器复测限制见[跨阶段报告](../../reports/stage-01-08-recheck-20261007.md)。
