# Stage 06 路线 4：Streamlit 调查工作台验收

> 2026-09-26；路线 4 工程门禁通过，个人 L4 学习自测待学习者完成。Stage 06 总体仍为 In Progress。

## 交付与边界

- 根目录 `streamlit_app.py` 是单页本地入口；`src/graphrisk/demo/api_client.py` 只调用版本化 FastAPI。工作台不加载原始 IEEE-CIS 数据、模型、政策索引或 LangGraph 检查点。
- 调查表单要求交易 ID、显式政策日期和图邻居上限。仅点击“开始新调查”才 `POST /api/v1/investigations`；普通 rerun、刷新和审批状态读取只按 `run_id` 发 GET。`run_id` 同时保留于 UI 会话和 URL，浏览器刷新可恢复当前视图。
- 页面显示交易事实、严格历史窗口、Stage 02 XGBoost 分数、GraphSAGE/融合分数、图邻居的共享实体类型及目标前时间差、政策标题/版本/章节/原文/出处、报告不确定性和受控工作流审计。图成功但无邻居与图工具不可用使用不同文案；无政策不提供审批控件。
- 审批表单在提交前展示冻结 SHA-256；按 API 返回的会话、交易和哈希提交批准/拒绝及备注。终态来自 API，`reviewer_id` 标为未认证演示字段。页面始终提示模拟政策、分数未校准、无自动处置；不读取或显示测试标签。

## 版本匹配参考与测试

已运行 `developing-with-streamlit` 发现脚本，定位仓库 `.venv` 内的 Streamlit **1.64.0** 指南，并阅读 best practices、layout、session state、testing 与 design 参考；表单和状态控件的签名由本地 `streamlit docs` 核对。未使用自定义 CSS 或新 UI 框架。

```powershell
uv run --offline pytest tests/demo/test_streamlit_app.py -q
uv run --offline pytest tests/integration -q
uv run --offline pytest tests/unit tests/api tests/demo tests/test_smoke.py -q
```

5 项 `AppTest` 通过：正常调查的批准/拒绝与备注、重复 rerun 不重新创建调查；图查询成功但无邻居、图不可用、无政策、风险模型失败和生成失败的 UI 分支。集成测试 **64 passed**；其余单元、API、界面和 smoke 测试 **228 passed, 1 warning**，共 292 项。警告为 Starlette TestClient 使用 anyio 旧别名。

尝试单次 `uv run --offline pytest -q` 时，本机同时运行的 API 与 Streamlit 占用内存，pytest 曾在收集阶段 `MemoryError`；停止 Streamlit 后再试，DuckDB 小 fixture 仍因可用内存不足出现 `OutOfMemoryException`（3 failed、5 errors、283 passed）。拆分为上述两个独立进程运行后全部通过；这不是代码断言失败。若要复验单次全套运行，先关闭其它高内存进程并确认可用内存，再执行全套命令。

## 真实浏览器验收

本机 `127.0.0.1:8000/api/v1/ready` 返回 200 `ready`；该端口已有一个本地 API 进程，路线 4 未将其关闭。启动 `127.0.0.1:8501` 的 Streamlit 后，在浏览器提交 IEEE-CIS held-out 交易 `3488959`、政策日期 `2026-07-01`、关系上限 3。页面显示 `verified`、XGBoost `0.1967`、GraphSAGE `0.2969`、融合 `0.1883`，注明三者未校准；图查询成功但当前窗口没有风险邻居，因而显示“无邻居”而非“图失败”。模拟政策 V1 可展开看到原文、`source_ref` 和 `chunk_id`，未显示测试标签。

同一页面提交 `reject`、演示复核员 `demo-reviewer` 和备注后，显示 `rejected`、审计 2 条、外部处置 0。浏览器重新加载带 `run_id` 的 URL 后，仍从 API 读回相同终态和备注，未重新发起调查。测试结束后停止本次启动的 Streamlit；现有 API 进程未由路线 4 管理。下一路线负责可复现的本地启动与 Docker Compose 验收。

## L4 自测

学习者可依次点击“开始新调查”“刷新当前状态”和浏览器刷新，观察只有第一步会产生新的 `run_id`。然后解释：为什么 `st.session_state` 仅保存选中运行，为什么报告和审批必须从 API 读取，为什么一个 `[]` 邻居列表在图状态 `ok` 与 `unavailable` 下文案不同。最后在“无政策”分支确认页面没有审批按钮。完成解释与实际操作前，个人 L4 不标为完成。
