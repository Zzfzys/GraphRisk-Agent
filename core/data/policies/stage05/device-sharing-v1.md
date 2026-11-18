---
doc_id: S05-DEVICE-SHARING-V1
policy_id: device-sharing
title: 共享设备核查
version: 1
effective_from: "2026-01-01"
effective_to: "2026-07-01"
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/device-sharing-v1.md
license: project-original-public-demo
public_release: true
supersedes: null
conflicts_with: []
---
# 模拟政策：共享设备核查

## 触发条件
在本模拟规则中，同一设备在目标交易发生前的 24 小时内关联至少 8 个不同账户时，标记为需要人工核查的设备共享线索。计数不包含目标交易或之后的交易。

## 处理边界
共享设备只能作为调查线索，不能据此认定账户属于同一人或认定交易欺诈。调查员应核对设备标识质量和其它独立证据。
