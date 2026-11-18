param(
    [ValidateSet('preflight','register','trial','api','workbench')]
    [string]$Component = 'preflight'
)
$ErrorActionPreference = 'Stop'
$taskProject = Split-Path -Parent $PSScriptRoot
. (Join-Path $taskProject 'scripts/enter_project_env.ps1')
Set-Location -LiteralPath $taskProject
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'E:\GraphRisk-Agent;E:\GraphRisk-Agent\artifacts\agent-trial-20261115-2u0xpeep\live;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\src;C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent\scripts'
$qaConfig = Join-Path $taskProject 'reports/case-qa-v1/20261117-live-quality-v3/config.json'
$qaApproval = Join-Path $taskProject 'reports/case-qa-v1/20261117-live-quality-v3/OUTBOUND-APPROVED.json'
if ($Component -eq 'workbench') {
    uv run --offline --no-sync streamlit run extensions/case_qa_live_v3/web.py --server.address 127.0.0.1 --server.port 8503 --browser.gatherUsageStats false
} else {
    uv run --offline --no-sync python -m extensions.case_qa_live_v3.runtime $Component --config $qaConfig --approval $qaApproval
}
exit $LASTEXITCODE
