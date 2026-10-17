$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $demoRoot 'scripts\dev-processes.ps1')
$created = [DateTime]'2026-09-01T12:00:00'
function FixtureProcess($Id, $Parent, $Name, $Executable, $Command, $Date = $created) {
    [pscustomobject]@{ ProcessId = $Id; ParentProcessId = $Parent; Name = $Name; ExecutablePath = $Executable; CommandLine = $Command; CreationDate = $Date }
}
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
$python = Join-Path $demoRoot '.venv\Scripts\python.exe'
$vite = Join-Path $demoRoot 'frontend\node_modules\vite\bin\vite.js'
$processes = @{
    100 = FixtureProcess 100 1 'python.exe' $python "`"$python`" -m uvicorn explorer.server:create_app --factory"
    101 = FixtureProcess 101 100 'python.exe' 'C:\runtime\python.exe' 'python -c multiprocessing.spawn'
    200 = FixtureProcess 200 1 'node.exe' 'C:\runtime\node.exe' "node `"$vite`" --port 8765"
    300 = FixtureProcess 300 1 'python.exe' 'C:\other\python.exe' 'python -m uvicorn explorer.server:create_app --factory'
    400 = FixtureProcess 400 1 'node.exe' 'C:\runtime\node.exe' "node `"$vite`" --port 8765" $created.AddMinutes(1)
    401 = FixtureProcess 401 400 'python.exe' 'C:\other\python.exe' 'python something_else.py'
    500 = FixtureProcess 500 1 'node.exe' 'C:\runtime\node.exe' "node `"$vite.bak`""
}
Assert ((Find-DemoProcessRoot 101 $processes $demoRoot).ProcessId -eq 100) 'Python child should resolve to this demo root.'
Assert ((Find-DemoProcessRoot 200 $processes $demoRoot).ProcessId -eq 200) 'Absolute Vite path should identify this demo.'
Assert ($null -eq (Find-DemoProcessRoot 300 $processes $demoRoot)) 'Another copy of the API must not be killed.'
Assert ($null -eq (Find-DemoProcessRoot 401 $processes $demoRoot)) 'A reused parent PID must not imply ownership.'
Assert ($null -eq (Find-DemoProcessRoot 500 $processes $demoRoot)) 'A partial Vite path must not match.'
Assert ($null -eq (Find-DemoProcessRoot 999 $processes $demoRoot)) 'Unknown PID must not imply ownership.'

# One recognised port plus one unrelated port must stop neither process.
$script:stopCalls = 0
function Get-NetTCPConnection {
    @(
        [pscustomobject]@{ LocalPort = 8765; OwningProcess = 101 },
        [pscustomobject]@{ LocalPort = 8766; OwningProcess = 300 }
    )
}
function Get-CimInstance { $processes.Values }
function Stop-DemoProcessTree { $script:stopCalls++ }
$blocked = $false
try { Clear-DemoPorts -Ports @(8765, 8766) -DemoRoot $demoRoot }
catch { $blocked = $_.Exception.Message -match 'unrelated or unrecognised process' }
Assert $blocked 'An unrelated listener must block startup.'
Assert ($script:stopCalls -eq 0) 'Preflight must inspect both ports before stopping anything.'
Write-Host 'Eight dev-process safety checks passed. No real processes were stopped.'

# Windows locks Vite's native modules. Stop the owned servers before npm ci.
$launcher = Get-Content -LiteralPath (Join-Path (Split-Path $demoRoot -Parent) 'run-demo.dev.ps1') -Raw
$startup = $launcher.Substring($launcher.IndexOf('$localRoot ='))
Assert ($startup.IndexOf('Clear-DemoPorts -Ports') -lt $startup.IndexOf('            npm ci')) 'Stop owned dev servers before replacing installed native modules.'
Write-Host 'Launcher dependency-order check passed.'
