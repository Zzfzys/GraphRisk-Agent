# Dot-source in each project terminal: . .\scripts\enter_project_env.ps1
# Process-only settings; no registry writes, deletions, or application migrations.
$taskRepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskTempRoot = Join-Path $taskRepoRoot 'artifacts\tmp'
$taskPipCacheRoot = Join-Path $taskRepoRoot 'artifacts\pip-cache'
$taskUvCacheRoot = Join-Path $taskRepoRoot '.uv-cache'
$taskPythonRoot = Join-Path $taskRepoRoot '.uv-python'
foreach ($taskDirectory in @($taskTempRoot, $taskPipCacheRoot, $taskUvCacheRoot, $taskPythonRoot)) {
    New-Item -ItemType Directory -Path $taskDirectory -Force -ErrorAction Stop | Out-Null
}
$env:TEMP = $taskTempRoot
$env:TMP = $taskTempRoot
$env:PIP_CACHE_DIR = $taskPipCacheRoot
$env:UV_CACHE_DIR = $taskUvCacheRoot
$env:UV_PYTHON_INSTALL_DIR = $taskPythonRoot

# Keep local API/Ollama/database traffic out of inherited HTTP proxies.
$taskLocalHosts = @('localhost', '127.0.0.1', '::1', 'host.docker.internal', 'db')
$taskExistingHosts = @($env:NO_PROXY -split ',')
$env:NO_PROXY = (($taskLocalHosts + $taskExistingHosts | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique) -join ',')
[pscustomobject]@{
    Project = $taskRepoRoot
    TemporaryFiles = $env:TEMP
    PipCache = $env:PIP_CACHE_DIR
    UvCache = $env:UV_CACHE_DIR
    ManagedPython = $env:UV_PYTHON_INSTALL_DIR
}
