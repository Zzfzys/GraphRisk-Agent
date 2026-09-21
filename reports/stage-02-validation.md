# Stage 02 验证报告

> 验证日期：2026-09-21  
> 数据来源：确定性合成数据  
> 数据规模：训练集 3,500、验证集 750、测试集 750

## 结论

Stage 02 的训练、评估、实验追踪和在线预测门禁均已通过。三个模型使用 Stage 01 固定的时间切分与同一组 point-in-time 特征。模型选择只比较验证集 PR-AUC，最终选择 Logistic Regression；测试集仅用于选型后的独立报告。

这些指标只证明管线和评估方法可运行，不代表 IEEE-CIS 或真实业务效果。

## 模型对比

| 模型 | 验证 PR-AUC | 测试 PR-AUC | 测试 ROC-AUC | 测试 Recall | 测试 F1 | 测试 Recall@Top-5% | 单条预测 P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dummy | 0.0213 | 0.0280 | 0.5000 | 0.0000 | 0.0000 | 0.0000 | 8.54 ms |
| Logistic Regression | **0.1457** | 0.1892 | 0.7799 | 0.5238 | 0.2292 | 0.2381 | 8.40 ms |
| XGBoost | 0.1363 | **0.2024** | **0.7914** | 0.3333 | **0.2545** | **0.3333** | 9.21 ms |

完整的固定快照见 [stage-02-baseline-results.csv](stage-02-baseline-results.csv)。训练命令还会生成被 Git 忽略的 `reports/generated/baseline_results.csv`，其中包含未舍入数值、训练时间、模型大小和全部 Top-K 指标。

XGBoost 的测试 PR-AUC 高于 Logistic Regression，但它的验证 PR-AUC 较低。为了不利用测试集调模型或选模型，服务仍加载验证集胜出的 Logistic Regression。

## 实验与产物

- MLflow 使用本地 SQLite 后端，实验中三个模型均为 `FINISHED`；
- 每个运行记录随机种子、数据规模、类别权重、特征数、验证/测试指标、训练时间、P95 延迟、模型大小和 Git commit；
- champion 产物包含 `model.joblib` 与 `metadata.json`；
- champion metadata 明确记录 `selected_by=validation_pr_auc`、特征列、风险阈值和数据来源；
- 649 个变换后特征来自 9 个数值特征和 5 个类别特征。

运行与检查命令：

```powershell
uv run --offline python scripts\train_baselines.py --config configs\train_baseline.yaml
uv run --offline python scripts\list_mlflow_runs.py --config configs\train_baseline.yaml
```

## API 验证

真实 Uvicorn 进程完成了以下检查：

| 请求 | 状态 | 结果 |
|---|---:|---|
| `GET /health` | 200 | `model_artifact_available=true` |
| `GET /model/info` | 200 | 返回 champion 名称、版本、数据来源和验证/测试指标 |
| `POST /risk/predict`，`T00000001` | 200 | 分数 `0.117666`、等级 `low`、版本 `logistic_regression-v1` |
| `POST /risk/predict`，不存在的编号 | 404 | 返回 `transaction_not_found` 结构化错误 |
| `POST /risk/predict`，空编号 | 422 | Pydantic 结构化校验错误 |

启动命令：

```powershell
uv run --offline uvicorn api.main:app --host 127.0.0.1 --port 8000
```

## 自动化验证

```text
uv sync --locked --offline                         PASS
uv lock --check --offline                          PASS
uv run --offline pytest                            25 passed
uv run --offline python -m compileall src api scripts tests  PASS
uv build --offline                                 PASS
```

测试覆盖指标计算、Top-K 边界、模型训练与概率输出、champion 加载、风险等级、三个 API 端点以及错误响应。Stage 01 的时间特征和数据泄漏测试也在同一套测试中继续通过。

## 已知边界

- 当前只使用合成数据，真实 IEEE-CIS 数据仍待下载和 adapter 接入；
- `0.30` 与 `0.70` 风险等级阈值是演示规则，未做业务成本校准；
- 当前类别 ID 采用 one-hot 编码，数据规模扩大后需评估高基数处理；
- 当前概率未做 calibration，不能解释为真实欺诈发生概率；
- 延迟来自本机单请求、小数据文件环境，不能替代并发压测。
