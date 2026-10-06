param([int]$Port = 8765, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
Push-Location $demoRoot
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if (-not $SkipBuild) {
        Push-Location (Join-Path $demoRoot 'frontend')
        try {
            npm ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    Write-Host "Open http://127.0.0.1:$Port . Ctrl+C stops the local server."
    uv run --frozen python -m uvicorn explorer.server:create_app --factory --host 127.0.0.1 --port $Port
} finally { Pop-Location }
