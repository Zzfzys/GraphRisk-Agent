# 原 Stage 09 路线 7：可观测性、漂移与安全恢复

2026-10-17：**本地工程验收通过，L7 本人学习 Pending**。Stage 09 整体仍 In Progress；原路线 8 与后续 GNN 关系模式检索尚未启动。源码基于 b550d05 及其已成功 CI 进入本路线；本轮修改需要自己的回归证据，不把旧 CI 冒充本轮。

通俗地说，我们给 Agent 补了“查故障的记录”“看数据有没有变化的仪表”和“数据库坏了能不能救回来的演练”。没有更换风险模型，没有新增真实训练、LLM 或付费调用。

## 改了什么，怎样验收

| 工作 | 实际结果 | 证据（私有根目录下） |
|---|---|---|
| 关联与版本日志 | 新白名单事件流关联 request/run/attempt、版本哈希、Token、重试、耗时、审批；API/旧报告协议保持，幂等重放不再算新执行 | 新单测、recovery-v3/trace.jsonl |
| 历史运行统计 | 路线 1/5 各 30 主案、各 3 次重复分开；同集 30 笔，不冒充 60 笔独立交易；记录历史质量失败 | metrics-v1/result.json、input-bindings.json |
| 数值与分数漂移 | 先冻结参考分箱，再比较严格时序的 44,289 / 44,292 条；同一冻结模型，无标签/新预测 | drift-v1/reference.json、result.json |
| 故障与安全 | 模型连接/超时/总截止、无当期/旧政策/注入、错误 GNN 版本、实际 DB 断线；15/15 检查通过 | recovery-v3/completed-probes.json |
| 真正备份恢复 | pg_dump custom → 另一全新空库 pg_restore，12 表 88 行、schema/索引/约束/扩展/内容一致 | source-logical.json、restored-logical.json、pg-dump.txt、pg-restore.txt |
| 恢复后继续 | 待审/已批准/已拒绝原报告哈希与创建请求 ID 保留；幂等创建不生成，待审可批准、错哈希/冲突拒绝 | recovery-v3/result.json、私有 API 响应 |
| 边界与清理 | 6 个秘密/路径/Prompt canary 在新 API/白名单日志中 0 命中，受限角色恢复验证；随机容器及其匿名卷、密码文件已移除 | boundary-scan.json、sandbox-boundary.json、sandbox-cleanup.json |
| 总门禁 | 输入/合同/资源/15 探针/逻辑清单/备份文件字节/边界/清理再次核对通过 | check-v1/result.json |

私有证据根：`E:/GraphRisk-Agent/reports/generated/stage09/route7/20261017-ops-v1/`。操作、字段表、可复制命令与三次 30 分钟练习见[路线 7 说明](../docs/data/stage-09-operations.md)。运行合同为 [stage09_operations.json](../configs/stage09_operations.json)，测量前冻结 SHA256 `cdab64e457237d0cc0c18f1eff9bd99e9291b01f0e6ae1b2d88ab33602349290`，未依据结果修改门槛。

## 历史统计说明

| 同集主案 30 笔 | 路线 1 | 路线 5 |
|---|---:|---:|
| 当时评分通过 | 29/30 | 28/30 |
| verified / partial_evidence / 生成依据失败 | 19 / 10 / 1 | 18 / 10 / 2 |
| 图证据 available / unavailable | 20 / 10 | 20 / 10 |
| 主案端到端 P50 / P95（毫秒） | 1908.53 / 2031.92 | 1907.71 / 1993.82 |
| schema 无效返回 | 0 | 0 |
| 发送生成尝试 / 重试 | 30 / 0 | 30 / 0 |
| 实际观察 prompt / output Token | 8930 / 3298 | 8890 / 3252 |

两组重复各 3 次均为当时评分 3/3，单列不扩充不同交易分母。30 笔配对中，18 保持 verified，10 保持 partial，一笔保持生成失败，一笔 verified→生成失败；不宣称因果质量改善。无不足终态不代表无依据缺口；partial 10 笔的图缺口保留。失误是依据核验问题，不能因为 schema 合法就算报告正确。

旧记录未保存 request_id，且没有独立冷/热启动观测；保留 null/unknown。Ollama load_duration 另行统计，不据此补造 cold/warm。P95 是小样本描述，既非生产 SLO，也非并发/负载结果。工具耗时、HTTP 尝试、整个案例耗时和报告统计分开，不重复加总。

## 漂移结果

| 字段 | 实际历史 PSI | 预设判断 | 合成扰动 PSI |
|---|---:|---|---:|
| 交易金额 | 0.002737 | stable | 19.100790 / alert |
| 24 小时交易数 | 0.081160 | stable | 2.662654 / alert |
| 冻结模型风险分数 | 0.002705 | stable | 2.575460 / alert |

