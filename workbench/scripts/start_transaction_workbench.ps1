param([ValidateSet('api','workbench')][string]$Component = 'workbench')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'E:\GraphRisk-Agent'
. .\scripts\enter_project_env.ps1
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'E:\GraphRisk-Agent;E:\GraphRisk-Agent\artifacts\agent-trial-20261115-2u0xpeep\live;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\scripts'
$taskConfig = 'E:\GraphRisk-Agent\reports\transaction-workbench\config.json'
if ($Component -eq 'api') {
    uv run --offline --no-sync python -m extensions.transaction_workbench.runtime --config $taskConfig
} else {
    uv run --offline --no-sync streamlit run extensions/transaction_workbench/web.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
}
exit $LASTEXITCODE
