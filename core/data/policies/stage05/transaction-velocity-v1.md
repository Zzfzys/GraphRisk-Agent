---
doc_id: S05-TRANSACTION-VELOCITY-V1
policy_id: transaction-velocity
title: 交易速度核查
version: 1
effective_from: "2026-01-01"
effective_to: null
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/transaction-velocity-v1.md
license: project-original-public-demo
public_release: true
supersedes: null
conflicts_with: []
---
# 模拟政策：交易速度核查

## 触发条件
同一卡片在目标交易前 10 分钟内出现至少 4 次交易尝试时，将速度异常列为人工调查线索。时间窗口只使用严格早于目标交易的事件，同时间事件视为同时发生。

## 解释限制
速度异常可能来自正常重试或系统故障，不能单独作为拒绝交易的依据。报告应给出窗口、计数和数据缺失情况。
