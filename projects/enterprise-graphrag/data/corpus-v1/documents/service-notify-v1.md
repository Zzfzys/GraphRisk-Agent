---
{
  "authority": "approved",
  "doc_id": "service-notify-v1",
  "effective_from": "2026-01-01",
  "effective_to": null,
  "license": "project-original-public-demo",
  "provenance": "project-original-simulated",
  "public_release": true,
  "subject_id": "svc-notify",
  "supersedes": null,
  "title": "通知服务服务登记",
  "version": 1
}
---

# 通知服务服务登记

> 本文为项目原创模拟 IT 运维资料，不对应真实企业或真实系统。

## owner

通知服务（svc-notify）的正式责任团队是应用组（team-application）。责任团队负责该服务的变更核准与故障协调；不代表有权代用户执行操作。

## dependency

通知服务（svc-notify）直接依赖服务 svc-gateway。这是一条有方向的运行依赖，只说明调用方依赖被调用方，不说明反向依赖。

## runbook

通知服务（svc-notify）使用入口限流检查手册（rb-gateway）作为只读排查入口。本文不提供实际账号、地址、凭据或生产操作授权。
