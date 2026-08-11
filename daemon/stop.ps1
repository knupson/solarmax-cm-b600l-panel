# Frena el daemon del panel y su sidecar de sensores.
# Usa el pidfile, y además barre huérfanos por linea de comandos (mas confiable que filtrar por StartTime).
$ErrorActionPreference = 'SilentlyContinue'
$here = $PSScriptRoot
$pidFile = Join-Path $here 'panel.pid'

function Kill-Tree([int]$id) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $id" | ForEach-Object {
        Kill-Tree $_.ProcessId
    }
    Stop-Process -Id $id -Force
}

$killed = @()

if (Test-Path $pidFile) {
    $id = [int](Get-Content $pidFile | Select-Object -First 1)
    if (Get-Process -Id $id) { Kill-Tree $id; $killed += "daemon pid $id" }
    Remove-Item $pidFile -Force
}

# barrido: cualquier python corriendo panel.py y cualquier powershell corriendo sensors.ps1
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -match 'panel\.py|sensors\.ps1' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        $killed += "$($_.Name) pid $($_.ProcessId)"
    }

if ($killed.Count) { "frenado: $($killed -join ', ')" } else { 'no habia nada corriendo' }
