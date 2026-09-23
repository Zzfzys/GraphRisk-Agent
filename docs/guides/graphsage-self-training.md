# 自己训练与调参 GraphSAGE

本指南使用仓库现有的 IEEE-CIS 图产物。所有命令都在 `E:\GraphRisk-Agent` 的 PowerShell 中运行。模型文件位于 Git 忽略的 `artifacts/`，不会随代码提交。

## 1. 检查输入和已有基线

```powershell
Set-Location E:\GraphRisk-Agent
Test-Path artifacts\stage-04\ieee-cis\graph-manifest.json
Test-Path artifacts\stage-04\ieee-cis\models\graphsage-v1\metadata.json
```

两行都应显示 `True`。如果图 manifest 不存在，先按[图产物契约](../data/stage-04-graph-artifacts.md)运行 `scripts\build_graph_artifacts.py`。如果只是查看此前的正式训练结果，无需重新训练：

```powershell
$base = 'artifacts\stage-04\ieee-cis\models\graphsage-v1'
$meta = Get-Content "$base\metadata.json" -Raw | ConvertFrom-Json
$meta | Select-Object model_version,best_epoch,stopped_after_epoch,device_actual,training_seconds
$meta.validation_metrics | Select-Object pr_auc,roc_auc,precision_at_1pct,recall_at_1pct
$meta.test_metrics | Select-Object pr_auc,roc_auc,precision_at_1pct,recall_at_1pct
Get-Content "$base\training-history.json" -Raw | ConvertFrom-Json |
  Format-Table epoch,train_loss,validation_pr_auc,validation_roc_auc
```

当前已验收的正式运行：最佳 epoch 2，验证 PR-AUC 0.178343，冻结后测试 PR-AUC 0.103733。训练 loss 下降、验证 PR-AUC 下降时，优先检查过拟合和时间漂移；不要只看 loss 或将 sigmoid 分数解释为已校准概率。

## 2. 建立一个不会覆盖基线的试验

```powershell
Copy-Item configs\graph_ieee_cis.yaml configs\graph_ieee_cis_trial.yaml
notepad configs\graph_ieee_cis_trial.yaml
```

首次调参只修改 `training` 下的模型参数：`learning_rate`、`dropout`、`hidden_channels`、`batch_size`、`max_epochs`、`early_stopping_patience`、`device`。例如先只把 `learning_rate: 0.001` 改成 `learning_rate: 0.0005`，其它保持不变，便于归因。约束是 `0 <= dropout < 1`、学习率大于 0、`early_stopping_patience <= max_epochs`。`auto` 优先使用 CUDA；`cpu` 强制 CPU。

保持 `dataset_source`、全部 `paths`、`model_version`、`random_seed`、`fanout` 和三个目标数不变，才能复用已经校验的同一图 manifest。尤其不要为每个试验随手改 `model_version`：图 manifest 会因此不匹配。一次只试少量预先想好的候选，用相同目标 ID 比较。

训练并**只看验证集**：

```powershell
uv run --offline python scripts\train_graphsage.py `
  --config configs\graph_ieee_cis_trial.yaml `
  --validation-only `
  --run-name lr0005
```

试验保存到 `artifacts\stage-04\ieee-cis\models\graphsage-v1\runs\lr0005\`。`--validation-only` 不读取测试标签，输出显示 `test_pr_auc=not_evaluated`，metadata 的 `test_evaluated` 为 `false`。必须指定唯一的 `--run-name`；已完成的同名试验不会被覆盖。当前机器正式 50k 训练约需 5～6 分钟，具体时间取决于设备和早停轮数。

## 3. 查看和比较候选

```powershell
$run = 'artifacts\stage-04\ieee-cis\models\graphsage-v1\runs\lr0005'
$meta = Get-Content "$run\metadata.json" -Raw | ConvertFrom-Json
$meta | Select-Object run_name,best_epoch,stopped_after_epoch,learning_rate,dropout,device_actual
$meta.validation_metrics | Select-Object pr_auc,roc_auc,precision_at_1pct,recall_at_1pct
Get-Content "$run\training-history.json" -Raw | ConvertFrom-Json |
  Format-Table epoch,train_loss,validation_pr_auc,validation_roc_auc
```

比较所有试验：

```powershell
Get-ChildItem artifacts\stage-04\ieee-cis\models\graphsage-v1\runs -Directory |
  ForEach-Object {
    $m = Get-Content (Join-Path $_.FullName 'metadata.json') -Raw | ConvertFrom-Json
    [pscustomobject]@{
      Run = $_.Name
      ValidationPRAUC = $m.validation_metrics.pr_auc
      BestEpoch = $m.best_epoch
      LearningRate = $m.learning_rate
      Dropout = $m.dropout
      TestEvaluated = $m.test_evaluated
    }
  } | Sort-Object ValidationPRAUC -Descending | Format-Table
```

以验证 PR-AUC 选择候选，同时看曲线是否只靠某一轮偶然尖峰。当前目标的验证正例率为 3.75%，PR-AUC 应连同正例率与 Precision@Top-K 解读。不要用已看过的测试 PR-AUC 反复挑参数。

## 4. 固定配置后做一次最终测试

选定配置后，用**新的运行名**按相同参数重新训练，省略 `--validation-only`：

```powershell
uv run --offline python scripts\train_graphsage.py `
  --config configs\graph_ieee_cis_trial.yaml `
  --run-name final_lr0005
```

程序只在验证集选好最佳权重后读取并评估测试标签，结果在该运行的 `metadata.json` 中，`test_evaluated` 为 `true`。同一配置和设备下，先核对最终运行与验证试验的曲线和最佳 epoch 是否一致；再报告测试 PR-AUC。最终试验不覆盖原始 `graphsage-v1` 基线或其它运行。

如果要改 `fanout`、三个目标数、`random_seed`、`model_version` 或数据来源，必须使用新的完整图产物目录，更新配置中所有 `artifacts/stage-04/...` 路径并重跑图构建；目标数改变还会改变比较集合。首次调参先保持它们固定。GraphSAGE 与 XGBoost 的正式同 ID 对比属于 Stage 04 路线 4。
