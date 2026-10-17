param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [ValidateRange(1024, 65535)][int]$ApiPort = 8766,
    [switch]$Stop,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
if ($Help) {
    Write-Host 'Usage: .\run-demo.dev.ps1 [-Port 8765] [-ApiPort 8766] [-Stop]'
    Write-Host 'Starts the local demo. Requires Windows, Node.js and uv.'
    return
}
if ($Port -eq $ApiPort) { throw 'Choose different frontend and API ports.' }
$demoRoot = Join-Path $PSScriptRoot 'demonstrator'
$frontendRoot = Join-Path $demoRoot 'frontend'
. (Join-Path $demoRoot 'scripts\dev-processes.ps1')

if ($Stop) {
    Clear-DemoPorts -Ports @($Port, $ApiPort) -DemoRoot $demoRoot
    Write-Host 'Demo processes stopped.'
    return
}

$localRoot = Join-Path $demoRoot '.local'
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
# Release Vite's native-module file handles before npm ci can replace them.
Clear-DemoPorts -Ports @($Port, $ApiPort) -DemoRoot $demoRoot
Push-Location $demoRoot
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Frozen Python dependency installation failed.' }
    $lockHash = (Get-FileHash -LiteralPath (Join-Path $frontendRoot 'package-lock.json') -Algorithm SHA256).Hash
    $stamp = Join-Path $localRoot 'frontend-lock.sha256'
    $installedHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
    $vite = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
    if ($installedHash -ne $lockHash -or -not (Test-Path -LiteralPath $vite)) {
        Push-Location $frontendRoot
        try {
            npm ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            $lockHash | Set-Content -LiteralPath $stamp -Encoding ascii
        } finally { Pop-Location }
    }
} finally { Pop-Location }

$python = Join-Path $demoRoot '.venv\Scripts\python.exe'
$node = (Get-Command node.exe -ErrorAction Stop).Source
Clear-DemoPorts -Ports @($Port, $ApiPort) -DemoRoot $demoRoot
$apiRootProcess = $null
$previousApiPort = $env:DEMO_API_PORT
$logTag = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$outLog = Join-Path $localRoot "dev-$logTag-api.out.log"
$errLog = Join-Path $localRoot "dev-$logTag-api.err.log"
try {
    $env:DEMO_API_PORT = [string]$ApiPort
    $apiProcess = Start-Process -FilePath $python -WorkingDirectory $demoRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @('-m', 'uvicorn', 'explorer.server:create_app', '--factory', '--host', '127.0.0.1', '--port', [string]$ApiPort, '--reload', '--reload-dir', 'explorer') `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $apiRootProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($apiProcess.Id)"
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $apiProcess.Refresh()
        if ($apiProcess.HasExited) { throw "API startup failed. See $errLog" }
        $listener = @(Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue)
        if ($listener.Count) { break }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $listener.Count) { throw "API startup timed out. See $errLog" }
    Write-Host "Open http://127.0.0.1:$Port/ . Ctrl+C stops the demo."
    Push-Location $frontendRoot
    try {
        & $node $vite --host 127.0.0.1 --port $Port --strictPort
        if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE." }
    } finally { Pop-Location }
} finally {
    $env:DEMO_API_PORT = $previousApiPort
    if ($null -ne $apiRootProcess) { Stop-DemoProcessTree -Root $apiRootProcess }
}
