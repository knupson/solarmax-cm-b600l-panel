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
#
# "caps" se recalcula en CADA vuelta del loop, a partir de si esa vuelta trajo
# datos usables. No es un latch fijado al arrancar: si una fuente se cae en
# la mitad de la corrida deja de reportar caps=true (con motivo, del lado
# Python), y si vuelve, se recupera sola. Solo el setup caro (abrir el objeto
# CIM de GSA1, cargar LibreHardwareMonitor y abrir Computer) se hace una sola
# vez arriba; lo que se re-evalua siempre es si LA LECTURA de esta vuelta dio
# un valor utilizable.
$ErrorActionPreference = 'SilentlyContinue'

# --- GSA1 (solo Gigabyte): setup una sola vez ---
$gsa = Get-CimInstance -Namespace root\WMI -ClassName GSA1_ACPIMethod

# --- base clock real de ESTA CPU (antes estaba quemado en 2500) ---
$baseMhz = (Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed
if (-not $baseMhz -or $baseMhz -le 0) { $baseMhz = 0 }
$cpuName = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name

# --- LHM: setup una sola vez ---
$comp = $null
try {
    Add-Type -Path "$PSScriptRoot\lib\LibreHardwareMonitorLib.dll" -ErrorAction Stop
    $comp = New-Object LibreHardwareMonitor.Hardware.Computer
    $comp.IsGpuEnabled = $true
    $comp.IsStorageEnabled = $true
    $comp.Open()
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
    $caps = [ordered]@{ gsa1 = $false; pdh = $false; lhm = $false }

    if ($gsa) {
        $g = [ordered]@{}
        $g.'cpu.temp'     = Gsa-Temp 2
        $g.'cpu.vrm_temp' = Gsa-Temp 4
        $v = (Invoke-CimMethod -InputObject $gsa -MethodName EZVGetVoltage -Arguments @{ Id = 5 }).Value
        $g.'cpu.vcore'    = if ($null -ne $v -and $v -gt 0) { [math]::Round($v / 1000.0, 3) } else { $null }
        # caps.gsa1 refleja ESTA lectura: 0 es un valor real para algunos
        # sensores, asi que se prueba contra $null, no contra falsy.
        if ($null -ne $g.'cpu.temp' -or $null -ne $g.'cpu.vrm_temp' -or $null -ne $g.'cpu.vcore') {
            $caps.gsa1 = $true
        }
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
        $lhmOk = $false
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
                    if ($null -ne $l.'gpu.load' -or $null -ne $l.'gpu.temp') { $lhmOk = $true }
                }
                'Storage' {
                    $t = Sensor $hw 'Temperature' 'Temperature'
                    if ($null -ne $t) { $l."disk.temp.$disk" = $t; $disk++; $lhmOk = $true }
                }
            }
        }
        # caps.lhm refleja si ESTA vuelta realmente saco algun sensor de GPU o
        # disco, no si Computer.Open() funciono al arrancar.
        $caps.lhm = $lhmOk
        $out.lhm = $l
    }

    $out.caps = $caps
    ($out | ConvertTo-Json -Compress -Depth 4)
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 900
}
