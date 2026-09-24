# Stage 05 路线 1：模拟政策语料与出处

本语料是为 GraphRisk-Agent **原创编写的模拟企业规则**，不代表任何真实金融机构、IEEE-CIS 数据提供方或监管要求。创建日期为 2026-09-24；政策的 2026 年生效日期是为了测试版本筛选而设定的**模拟日期**，不能从 IEEE-CIS 的人工交易时间推断。调查请求必须单独提供 `policy_as_of`。

## 来源与公开说明

每份文档位于 `data/policies/stage05/`，YAML 元数据有稳定 `doc_id`、同一主题的 `policy_id`、整数 `version`、半开生效区间 `[effective_from, effective_to)`、所有者、`source_type: simulated`、指向自身的 `source_ref`、`license: project-original-public-demo` 和 `public_release: true`。`project-original-public-demo` 是**本项目自建文本可用于公开作品集的出处/发布标记**，不是对第三方材料的许可声明，也不声称授予仓库以外的再分发权。本批语料没有复制外部政策，因此没有需要记录的外部 URL 或第三方许可。若今后纳入公开材料，必须另外记录 URL、具体许可、获取日期和版本，并扩展导入契约。

| 文档 ID | 版本 | 生效区间 | 主题 |
|---|---:|---|---|
| `S05-DEVICE-SHARING-V1` | 1 | 2026-01-01 至 2026-07-01（不含） | 24 小时共享设备至少 8 账户时核查 |
| `S05-DEVICE-SHARING-V2` | 2 | 2026-07-01 起 | 阈值改为至少 5 账户，进入人工复核 |
| `S05-TRANSACTION-VELOCITY-V1` | 1 | 2026-01-01 起 | 10 分钟内同卡交易尝试速度 |
| `S05-MANUAL-REVIEW-V1` | 1 | 2026-01-01 起 | 复核建议最低证据与记录要求 |
| `S05-INSUFFICIENT-EVIDENCE-V1` | 1 | 2026-01-01 起 | 缺证据时明确表达无法判断 |
| `S05-TRUSTED-DEVICE-EXCEPTION-V1` | 1 | 2026-07-01 起 | 可信设备例外，与新版共享设备规则冲突 |
| `S05-GRAPH-SIGNAL-V1` | 1 | 2026-01-01 起 | 图关系只能作为调查线索 |
| `S05-APPROVAL-GATE-V1` | 1 | 2026-01-01 起 | 高风险建议等待人工审批 |

原文均以 `# 模拟政策：...` 开头，每个规则按 `##` 章节组织。导入 manifest 为每个段落记录章节名、章节内段落序号、源文件行号范围和 SHA-256；文档正文也有 SHA-256。下阶段的 `chunk_id` 和检索索引尚未生成，不能将当前段落位置误称为最终检索引用。

## 时间和冲突手算

半开区间意味着 `effective_to` 当日**不再**适用旧版。若一台设备在目标之前的 24 小时关联 5 个账户：

| `policy_as_of` | 适用的共享设备版本 | 手算结论 |
|---|---|---|
| 2026-06-30 | V1，阈值 8 | 未达到这条规则的核查阈值 |
| 2026-07-01 | V2，阈值 5 | 达到新版人工复核阈值 |
| 2026-08-01 | V2，阈值 5 | 达到新版人工复核阈值 |

自 2026-07-01 起，`trusted-device-exception` 的“可信设备可不进入共享设备复核队列”与 `device-sharing` V2 的“必须进入人工复核队列”直接冲突。语料通过 `conflicts_with: [device-sharing]` 显式标注，但**没有设定自动优先级**。后续检索/Agent 必须呈现两条准确版本并等待人工裁定；不能让相似度或模型文本自行解决。可信设备标记必须有独立批准记录，不能由 LLM 生成。

## 导入与校验

```powershell
uv run --offline python scripts/import_policies.py --config configs/stage05.yaml
uv run --offline pytest tests/unit/test_stage05_policy_corpus.py -q
```

导入器只读公开模拟源文件，向 Git 忽略的 `artifacts/stage-05/policy/corpus-manifest.json` 写入确定性 manifest，不读取 IEEE-CIS 原始 CSV 或标签。它拒绝非法日期、同一 `policy_id` 的重叠版本、未声明前版、重复 `doc_id`、空来源/许可、错误源文件、不可公开标记及不存在的冲突目标。指令式恶意文本只在测试夹具里作为**不可信内容**解析，正式 8 份语料不包含该测试片段；导入器不会执行文档文字。

路线 1 只冻结文档和出处。切分策略、词法/语义检索、时效过滤的查询接口与检索质量评估分别属于路线 2/3。
