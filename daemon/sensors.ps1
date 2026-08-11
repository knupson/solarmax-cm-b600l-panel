# Sidecar de sensores para VMax Panel.
# Emite una linea JSON por segundo a stdout, con ids canonicos de metrica ya
# namespaceados por provider, mas un bloque "caps" con lo que funciono aca.
#
#   gsa1  Gigabyte GSA1 ACPI-WMI (driverless): temp CPU (id2), temp VRM (id4), VCore (EZV id5)
#   pdh   % Processor Performance x base clock -> clock real de CPU
#   lhm   LibreHardwareMonitor: GPU y temps de SSD por SMART
#
# SOLO lecturas. GSA1 tambien expone PIOWrite/MEMWrite/PCIWrite (escritura
# arbitraria a puertos, memoria fisica y espacio PCI): no se invocan.
$ErrorActionPreference = 'SilentlyContinue'

$caps = [ordered]@{ gsa1 = $false; pdh = $false; lhm = $false }

# --- GSA1 (solo Gigabyte) ---
$gsa = Get-CimInstance -Namespace root\WMI -ClassName GSA1_ACPIMethod
if ($gsa) {
    $probe = (Invoke-CimMethod -InputObject $gsa -MethodName ZFCGetCurrentTemp -Arguments @{ id = [byte]2 }).value
    if ($null -ne $probe -and $probe -gt 0) { $caps.gsa1 = $true }
}

# --- base clock real de ESTA CPU (antes estaba quemado en 2500) ---
$baseMhz = (Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed
if (-not $baseMhz -or $baseMhz -le 0) { $baseMhz = 0 }
$cpuName = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name

# --- LHM ---
$comp = $null
try {
    Add-Type -Path "$PSScriptRoot\LibreHardwareMonitorLib.dll" -ErrorAction Stop
    $comp = New-Object LibreHardwareMonitor.Hardware.Computer
    $comp.IsGpuEnabled = $true
    $comp.IsStorageEnabled = $true
    $comp.Open()
    if ($comp.Hardware.Count -gt 0) { $caps.lhm = $true }
} catch { $comp = $null }

function Gsa-Temp([byte]$id) {
    (Invoke-CimMethod -InputObject $gsa -MethodName ZFCGetCurrentTemp -Arguments @{ id = $id }).value
}

function Sensor($hw, $type, $name) {
    ($hw.Sensors | Where-Object { $_.SensorType -eq $type -and $_.Name -eq $name } |
        Select-Object -First 1).Value
}

while ($true) {
    $out = [ordered]@{}

    if ($caps.gsa1) {
        $g = [ordered]@{}
        $g.'cpu.temp'     = Gsa-Temp 2
        $g.'cpu.vrm_temp' = Gsa-Temp 4
        $v = (Invoke-CimMethod -InputObject $gsa -MethodName EZVGetVoltage -Arguments @{ Id = 5 }).Value
        $g.'cpu.vcore'    = if ($v -gt 0) { [math]::Round($v / 1000.0, 3) } else { $null }
        $out.gsa1 = $g
    }

    if ($baseMhz -gt 0) {
        $perf = (Get-Counter '\Processor Information(_Total)\% Processor Performance' -MaxSamples 1).CounterSamples[0].CookedValue
        if ($null -ne $perf) {
            $caps.pdh = $true
            $out.pdh = [ordered]@{ 'cpu.clock' = [int]($baseMhz * $perf / 100.0); 'cpu.name' = $cpuName }
        }
    }

    if ($comp) {
        $l = [ordered]@{}
        $disk = 0
        foreach ($hw in $comp.Hardware) {
            $hw.Update()
            switch -Wildcard ("$($hw.HardwareType)") {
                'Gpu*' {
                    $l.'gpu.name'    = $hw.Name
                    $l.'gpu.load'    = Sensor $hw 'Load' 'GPU Core'
                    $l.'gpu.vram'    = Sensor $hw 'Load' 'GPU Memory'
                    $l.'gpu.temp'    = Sensor $hw 'Temperature' 'GPU Core'
                    $l.'gpu.hotspot' = Sensor $hw 'Temperature' 'GPU Hot Spot'
                    $l.'gpu.power'   = Sensor $hw 'Power' 'GPU Package'
                    $l.'gpu.clock'   = Sensor $hw 'Clock' 'GPU Core'
                    $l.'gpu.fan'     = Sensor $hw 'Fan' 'GPU Fan'
                }
                'Storage' {
                    $t = Sensor $hw 'Temperature' 'Temperature'
                    if ($null -ne $t) { $l."disk.temp.$disk" = $t; $disk++ }
                }
            }
        }
        $out.lhm = $l
    }

    $out.caps = $caps
    ($out | ConvertTo-Json -Compress -Depth 4)
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 900
}
