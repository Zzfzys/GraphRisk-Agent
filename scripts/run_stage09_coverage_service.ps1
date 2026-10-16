param(
    [ValidateSet('api','workbench')][string]$Component = 'api',
    [string]$ExecutionRoot = 'E:\GraphRisk-Agent'
)
$ErrorActionPreference = 'Stop'
$coverageSource = Split-Path $PSScriptRoot -Parent
. (Join-Path $ExecutionRoot 'scripts\enter_project_env.ps1') | Out-Null
$env:PYTHONPATH = "$coverageSource\src;$coverageSource"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:GRAPHRISK_STAGE06_CONFIG = Join-Path $ExecutionRoot 'configs\stage09_coverage.yaml'
$env:GRAPHRISK_STAGE07_STORAGE = 'memory'
Push-Location -LiteralPath $ExecutionRoot
try {
    if ($Component -eq 'api') {
        uv run --offline --no-sync uvicorn api.main:app --host 127.0.0.1 --port 8000
    } else {
        uv run --offline --no-sync streamlit run (Join-Path $coverageSource 'streamlit_app.py') --server.address 127.0.0.1 --server.port 8501
    }
} finally {
    Pop-Location
}
