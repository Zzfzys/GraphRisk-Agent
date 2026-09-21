$ErrorActionPreference = "Continue"

Write-Output "GraphRisk-Agent environment diagnostic"
Write-Output "======================================"

Write-Output "`nRepository"
Write-Output ("Path: " + (Get-Location).Path)
if (Get-Command git -ErrorAction SilentlyContinue) {
    git --version
    git status --short --branch 2>$null
} else {
    Write-Output "Git: unavailable"
}

Write-Output "`nDisk"
Get-PSDrive -Name E -ErrorAction SilentlyContinue |
    Select-Object Name,
        @{Name = "FreeGB"; Expression = { [math]::Round($_.Free / 1GB, 2) } },
        @{Name = "UsedGB"; Expression = { [math]::Round($_.Used / 1GB, 2) } } |
    Format-Table -AutoSize

Write-Output "`nPython and uv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv --version
} else {
    Write-Output "uv: unavailable"
}

if (Test-Path -LiteralPath ".venv\Scripts\python.exe") {
    .venv\Scripts\python.exe --version
} else {
    Write-Output "Project Python: unavailable"
}

Write-Output "`nGPU"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
} else {
    Write-Output "NVIDIA GPU diagnostic: unavailable"
}

Write-Output "`nOptional infrastructure"
foreach ($commandName in @("docker", "psql", "neo4j", "cypher-shell")) {
    if (Get-Command $commandName -ErrorAction SilentlyContinue) {
        Write-Output ("{0}: available" -f $commandName)
    } else {
        Write-Output ("{0}: unavailable" -f $commandName)
    }
}