参考最大时间 14417347 < 比较最小时间 14417365，同时间不跨组。0.10 warning / 0.25 alert、0.5 计数平滑、缺失/尾部桶均预先冻结。实际缺失率为 0；合成列统一加入 25% 缺失，金额另加大幅平移。合成告警不计入真实漂移成绩。

只读已观察历史测试数据，分数绑定既有 x3-scores 和模型决策；没有标签、没有新增预测/训练，也没有新盲测或生产分布。PSI 不能证明 AUC/AP 下降，低 PSI 也不能证明模型正确。

## 恢复与安全边界

使用有本轮所有权标签的新随机 384 MiB pgvector 容器，loopback 端口、单个只读密码挂载；source 和 restore 是两个不同的新数据库。实际停止本轮 DB 后 API 返回 503，重启读回原哈希；真正的备份恢复另做，不能把这个重启当恢复。

pg_dump/pg_restore 后，12 表 88 行逻辑清单相同；schema、精确 numeric 文本、索引/约束与扩展版本也核对。pgvector 政策行和受限应用角色验证通过。合成预算的 uncertain/settled 两种尝试及 0.060000 元占用保留；这不是实际消费，也不写旧总账。恢复后待审继续，错误报告哈希 409、批准 200、同决定重复 200、改成相反决定 409，外部动作始终 0；原源库不因目标续接而变化。

模型连接失败和传输超时各最多 2 次尝试/1 次重试；总截止采用受控时钟验证 504，不是任意卡死 SDK 的强制终止保证。“expired_policy_stops_generation”探针实际使用政策尚未生效的日期，验证无当期政策时 0 次生成；另一个 stale_version 探针验证已失效旧版本返回后关闭失败。注入 auto_block 也关闭失败。GNN 匹配控制组 201、错误版本 500。生成与 embedding 都是机制 fixture，不是新增真实模型评估。

备份（未加密、私有、仅合成调查内容）在 `E:/GraphRisk-Agent/artifacts/stage-09/route7/20261017-ops-v1/recovery-v3/isolated-investigations.dump`；保留至原路线 8 人工复核后，由用户决定归档/清理。字节/哈希：34163 / `8e0b8af1ebf4757a2adbf0f98c84e7680cbd6c816827c6016285feea390e99dc`。它不进入 Git、wheel 或镜像；chmod 不等于 Windows 完整 ACL 或加密备份。损坏归档先拒绝，恢复目标必须新建空库，不覆盖旧库。

六 canary 扫描范围仅限本轮 API 响应与两个白名单日志流；备份按私有材料另管，不宣称全机无敏感日志。当前单 worker、reviewer_id 未认证、无实际封卡工具；生产鉴权/授权、多 worker、加密异地备份、全实例 DR、RTO/RPO 与主动告警通知均未验收。

## 回归、失败记录与旧证据

- 最终 Windows CPU：**839 passed / 1 旧符号链接权限 skip**；数据库四文件明确分层，不把缺 DSN 跳过计入通过。
- 隔离 PostgreSQL 回归：**26 passed / 0 skip**，另有既有 **15/15** 受限账号机制探针；与本路线 15 项故障/恢复探针分别计数。
- 静态 **287** Python AST、**47** 冻结文件 Git 往返、两个 wheel 构建及脱离源码导入通过；新备份文件类型的 wheel 拒绝测试纳入。
- `regression-v3/` 保留最终各层摘要。新增测试已经接入同分支 GitHub workflow；本轮提交、远程实际 run 和日志证据单独写入 acceptance，不借用旧 b550d05 run。独立 GraphRAG 未改业务代码；本地本轮未重复 91+3 或另建应用镜像，不把此前结果报作本轮。
- `recovery-v1` 为真实失败：Docker 重启重分配了临时端口。改用本轮固定空闲 loopback 端口后 v2 通过，最终代码 v3 再次通过；失败记录保留，均只清理本轮拥有资源。
- `regression-v1/v2` CPU 各 2 项新日志测试失败。日志实际输出，父 logger 配置阻断 caplog 捕获；改成直接附加测试 handler 并恢复状态后完整回归通过。没有放宽业务断言或隐藏失败。
- 旧 5B 只读核验 **530 文件 / confirmation passed / coverage 177162 行** 通过，训练与生成总账原哈希不变；GNN 增量失败/不晋级保持，38 次 fit 尝试与 130 次生成的旧分母不改。

工程完成与本人学习分别记录。L7 三次 30 分钟任务已就绪，未代签；下一步等用户启动原路线 8，总验收后才考虑另行启动关系模式检索专项。
