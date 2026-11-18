param([ValidateSet('api','workbench')][string]$Component)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $taskRoot 'scripts/enter_project_env.ps1')
Set-Location -LiteralPath $taskRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'E:\GraphRisk-Agent;E:\GraphRisk-Agent\artifacts\agent-trial-20261115-2u0xpeep\live;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\scripts'
$env:GRAPHRISK_CONVERSATION_API_URL = 'http://127.0.0.1:8002'
$env:GRAPHRISK_UPGRADE_LIVE = $null
$taskManifest = Join-Path $taskRoot 'reports/case-qa-web-v1/20261118-web-v1/manifest.json'
if ($Component -eq 'api') {
    uv run --offline --no-sync python -m extensions.case_qa_web_v1.runtime --manifest $taskManifest
} else {
    uv run --offline --no-sync streamlit run extensions/case_qa_web_v1/web.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
}
exit $LASTEXITCODE
