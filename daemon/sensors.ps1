# Sidecar de sensores para el daemon del panel.
# Emite una linea JSON por segundo a stdout. SOLO lecturas de hardware.
#   - GSA1 ACPI-WMI (Gigabyte, driverless): temp CPU (id2), temp VRM (id4), VCore (EZV id5)
#   - LibreHardwareMonitor: GPU (load/temp/hotspot/power/clock/fan/vram) y temps de SSD
#   - PDH: % Processor Performance (para clock real de CPU)
$ErrorActionPreference = 'SilentlyContinue'

Add-Type -Path "$PSScriptRoot\LibreHardwareMonitorLib.dll"

$gsa = Get-CimInstance -Namespace root\WMI -ClassName GSA1_ACPIMethod

$comp = New-Object LibreHardwareMonitor.Hardware.Computer
$comp.IsGpuEnabled = $true
$comp.IsStorageEnabled = $true
$comp.Open()

$BASE_MHZ = 2500   # base clock del i5-12400F, para reconstruir el clock real

function Gsa-Temp([byte]$id) {
    (Invoke-CimMethod -InputObject $gsa -MethodName ZFCGetCurrentTemp -Arguments @{ id = $id }).value
}

function Sensor($hw, $type, $name) {
    ($hw.Sensors | Where-Object { $_.SensorType -eq $type -and $_.Name -eq $name } | Select-Object -First 1).Value
}

while ($true) {
    $o = [ordered]@{}

    $o.cpu_temp = Gsa-Temp 2
    $o.vrm_temp = Gsa-Temp 4
    $v = (Invoke-CimMethod -InputObject $gsa -MethodName EZVGetVoltage -Arguments @{ Id = 5 }).Value
    $o.vcore = if ($v -gt 0) { [math]::Round($v / 1000.0, 3) } else { $null }

    $perf = (Get-Counter '\Processor Information(_Total)\% Processor Performance' -MaxSamples 1).CounterSamples[0].CookedValue
    $o.cpu_clock = [int]($BASE_MHZ * $perf / 100.0)

    foreach ($hw in $comp.Hardware) {
        $hw.Update()
        switch ("$($hw.HardwareType)") {
            'GpuAmd' {
                $o.gpu_load    = Sensor $hw 'Load' 'GPU Core'
                $o.gpu_vram    = Sensor $hw 'Load' 'GPU Memory'
                $o.gpu_temp    = Sensor $hw 'Temperature' 'GPU Core'
                $o.gpu_hotspot = Sensor $hw 'Temperature' 'GPU Hot Spot'
                $o.gpu_power   = Sensor $hw 'Power' 'GPU Package'
                $o.gpu_clock   = Sensor $hw 'Clock' 'GPU Core'
                $o.gpu_fan     = Sensor $hw 'Fan' 'GPU Fan'
                $o.gpu_name    = $hw.Name
            }
            'Storage' {
                if (-not $o.Contains('disks')) { $o.disks = @() }
                $o.disks += @{
                    name = $hw.Name
                    temp = Sensor $hw 'Temperature' 'Temperature'
                    used = Sensor $hw 'Load' 'Used Space'
                }
            }
        }
    }

    ($o | ConvertTo-Json -Compress -Depth 4)
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 900
}
