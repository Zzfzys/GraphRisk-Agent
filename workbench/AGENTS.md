# GraphRisk-Agent repository instructions

## 当前项目入口

- 读完本文件后，先读 [CURRENT.md](E:/GraphRisk-Agent/CURRENT.md)，再按任务需要读取其中链接的计划与证据；这是当前状态与下一步的唯一摘要入口。
- 任务改变当前源码、启动方式、采用版本、下一步或暂停条件时，核验依据并更新CURRENT.md；不要在多个README、计划或日期文档中复制一套实时状态。
- 本文件维护稳定工程规则；旧文档的状态段落是当时快照，不自动覆盖用户最新指令或经核验的当前入口。


## Project objective

Build a portfolio-grade fraud investigation Agent for LLM/Agent application engineering roles. The Agent must ground its output in transaction data, ML/GNN model results, graph evidence, and cited policy documents. High-risk actions require human approval.

## Environment

- Python 3.11 only;
- Use uv and the checked-in `uv.lock`;
- Keep uv cache, managed Python, and the virtual environment inside the repository;
- Before running project commands in a Windows PowerShell process, dot-source scripts/enter_project_env.ps1 to keep TEMP/TMP and pip/uv caches inside the repository. Repeat for each new shell process; do not change system-wide temporary directories or delete application runtimes;
- Run tests with `uv run --offline pytest` after dependencies are synchronized;
- Do not introduce a new framework when an existing dependency covers the requirement.

## Source layout

- Application code: `src/graphrisk/`;
- Executable wrappers: `scripts/`;
- Configuration: `configs/`;
- Tests: `tests/unit/` and `tests/integration/`;
- Stage plans and evidence: `docs/plans/`;
- Data contracts and provenance: `docs/data/` and `data/provenance.example.json`;
- Generated data, reports, model files, local environments, caches, and secrets are not committed.

## Data correctness

- Split fraud data chronologically; never shuffle before train/validation/test assignment;
- Historical features may only use events with earlier event time;
- Treat equal timestamps as simultaneous unless a documented source ordering exists;
- Do not use labels in feature construction;
- Training graphs must not contain future validation or test relationships;
- Keep synthetic and real-dataset metrics separate;
- `identity_key` is a heuristic, not a verified customer ID.

## Engineering rules

- New behavior requires meaningful tests;
- Tools and data functions use explicit inputs and structured outputs;
- Fail validation with actionable errors instead of silently coercing invalid data;
- Keep secrets out of source, logs, tests, and documentation;
- Prefer small deterministic fixtures for tests;
- Update the active stage document whenever scope, behavior, evidence, or a decision changes;
- Update `PROJECT_PLAN.md` when a change affects multiple stages or the overall scope;
- Do not mark a stage done until every acceptance criterion has evidence.

<a id="task-closeout"></a>
## 任务收尾与资料维护

每次任务最终汇报前，按下列清单做增量收尾，只覆盖本任务触及的文件、产物和直接引用，不自动扩大为全仓盘点或清理。纯解释／问答且没有文件、产物或状态变化时，可以直接判定无需资料更新，不强制改文件或生成收尾日志。

1. **核实实际结果。** 检查本任务的变更、执行记录和相关验证，区分已完成、失败、中断、延期及待验收；保护用户已有修改，不为“清爽”回退其他工作。按实际变化更新活跃任务／阶段计划，证据不足不勾完成。
2. **同步必要说明。** 行为、接口或命令变化时更新对应文档；当前源码、启动方式、采用版本、下一步、授权或暂停条件变化时更新[CURRENT.md](CURRENT.md)。只有跨阶段或总体范围变化才更新PROJECT_PLAN。可更新文档原位维护，不另建日期版“最新状态”或复制实时进度。
3. **整理本任务资料。** 对新增、替代、停用或删除的持久材料，在[资料状态索引](docs/MATERIALS.md)及其机器清单中增量登记用途、状态、替代关系和去向；无变化不全量重扫。只清理已核实的本任务临时残留。退役资料前核对独立价值、直接引用、已知冻结／账本保护及恢复条件；修正可更新的旧引用，记录精确路径和恢复依据。冻结材料、有效合同、授权、原稿、账本、模型回退及未知／失败证据继续按约保留，不覆写历史回执；无法确认的条目保留待确认。
4. **验证改动范围。** 检查本任务改动的链接、替代引用及必要测试。纯文档维护可验证链接、内容差异和清单一致性；不为收尾额外重跑全量测试、训练或模型调用，执行仍遵守本任务已有授权。没有新增外发用途、额度或越权事项时，不重复索取已有的常规操作授权。
5. **简要汇报收口。** 最终说明结果、实际验证、尚未完成事项及重要资料的删除／保留去向。只记录必要证据，不为证明“已维护”另写一份Markdown；受冻结或审计约束的新证据按原合同另存，不覆盖旧记录。

## Current commands

```powershell
uv sync --locked
uv run --offline pytest
uv run --offline python scripts\generate_synthetic_data.py --config configs\dev.yaml
uv run --offline python scripts\build_features.py --config configs\dev.yaml
uv run --offline python scripts\build_features.py --config configs\ieee_cis.yaml
uv run --offline python scripts\train_baselines.py --config configs\train_baseline.yaml
uv run --offline python scripts\train_baselines.py --config configs\train_baseline_ieee_cis.yaml
uv run --offline python scripts\list_mlflow_runs.py --config configs\train_baseline.yaml
uv run --offline python scripts\run_investigation.py 2987100 --data-config configs\ieee_cis.yaml --training-config configs\train_baseline_ieee_cis.yaml --relation-limit 3
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```
