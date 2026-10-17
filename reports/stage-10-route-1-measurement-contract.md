# Stage 10 路线 1：冻结样本、性能口径与实验运行器验收

2026-10-17：**路线 1 工程完成；路线 2～8 Planned，L1 本人 Pending。** 本轮冻结并测试运行器，没有新增真实训练、真实模型请求或付费调用，没有产生当前增强 Agent 的新质量/性能成绩。

## 实现结果

| 条件 | 证据和结果 |
|---|---|
| 机器可读合同 | configs/stage10_measurement.json，固定模型/digest/提示/schema设置、10首次+2重复、9/10门槛、unsafe=0、14追加请求、P99与资源口径 |
| 样本冻结 | 177,162 行覆盖记录只投影 ID/event_second/split/selection_cutoff；验证/测试各88,581，选5+5；按时间/ID均匀排名，不读分数/标签挑选 |
| 排除历史 | 12份历史/源评估清单汇总58个ID/占位符，其中49个匹配覆盖交易；全部排除。新增题不是新的欺诈盲测 |
| 稳定输入 | manifest SHA `2861adba1df19526c6d517eaf612d3b5880c504da2dd5603b367fdfca16fad5b`；391原始源文件、7运行输入、12排除输入、覆盖profile的35个依赖核对 |
| 预算边界 | 全阶段唯一追加JSONL，绑定旧130/144，不改旧文件；发送前预约+fsync，hash链、OS锁、每run上限2/全局14。崩溃/超时未知阻止后续发送，不退款不自动重发 |
| 实际发送接入 | 通过原生OllamaQualityProvider及with_deadline/RetryingProvider验证；每个POST在HTTP边界计数，GET tags不算生成；真实传输只能由守卫创建retries=0的HTTPTransport，禁止藏在底层的重试 |
| 请求与来源 | 固定loopback、模型、think=false、keep_alive=0、token512、温度/schema/packet hash；改请求设置、标签/分数字段参与选择、来源错配均失败关闭 |
| 统计 | 线性Hyndman–Fan7，所有请求/成功子集分开，分组隔离scope/组件/并发/冷暖；快速组n<100和真实Agent小样本P99=null，失败与未运行保留 |
| 逐次记录 | HTTP耗时/状态/序号、prompt/schema的绑定和未知Token；操作级Context/Envelope附录记录版本、UTC起止、计时边界、资源范围/未知值、实际发送计数差值。原始结果/异常消息不进入白名单汇总 |
| 真实与模拟 | 12次原生provider+MockTransport+假时钟演练通过；独立fixture台账用12，真实追加0、剩余14。quality_evidence/performance_evidence均false |
| 测试 | 最终CPU898 passed/1旧Windows符号链接权限skip/0失败/0错误，包含47新增（41核心+6计时）；真库4文件显式另层。最终提交自己的CI单独封存，不借前提交成绩 |

源码入口：[核心合同/预算/统计](../src/graphrisk/demo/stage10_measurement.py)、[冻结/check/dry-run](../src/graphrisk/demo/stage10_registration.py)、[操作计时附录](../src/graphrisk/demo/stage10_telemetry.py)、[CLI](../scripts/run_stage10_measurement.py)。原应用流程、提示和模型权重未修改。

## 私有证据与冻结顺序

- 工作/验证：`E:/GraphRisk-Agent/reports/generated/stage10/route1/20261017-contract-v1/`，含47定向测试、最终CPU、旧文档归档和首次失败。
- 正式样本/注册：`.../route1/20261017-frozen-v1/` 的manifest/registration与mock-v1；不覆盖原始清单，不用换目录重获预算。
- 计时附录：在真实请求仍为0时，追加2个源文件；`verification-v2/source-appendix.json`封存最终代码/全量快照，保留首版附录。修复了协议hash和实验hash的混用，增加断言。原391文件、题目、门槛及HTTP守卫未变。
- `check`会列出相对原清单新增的这2个文件；路线2须绑定实际执行入口与此附录后才发送。新增文件列出不等于路线2已经实施。
- 唯一真实追加台账：`reports/generated/stage10/generation-ledger.jsonl`，本轮只有header，没有reservation。旧生成/fit台账原hash不变。
- 最终 Git、实际 CI、台账/文件hash与适用范围存本轮私有acceptance.json，后续可核对；原实验来源不改。

## 遇到的问题

首次pytest未显式basetemp，C盘工作树创建临时目录被权限拒绝：14通过/17setup errors，非业务断言失败。后续固定E盘唯一目录，31项、41项和新增附录验证通过，最终完整CPU898通过。初轮记录保留，未改系统ACL/TEMP。不要把这些不同轮次的通过数相加。

计时附录新增于样本冻结之后、任何真实输出之前，所以单独封存版本，不重写原manifest或重置预算；后续必须再冻结真实执行编排。当前真实传输功能只作为可复用守卫，CLI不开放真实run，本轮没有访问本机Ollama生成端点。

## 学到了什么与下一步

采样不能挑成功案例；一次run可能对应多个HTTP请求；HTTP200不代表判断正确；缺失不是0；时间分位不能跨模式/冷暖混算；模拟演练只证明机制。具体75分钟本人练习和安全复查命令见[操作与学习](../docs/data/stage-10-measurement.md)。

下一路线2先登记真实执行入口、确认模型digest/资源与计数，再在同一固定样本和剩余额度内做质量/性能实测；不新训、不付费，不把模拟25ms或旧5A40/40当当前新成绩。Stage10保持In Progress，GNN不晋级、个人/实用性Pending、后续检索专项未启动。
