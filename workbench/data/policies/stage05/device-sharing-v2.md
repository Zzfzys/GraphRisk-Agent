---
doc_id: S05-DEVICE-SHARING-V2
policy_id: device-sharing
title: 共享设备核查
version: 2
effective_from: "2026-07-01"
effective_to: null
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/device-sharing-v2.md
license: project-original-public-demo
public_release: true
supersedes: S05-DEVICE-SHARING-V1
conflicts_with: []
---
# 模拟政策：共享设备核查

## 触发条件
自本模拟版本生效起，同一设备在目标交易发生前的 24 小时内关联至少 5 个不同账户时，必须进入人工复核队列。计数不包含目标交易或之后的交易。

## 处理边界
本规则取代上一版本的 8 账户阈值。共享设备仍不能证明真实身份或欺诈；若同时命中其它相冲突的模拟政策，报告须列出冲突并等待人工判断，不得自动跳过复核。
