param([ValidateSet('api','workbench')][string]$Component)
$ErrorActionPreference = 'Stop'
. E:\GraphRisk-Agent\scripts\enter_project_env.ps1 | Out-Null
Set-Location -LiteralPath E:\GraphRisk-Agent
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = (Join-Path $PSScriptRoot 'live') + ';C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\scripts'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:GRAPHRISK_STAGE06_CONFIG = $null
$env:GRAPHRISK_CONVERSATION_API_URL = 'http://127.0.0.1:8002'
$original = 'E:\GraphRisk-Agent\reports\generated\agent-investigation-upgrade\route5\report-v3\20261115-web-live-six-v1'
$recovery = 'E:\GraphRisk-Agent\reports\generated\agent-investigation-upgrade\route5\report-v3\20261116-web-live-proxy-recovery-v1'
if ($Component -eq 'api') {
    $env:GRAPHRISK_UPGRADE_LIVE = '1'
    uv run --offline --no-sync python (Join-Path $PSScriptRoot 'live\serve_live_trial_recovery.py') --serve --config (Join-Path $original 'config.json') --approval (Join-Path $original 'OUTBOUND-APPROVED.json') --recovery-config (Join-Path $recovery 'recovery.json') --recovery-approval (Join-Path $recovery 'OUTBOUND-APPROVED.json') --port 8002
} else {
    $env:GRAPHRISK_UPGRADE_LIVE = $null
    uv run --offline --no-sync streamlit run (Join-Path $PSScriptRoot 'workbench-recovery.py') --server.address 127.0.0.1 --server.port 8501 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
}
exit $LASTEXITCODE