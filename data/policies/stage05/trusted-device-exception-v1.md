---
doc_id: S05-TRUSTED-DEVICE-EXCEPTION-V1
policy_id: trusted-device-exception
title: 可信设备例外
version: 1
effective_from: "2026-07-01"
effective_to: null
owner: GraphRisk-Agent 示例项目
source_type: simulated
source_ref: data/policies/stage05/trusted-device-exception-v1.md
license: project-original-public-demo
public_release: true
supersedes: null
conflicts_with:
  - device-sharing
---
# 模拟政策：可信设备例外

## 例外条件
若设备经独立人工流程标记为可信，即使它在 24 小时内关联至少 5 个账户，本模拟例外也允许不进入共享设备人工复核队列。设备是否可信必须有独立、可追溯的批准记录，模型不得自行填写该标记。

## 已知冲突
本条与同日生效的“共享设备核查”第 2 版的“必须进入人工复核队列”直接冲突。此处故意不规定自动优先级；同时命中时应列出两个准确版本并交人工裁定，不能选择对生成模型更方便的一条。
