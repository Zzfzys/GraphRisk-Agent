---
{
  "authority": "approved",
  "doc_id": "service-billing-v1",
  "effective_from": "2026-01-01",
  "effective_to": null,
  "license": "project-original-public-demo",
  "provenance": "project-original-simulated",
  "public_release": true,
  "subject_id": "svc-billing",
  "supersedes": null,
  "title": "账单服务服务登记",
  "version": 1
}
---

# 账单服务服务登记

> 本文为项目原创模拟 IT 运维资料，不对应真实企业或真实系统。

## owner

账单服务（svc-billing）的正式责任团队是数据组（team-data）。责任团队负责该服务的变更核准与故障协调；不代表有权代用户执行操作。

## dependency

账单服务（svc-billing）直接依赖服务 svc-orders。这是一条有方向的运行依赖，只说明调用方依赖被调用方，不说明反向依赖。

## runbook

账单服务（svc-billing）使用账单队列检查手册（rb-billing）作为只读排查入口。本文不提供实际账号、地址、凭据或生产操作授权。
