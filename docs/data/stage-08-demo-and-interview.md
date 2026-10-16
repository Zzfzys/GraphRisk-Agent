# Stage 08 路线 8：个人复现与面试表达

> 2026-10-02。工程演示入口已准备好；L8 必须由学习者亲自运行、解释并记录，不由自动测试代签。全部资料为项目原创模拟 IT 文档。

## 60 分钟个人练习

| 时间 | 你亲手做的事 | 应留下的证据 |
|---|---|---|
| 0～10 分钟 | 从两个锁文件分别同步核心包和只读演示环境，确认 Python 3.11、模型 digest 和索引。 | 版本、锁文件 SHA256、`/api/tags` 中的两个固定模型。 |
| 10～25 分钟 | 重新构建索引两次，比较文件 SHA256；跑下面的两跳与无答案 CLI。 | 两个索引同哈希；两跳两条边/两篇来源；拒答且生成调用 0。 |
| 25～40 分钟 | 打开工作台，分别提交两跳、无答案、注入问题；看答案、关系路径、原文/版本/有效期和执行记录。 | 记录状态与 reason；说明哪些案例在模型前被挡住。 |
| 40～50 分钟 | 读冻结评估的 text/graph/hybrid 结果和 DeepSeek 同集对照，解释两种评估不能混成一个排行榜。 | 写出 16/22、18/22、18/22；20/20 的提供商案例属于另一套冻结集。 |
| 50～60 分钟 | 不看稿录制 90 秒讲解；按自测表复盘。 | 录音/笔记路径由你自行填写；不可用工程测试代签。 |

在仓库根目录的 PowerShell 中执行。运行环境不得加载 GraphRisk 的私有数据或付费 API 密钥。核心包没有第三方运行依赖；演示 UI 使用 `demo/uv.lock` 单独锁定 Streamlit 1.64.0，不改变核心 `uv.lock` 和路线 7 评估指纹。

```powershell
$env:UV_CACHE_DIR = 'E:\GraphRisk-Agent\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = 'E:\GraphRisk-Agent\.uv-python'
uv sync --offline --locked --project projects/enterprise-graphrag
uv sync --offline --locked --project projects/enterprise-graphrag/demo
Invoke-RestMethod http://127.0.0.1:11434/api/tags
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.retrieval build --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route8/my-index-a.json
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.retrieval build --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route8/my-index-b.json
Get-FileHash reports/generated/stage08/route8/my-index-*.json -Algorithm SHA256
```

输出路径必须是新的；构建命令拒绝覆盖已有索引。预期两个索引均为 58 chunks、`index_version=84d09b81aec41cbebdbeed7b6e634a9147b786b5c6c2efb41595e37839bd8c2e`，文件 SHA256 均为 `02a134f896825b17087e93f376044ea617bd211aba31876311be54bd3c54f75e`。模型下载和 Ollama 启动见[路线 6 指南](stage-08-graphrag-answering.md)。若本机模型 digest 不同，应停下记录差异，不改冻结题目来凑结果。

```powershell
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.answering --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route8/my-index-a.json --mode hybrid --as-of 2026-09-30 --question '2026-09-30，星舟网关直接依赖的服务由哪个团队负责？请列出两步关系依据。' --output reports/generated/stage08/route8/my-two-hop.json
uv run --offline --locked --project projects/enterprise-graphrag python -m enterprise_graphrag.answering --dataset projects/enterprise-graphrag/data/corpus-v1 --index reports/generated/stage08/route8/my-index-a.json --mode hybrid --as-of 2026-09-30 --question '2026-09-30 月港推荐服务的责任团队是谁？' --output reports/generated/stage08/route8/my-no-answer.json
$env:GRAPHRAG_DEMO_INDEX = 'E:\GraphRisk-Agent\reports\generated\stage08\route8\my-index-a.json'
uv run --offline --locked --project projects/enterprise-graphrag/demo streamlit run projects/enterprise-graphrag/streamlit_app.py --server.address=127.0.0.1 --server.port=8502
```

无答案 CLI 预期退出码 2，须再检查 JSON 的 `reason=unknown_entity`、`answer=null`、`generation_calls=0`，不能把非零退出码都算崩溃。本次工程实测两跳已返回 `team-security`，路径 `e-gateway-dependency-v1 → e-identity-owner-v1`，引用来自网关依赖段和身份服务责任段；复现时小模型仍可能变化，真实结果必须如实记录。浏览器打开 `http://127.0.0.1:8502`。页面只读，拒答不显示半成品答案；手册注入题主要由确定性输入门禁拦截，不能据此宣称模型具备普遍抗注入能力。

## 90 秒面试讲解稿

**0～20 秒，场景。** “我为原创模拟 IT 运维资料做了一个可独立安装的知识助手。用户问服务的责任团队或两跳依赖关系时，系统给出可核对的原文和路径；证据不足就拒答。”

**20～45 秒，方法。** “24 篇文档含版本和生效区间，40 条人工审核关系都绑到原文片段。文本、图、混合三种检索共用严格核验；本地 0.5B 模型只选择关系和引用 ID，最终事实句由程序按核验结果渲染。”

**45～70 秒，实测。** “冻结 30 问的首次运行里，22 个可回答题分别答对 16、18、18 个，图与混合在两跳题都是 7/8；混合没有赢过纯图。图补齐了网关依赖服务的责任团队来源，但库存手册问题反而是文本模式答对。”

**70～90 秒，边界。** “这是小型模拟语料，冻结题此前已用于检索诊断，不能外推生产。注入题主要在程序门禁停下；首次冷启动也有失败。GraphRisk 的 50 个假模型机制案例及同交易 3 个本地样本，不是这套真实模型质量分数。”

## 自测：由你填写

- [ ] 我能不看稿完成两跳问答，并指出两条 edge ID、两处片段原文、有效日期。
- [ ] 我能复现无答案和注入拒答，分清模型主动拒答、程序门禁拒答与模型输出被核验器拒绝。
- [ ] 我能解释 text 16/22、graph/hybrid 18/22 的比较只消融检索方式；text 问答依然共享来源关系核验。
- [ ] 我能解释 DeepSeek 20/20 属于另一套 provider 案例，估算费用 0.028383 元尚未由正式账单对账。
- [ ] 我能说明原创模拟、6GB 资源、冷启动、缺少真库回归和不能自动工单/权限动作等限制。
- [ ] 我已录制 90 秒说明，并记下两个需要补学的错误码及自己的解释。

详细逐案失败和来源版本见[路线 7 评估](../../reports/stage-08-route-7-graphrag-evaluation.md)。路线 8 总验收进度见[验收报告](../../reports/stage-08-route-8-acceptance.md)。个人自测只由你自己勾选。