---
doc_id: S05-GRAPH-SIGNAL-V1
policy_id: graph-signal
title: 图关系线索使用
version: 1
effective_from: "2026-01-01"
effective_to: null
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/graph-signal-v1.md
license: project-original-public-demo
public_release: true
supersedes: null
conflicts_with: []
---
# 模拟政策：图关系线索使用

## 可用线索
合格的历史风险邻居可以提示共享设备、卡片、邮箱域或地址路径。报告应指出邻居交易 ID、共享实体类型和目标之前的时间间隔，不能引用目标之后或同时间的边。

## 解释限制
GraphSAGE 分数与共享匿名实体都是调查线索，不证明两个交易属于同一真实客户，也不证明邻居或目标已发生欺诈。若目标没有 held-out 图分数，应明确写为图能力缺口。
