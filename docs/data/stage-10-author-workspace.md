# 作者本机：C盘源码、E盘环境与工件

2026-10-18。这些绝对路径只适用于当前电脑，新读者使用[通用启动](stage-10-startup.md)。当前源码C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent；E:/GraphRisk-Agent保留环境、数据、模型、缓存及私有证据，E盘旧源码不是当前开发入口。

## 初始化

每个PowerShell先执行：

```powershell
. 'E:\GraphRisk-Agent\scripts\enter_project_env.ps1' | Out-Null
$source='C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent'
$runtime='E:\GraphRisk-Agent'
$env:PYTHONPATH="$source\src;$source;$source\scripts"
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:UV_PROJECT_ENVIRONMENT="$runtime\.venv"
Set-Location $runtime
```

本机不要对E盘旧项目执行新的依赖同步来代替当前锁文件；已有环境配合`--no-sync`使用。若需更新环境，应另核对C盘锁文件后安排。验证源码导入时从不含旧api/的目录运行：

```powershell
Push-Location "$runtime\artifacts\tmp"
try {
    uv run --offline --no-sync --project $runtime python -c "import api.main,graphrisk; print(api.main.__file__); print(graphrisk.__file__)"
} finally { Pop-Location }
```

预期两者来自C盘71df。单用`python -c`在E盘根目录检查api可能优先命中旧当前目录；必须明确源码/工作目录，不能只看Python版本。

## 当前增强网页：memory

窗口A初始化后：

```powershell
$env:GRAPHRISK_STAGE06_CONFIG="$runtime\configs\stage09_coverage.yaml"
$env:GRAPHRISK_STAGE07_STORAGE='memory'
uv run --offline --no-sync uvicorn api.main:app --app-dir $source --host 127.0.0.1 --port 8000 --workers 1
```

窗口B同样初始化后：

```powershell
$env:GRAPHRISK_STAGE06_API_URL='http://127.0.0.1:8000'
Remove-Item Env:GRAPHRISK_HISTORY_REPLAY -ErrorAction SilentlyContinue
uv run --offline --no-sync streamlit run "$source\streamlit_app.py" --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

窗口C初始化后只读检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/coverage/3488959
```

浏览器打开http://127.0.0.1:8501。显式选择E盘configs/stage09_coverage.yaml与C盘API/UI；应先核对配置与C盘对应文件字节一致。旧coverage脚本未向Uvicorn传app-dir，分离布局下可能导入E盘旧api，不使用它启动本机API；通用同根检出没有这个旧目录歧义。启动本身不创建调查，点击创建才会真实生成。当前Stage10正式预算142/144剩2；不要把普通页面按钮当实验预算守卫，也不要用它重跑失败题。查看保存记录和回放可用于练习。前台Ctrl+C分别停止本轮窗口；memory报告不跨API重启保存。

## 当前增强网页：PostgreSQL

需要先在本进程安全配置MIGRATION_DSN、APP_DSN和受限DSN；配置名见[通用指南](stage-10-startup.md#postgresql-mode)，不要打印连接串。初始化后：

```powershell
uv run --offline --no-sync python "$source\scripts\migrate_stage07.py"
uv run --offline --no-sync python "$source\scripts\grant_stage07_app.py"
$env:GRAPHRISK_STAGE06_CONFIG="$runtime\configs\stage09_coverage.yaml"
$env:GRAPHRISK_STAGE07_STORAGE='postgres'
uv run --offline --no-sync uvicorn api.main:app --app-dir $source --host 127.0.0.1 --port 8000 --workers 1
```

UI仍按memory窗口B的显式Streamlit命令启动，只负责HTTP展示；API的存储模式由API窗口决定。重启练习只重启自己的API并读同run_id，不删除数据库卷。真正新建仍是实模型调用，当前未安排追加生成。

## 历史报告回放

这是零新模型调用的练习方式，需要Docker已就绪且固定镜像已存在，以及私有Stage09旧报告。窗口A初始化后使用新的唯一输出目录：

```powershell
$replayDir=Join-Path $runtime ('reports\generated\stage09\route8\stage10-personal-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
uv run --offline --no-sync python "$source\scripts\run_stage09_final.py" serve --output-dir $replayDir
```

该窗口打印API地址，service.json在刚才明确指定的目录。此沙箱ready预期503（不具备新调查依赖），读取已有run仍可成功；不能按真实服务ready标准误判它失败。它建立本轮隔离数据库、加载旧xgboost-v1/candidate-4报告；不能称为当前增强配置新推理。窗口B初始化后，将`$replayDir`设为窗口A实际路径，再执行：

```powershell
$session=Get-Content -LiteralPath (Join-Path $replayDir 'service.json') -Raw | ConvertFrom-Json
if ($session.mode -ne 'historical_real_report_replay_no_new_inference') { throw '回放模式不匹配' }
$env:GRAPHRISK_STAGE06_API_URL=$session.api_url
$env:GRAPHRISK_HISTORY_REPLAY='1'
$session.views | ForEach-Object { 'http://127.0.0.1:8502/?run_id=' + $_.run_id }
uv run --offline --no-sync streamlit run "$source\streamlit_app.py" --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

打开打印的链接，确认页面有历史回放标识；练习读证据、填演示reviewer_id/备注、拒绝并刷新。标识变量本身不会切换后端，必须使用本轮service.json。回放用API随机端口，8502若占用可同步换空闲UI端口与链接，不结束其他服务。先Ctrl+C停止UI，再停止窗口A；本轮sandbox清理应有记录。该serve有资源与时长门禁，非永久生产部署。更完整说明见[Stage09个人复现](stage-09-final-reproduction.md)。

本轮路线4只检查现有入口/语法/CLI，不启动这些真实调查或回放数据库；路线5彩排和路线6复现将分别记录实际操作。后续Agent质量提醒stage10-agent仍待Stage10工程总审计后触发。
