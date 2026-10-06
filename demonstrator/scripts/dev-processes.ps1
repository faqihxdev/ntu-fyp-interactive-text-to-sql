# Windows process helpers. A port number alone never establishes ownership.
function Find-DemoProcessRoot {
    param([int]$OwnerId, [hashtable]$Processes, [string]$DemoRoot)
    $demoPython = Join-Path $DemoRoot '.venv\Scripts\python.exe'
    $demoVite = Join-Path $DemoRoot 'frontend\node_modules\vite\bin\vite.js'
    $visited = @{}
    while ($Processes.ContainsKey($OwnerId) -and -not $visited.ContainsKey($OwnerId)) {
        $visited[$OwnerId] = $true
        $item = $Processes[$OwnerId]
        $isApi = $item.ExecutablePath -eq $demoPython -and
            $item.CommandLine -match '\s-m\s+uvicorn\s+explorer\.server:create_app(?:\s|$)'
        $isVite = $item.Name -eq 'node.exe' -and
            $item.CommandLine -match ('(?:^|[\s"])' + [regex]::Escape($demoVite) + '(?:[\s"]|$)')
        if ($isApi -or $isVite) { return $item }
        $parent = $Processes[[int]$item.ParentProcessId]
        if (-not $parent -or $parent.CreationDate -gt $item.CreationDate) { break }
        $OwnerId = [int]$item.ParentProcessId
    }
    return $null
}

function Stop-DemoProcessTree {
    param($Root)
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($Root.ProcessId)"
    if (-not $current -or $current.CreationDate -ne $Root.CreationDate) { return }
    $snapshot = @(Get-CimInstance Win32_Process)
    $tree = [System.Collections.Generic.List[object]]::new()
    $tree.Add($current)
    for ($index = 0; $index -lt $tree.Count; $index++) {
        foreach ($child in $snapshot) {
            if ($child.ParentProcessId -eq $tree[$index].ProcessId -and
                $child.CreationDate -ge $tree[$index].CreationDate) {
                $tree.Add($child)
            }
        }
    }
    # Stop the root first so a reloader cannot replace a child during cleanup.
    foreach ($item in $tree) {
        $stillSame = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.ProcessId)"
        if ($stillSame -and $stillSame.CreationDate -eq $item.CreationDate) {
            Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Clear-DemoPorts {
    param([int[]]$Ports, [string]$DemoRoot)
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -in $Ports })
    if (-not $listeners.Count) { return }
    $processes = @{}
    foreach ($item in Get-CimInstance Win32_Process) { $processes[[int]$item.ProcessId] = $item }
    $roots = @{}
    # Inspect all ports before stopping anything.
    foreach ($listener in $listeners) {
        $root = Find-DemoProcessRoot -OwnerId $listener.OwningProcess -Processes $processes -DemoRoot $DemoRoot
        if (-not $root) {
            throw "Port $($listener.LocalPort) belongs to an unrelated or unrecognised process (PID $($listener.OwningProcess)). Nothing was stopped. Close it yourself or choose -Port and -ApiPort."
        }
        $roots[[int]$root.ProcessId] = $root
    }
    foreach ($root in $roots.Values) {
        Write-Host "Stopping previous demo process $($root.ProcessId)."
        Stop-DemoProcessTree -Root $root
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalPort -in $Ports })
        if (-not $remaining.Count) { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "A demo port is still occupied. No new server was started."
}
