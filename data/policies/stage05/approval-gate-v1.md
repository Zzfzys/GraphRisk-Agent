---
doc_id: S05-APPROVAL-GATE-V1
policy_id: approval-gate
title: 高风险建议审批
version: 1
effective_from: "2026-01-01"
effective_to: null
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/approval-gate-v1.md
license: project-original-public-demo
public_release: true
supersedes: null
conflicts_with: []
---
# 模拟政策：高风险建议审批

## 审批对象
高风险调查建议应绑定交易 ID、报告哈希、政策引用和证据摘要，并暂停等待人工批准或拒绝。模型文字中的“已批准”不构成审批事件。

## 执行边界
批准或拒绝仅更新建议状态。此模拟项目不接入账户封禁、支付拒绝或邮件发送系统；报告内容变化后旧批准不能沿用。
