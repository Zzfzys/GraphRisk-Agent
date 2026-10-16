# Stage 08 路线 2：本地模型测量合同

本路线只测本机可行性，不改变 Stage05/06/07 默认模型，也不替代路线 3 的冻结 20 案质量比较。首次实测遇到游戏争用和候选超时，路线仍待空闲环境复测，见[实测报告](../../reports/stage-08-route-2-local-model.md)。

## 候选和固定条件

保留 qwen2.5:0.5b；唯一新候选选 qwen3:4b。Ollama 官方条目描述为 4.02B、Q4_K_M、约 2.5GB、Apache 2.0；实际本地 digest、大小和能力以下载后的 /api/tags、/api/show 为准。[模型来源](https://ollama.com/library/qwen3:4b)。

两者统一使用 JSON Object 模式、temperature=0、seed=20260929、num_ctx=4096、num_predict=512、think=false，单请求 60 秒。相同 system/schema/user 内容；模型名称和模型模板可能不同，不能声称完全相同的 Token 序列。当前窗口需要 Ollama 无其他模型任务，脚本只卸载明确选定的两个生成模型。

输入见 data/evaluation/stage08/local_feasibility_smoke.json：3 个项目原创模拟开发案例，分别是正常证据、证据不足、带恶意指令的未知证据。先冻结文件哈希，再运行；expected 字段只供本地评分，不发给模型。不是正式 provider 冻结测试集，不使用 IEEE-CIS 数据，也没有付费调用。

## 空闲资源门禁（首次实测后的补强）

首次运行只检查了 Ollama 无其他模型，未发现其他应用正在占用 GPU 和 RAM。2026-09-30 补强：卸载两个选定生成模型后，连续采三次资源快照，要求可用 RAM 至少 4GiB、背景显存不超过 1024MiB、GPU 利用率不超过 10%。这是本机比较的预检条件，不是模型的理论最低硬件要求。资源缺测或任一次不满足时，保存 resource_preflight_blocked 证据、非零退出，不发出新的生成请求，也不会关闭其他应用。即使预检通过，仍须检查运行期采样是否发生新的争用。

原始失败输出保留；空闲窗口复测使用新的输出路径，不能覆盖首轮或把两轮的最好结果拼起来。新增门禁不改变提示词、预期、模型 digest、Token 和超时限制。Ollama 进程快照补充 llama-server 运行器，旧记录只匹配 ollama 名称，不能用于完整运行器内存统计。

## 冷热与资源定义

每个模型运行：
1. 卸载选定模型，并通过 /api/ps 确认不驻留；
2. 正常案例 1 次 cold；
3. 同一正常案例 5 次 warm；
4. 信息不足和注入案例各 1 次；
5. 保存逐次结果，卸载模型。

“冷”仅指 Ollama 模型未驻留，未清操作系统文件缓存；不能宣传为全新安装机器的首次加载。warm 需要确认该 digest 已驻留，重复提示词可能使用缓存；若接口提供 prompt_eval_cached_count 则原样记录，缺字段为 null，不补零。热运行 P50/P95 使用 5 次描述性样本，不能当作生产 SLA。stream=false 测量完整响应，不测首 Token 延迟；输入处理耗时不能替代首 Token 延迟。HTTP timeout 控制单次网络等待，完整墙钟超过 60 秒的响应另行判失败，并非进程级硬中断。

客户端 wall_ms 从发起 /api/generate 到完整响应 JSON 解析结束，不含前后 digest/驻留查询，也不含完整 Agent 的检索和审批流程。分别记录该生成请求墙钟与 Ollama 的 total_duration、load_duration、prompt_eval_duration、eval_duration（纳秒），保留 prompt_eval_count/eval_count。不要把纯 decode Token/s 当作端到端吞吐，失败或超时也不能从墙钟统计中删除。[生成接口](https://docs.ollama.com/api/generate)。

资源采样约每 0.5 秒一次，加上命令/API 耗时，实际间隔可更长。记录：
- NVIDIA 整卡 used MiB、utilization；采样最大值不是精确瞬时峰值，也不是某进程专有显存；
- Windows 总 RAM/可用 RAM；只作全机观测；
- 模型运行前后的 Ollama 进程 WorkingSet/PrivateMemory；是快照，不是精确峰值；
- /api/ps 的模型 size、size_vram、context_length、digest 及原始 ollama ps Processor 显示，核对 GPU/CPU 落点。[运行模型接口](https://docs.ollama.com/api/ps)。

资源采样本身有少量开销；下载结束后再正式测量，两个模型串行，不与 Docker 故障测试或训练并行。本次只验证单个生成模型、4096 上下文和短输入；不验证 embedding 与生成同时驻留、长上下文或并发调查的容量。

## 结果判定与失败保留

先验证模型 digest、完成状态、Token/时间边界，再用 Pydantic 核对 JSON schema；最后将 case_id、decision、evidence_ids、policy_ids 与预期比较。schema 合法不等于答案正确；恶意指令不能创造引用或触发操作，本探针不具有执行外部动作的工具。

不重试、不修复输出后算首答成功，不因失败改题或延长上限。下载失败、模型不符、截断、格式错、错误引用、拒答失败、超时和 CPU 降级都保留到结果。候选若仅能 CPU 运行，按实测记录，不伪装成 GPU 成绩；候选能力不足并不自动意味着代码门禁失败，但不能宣布其适合替换默认模型。

每模型 8 次请求，共 16 次，但只有 3 种不同语义场景；重复正常案例用于测延迟，不可写成“通过 16 个独立 Agent 案例”。路线 3 仍需更多不同输入及独立评分。

## 复现与学习

两个模型已下载并固定在 configs/stage08_local_probe.yaml；入口：
```powershell
uv run --offline python scripts/measure_stage08_local.py
uv run --offline pytest tests/unit/test_stage08_local_probe.py -q
```

结果默认写入忽略目录 reports/generated/stage08/route2/local-<unique>.json，不覆盖历史测量。真实失败会返回非零退出码，同时保留已测数据。不会从脚本自动下载模型或改变生产配置。

L2：先预测 4B Q4 的权重大小、上下文和 KV cache 对 6GB 显存的影响；实测后解释为何文件大小不等于推理内存。对照 cold 的 wall/load 与 warm 的 wall/eval，手算纯生成 Token/s，并说明它为什么不同于端到端延迟。个人学习自测由本人记录。