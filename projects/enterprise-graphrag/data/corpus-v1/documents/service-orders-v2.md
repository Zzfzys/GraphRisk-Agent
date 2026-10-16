---
{
  "authority": "approved",
  "doc_id": "service-orders-v2",
  "effective_from": "2026-07-01",
  "effective_to": null,
  "license": "project-original-public-demo",
  "provenance": "project-original-simulated",
  "public_release": true,
  "subject_id": "svc-orders",
  "supersedes": "service-orders-v1",
  "title": "轨道订单服务登记",
  "version": 2
}
---

# 轨道订单服务登记

> 本文为项目原创模拟 IT 运维资料，不对应真实企业或真实系统。

## owner

轨道订单（svc-orders）的正式责任团队是平台组（team-platform）。责任团队负责该服务的变更核准与故障协调；不代表有权代用户执行操作。

## dependency

轨道订单（svc-orders）直接依赖服务 svc-inventory。这是一条有方向的运行依赖，只说明调用方依赖被调用方，不说明反向依赖。

## runbook

轨道订单（svc-orders）使用订单积压检查手册（rb-orders）作为只读排查入口。本文不提供实际账号、地址、凭据或生产操作授权。
