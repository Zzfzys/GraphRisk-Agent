---
doc_id: S05-MANUAL-REVIEW-V1
policy_id: manual-review
title: 人工复核记录
version: 1
effective_from: "2026-01-01"
effective_to: null
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/manual-review-v1.md
license: project-original-public-demo
public_release: true
supersedes: null
conflicts_with: []
---
# 模拟政策：人工复核记录

## 最低记录要求
人工复核建议必须列出触发的政策版本、交易事实 ID、模型或图线索 ID，以及仍待核实的事实。模型分数不能改写为已证实欺诈或校准概率。

## 复核结论
复核员可以批准、拒绝或要求补证据；在批准前，系统仅保留待审建议，不执行封禁、拒付或通知等外部动作。
