# Sidecar de sensores para VMax Panel.
# Emite una linea JSON por segundo a stdout, con ids canonicos de metrica ya
# namespaceados por provider, mas un bloque "caps" con lo que funciono aca.
#
#   gsa1    Gigabyte GSA1 ACPI-WMI (driverless): temp CPU (id2), temp VRM (id4), VCore (EZV id5)
#   pdh     % Processor Performance x base clock -> clock real de CPU
#   lhm     LibreHardwareMonitor: GPU y temps de SSD por SMART
#   cpulhm  LibreHardwareMonitor CPU: package power y por-nucleo (temp/clock/carga)
#   mobo    SuperIO de la placa: fans y temperaturas
#   smbios  Win32_PhysicalMemory: velocidad real de la RAM (estaba horneada en el perfil)
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

# --- velocidad de la RAM: una sola vez, SMBIOS no cambia mientras Windows corre ---
# ConfiguredClockSpeed es la velocidad a la que esta corriendo; Speed es la del
# SPD. Se prefiere la configurada y se cae a la otra si la placa no la reporta.
# El modelo de la placa: una sola vez, tampoco cambia. Estaba horneado como
# etiqueta en el perfil Apex, asi que en cualquier otra maquina el panel mostraba
# con total seguridad una placa que no era la instalada.
$boardName = (Get-CimInstance Win32_BaseBoard | Select-Object -First 1).Product
if ($boardName) { $boardName = "$boardName".Trim().ToUpper() } else { $boardName = "" }

$mem = Get-CimInstance Win32_PhysicalMemory | Select-Object -First 1
$memSpeed = $mem.ConfiguredClockSpeed
if (-not $memSpeed -or $memSpeed -le 0) { $memSpeed = $mem.Speed }
if (-not $memSpeed -or $memSpeed -le 0) { $memSpeed = 0 }

# --- VRAM total por adaptador: una sola vez, no cambia ---
# Hace falta porque en AMD LibreHardwareMonitor NO expone el total, solo cuanta
# VRAM hay usada (SmallData 'D3D Dedicated Memory Used', en MB). Sin el total no
# hay porcentaje.
# La fuente NO es Win32_VideoController.AdapterRAM: ese campo es un uint32 y se
# desborda arriba de 4 GB, asi que en una placa de 16 GB miente. qwMemorySize es
# de 64 bits y lo escribe el propio driver.
$vramTotalMB = @{}
$claseDisplay = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
foreach ($k in (Get-ChildItem $claseDisplay)) {
    $p = Get-ItemProperty $k.PSPath
    $q = $p.'HardwareInformation.qwMemorySize'
    if ($q -and $p.DriverDesc) { $vramTotalMB[[string]$p.DriverDesc] = [math]::Round($q / 1MB) }
}

# --- LHM: setup una sola vez ---
$comp = $null
try {
    Add-Type -Path "$PSScriptRoot\lib\LibreHardwareMonitorLib.dll" -ErrorAction Stop
    $comp = New-Object LibreHardwareMonitor.Hardware.Computer
    $comp.IsGpuEnabled = $true
    $comp.IsStorageEnabled = $true
    # CPU y placa: estaban APAGADOS, y por eso el proyecto tenia documentado
    # que package power y fan de CPU "no se pueden leer". Se pueden: esta
    # version de LibreHardwareMonitor (0.9.3.0) lee RAPL sin cargar ningun
    # driver ring0 -- verificado con la lista de servicios abierta, no hay
    # WinRing0 -- y los fans salen del SuperIO ITE IT8689E.
    $comp.IsCpuEnabled = $true
    $comp.IsMotherboardEnabled = $true
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
    $caps = [ordered]@{ gsa1 = $false; pdh = $false; lhm = $false; smbios = $false;
                        cpulhm = $false; mobo = $false }

    # El unico cap que no se re-evalua contra una lectura nueva: SMBIOS se leyo
    # arriba y no cambia hasta el proximo arranque. Se emite igual en cada
    # vuelta para que el gate de frescura del lado Python lo cubra como al
    # resto -- si el sidecar muere, el valor deja de servirse en vez de quedar
    # congelado en pantalla.
    if ($memSpeed -gt 0 -or $boardName) {
        $caps.smbios = $true
        $sm = [ordered]@{}
        if ($memSpeed -gt 0) { $sm.'mem.speed' = [int]$memSpeed }
        if ($boardName)      { $sm.'mb.name'   = $boardName }
        $out.smbios = $sm
    }

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
        $cpuh = [ordered]@{}
        $mobo = [ordered]@{}
        $disk = 0
        $lhmOk = $false
        foreach ($hw in $comp.Hardware) {
            $hw.Update()
            switch -Wildcard ("$($hw.HardwareType)") {
                'Cpu' {
                    # Package power: el dato que estaba dado por imposible.
                    $cpuh.'cpu.power' = Sensor $hw 'Power' 'CPU Package'
                    # Por nucleo, con la numeracion 1-based de LHM para que
                    # coincida con lo que muestran las otras herramientas.
                    foreach ($s in $hw.Sensors) {
                        $n = $null
                        if ($s.Name -match '^CPU Core #(\d+)$') { $n = $Matches[1] }
                        if ($null -eq $n -or $null -eq $s.Value) { continue }
                        switch ("$($s.SensorType)") {
                            'Temperature' { $cpuh."core.$n.temp"  = [math]::Round($s.Value, 1) }
                            'Clock'       { $cpuh."core.$n.clock" = [math]::Round($s.Value) }
                        }
                    }
                    # La carga viene por THREAD ("CPU Core #3 Thread #2"): la
                    # del nucleo es el promedio de sus threads, que es lo que
                    # significa "cuanto se esta usando ese nucleo".
                    $porNucleo = @{}
                    foreach ($s in $hw.Sensors) {
                        if ("$($s.SensorType)" -ne 'Load') { continue }
                        if ($s.Name -notmatch '^CPU Core #(\d+) Thread #\d+$') { continue }
                        if ($null -eq $s.Value) { continue }
                        $k = $Matches[1]
                        if (-not $porNucleo.ContainsKey($k)) { $porNucleo[$k] = @() }
                        $porNucleo[$k] += [double]$s.Value
                    }
                    foreach ($k in $porNucleo.Keys) {
                        $cpuh."core.$k.load" = [math]::Round(($porNucleo[$k] | Measure-Object -Average).Average, 1)
                    }
                }
                'Gpu*' {
                    $l.'gpu.name'    = $hw.Name
                    $l.'gpu.load'    = Sensor $hw 'Load' 'GPU Core'
                    # 'Load' 'GPU Memory' NO es la VRAM ocupada: en AMD es la
                    # carga del BUS de memoria. Daba 1% con 1,5 GB de 16 GB en
                    # uso, o sea un 9% real, y la metrica se llama "VRAM usada".
                    # NVIDIA e Intel sirven usado y total directo; AMD solo el
                    # usado, y el total sale del registro (ver $vramTotalMB).
                    $vramUsadaMB = Sensor $hw 'SmallData' 'GPU Memory Used'
                    $vramTotal   = Sensor $hw 'SmallData' 'GPU Memory Total'
                    if ($null -eq $vramUsadaMB) {
                        $vramUsadaMB = Sensor $hw 'SmallData' 'D3D Dedicated Memory Used'
                    }
                    if ($null -eq $vramTotal) { $vramTotal = $vramTotalMB[[string]$hw.Name] }
                    # Sin total no se publica nada: la metrica queda no
                    # disponible y el panel lo dice, que es mejor que un numero
                    # inventado con un total adivinado.
                    if ($null -ne $vramUsadaMB -and $vramTotal -gt 0) {
                        $l.'gpu.vram' = [math]::Round(100 * $vramUsadaMB / $vramTotal, 1)
                    }
                    $l.'gpu.temp'    = Sensor $hw 'Temperature' 'GPU Core'
                    $l.'gpu.hotspot' = Sensor $hw 'Temperature' 'GPU Hot Spot'
                    $l.'gpu.power'   = Sensor $hw 'Power' 'GPU Package'
                    $l.'gpu.clock'   = Sensor $hw 'Clock' 'GPU Core'
                    $l.'gpu.fan'     = Sensor $hw 'Fan' 'GPU Fan'
                    if ($null -ne $l.'gpu.load' -or $null -ne $l.'gpu.temp') { $lhmOk = $true }
                }
                'Storage' {
                    $t = Sensor $hw 'Temperature' 'Temperature'
                    # El indice sale de la POSICION del disco en la enumeracion,
                    # no de cuantos contestaron. Antes se incrementaba solo
                    # cuando habia lectura, asi que un SSD que falla una vuelta
                    # CORRIA el indice de todos los que venian despues: los tres
                    # numeros del panel cambiaban de significado entre muestras
                    # sin que nada avisara. La clave se emite siempre, con null
                    # si esta vuelta no hubo temperatura, para que el conjunto de
                    # ids sea estable -- LhmProvider.served los descubre de la
                    # primera muestra y un disco ausente ahi no volvia a
                    # aparecer nunca.
                    $l."disk.temp.$disk" = $t
                    if ($null -ne $t) { $lhmOk = $true }
                    $disk++
                }
                'Motherboard' {
                    # Los sensores de la placa viven en el SuperIO, que cuelga
                    # como SubHardware: el Motherboard en si reporta 0.
                    $tmp = 0
                    foreach ($sub in $hw.SubHardware) {
                        $sub.Update()
                        foreach ($s in $sub.Sensors) {
                            if ($null -eq $s.Value) { continue }
                            switch ("$($s.SensorType)") {
                                'Fan' {
                                    if ($s.Name -match '#(\d+)$') {
                                        $mobo."fan.$($Matches[1]).rpm" = [math]::Round($s.Value)
                                    }
                                }
                                'Temperature' {
                                    $mobo."mb.temp.$tmp" = [math]::Round($s.Value, 1)
                                    $tmp++
                                }
                            }
                        }
                    }
                    # CPU_FAN es el primer conector en las placas Gigabyte, y en
                    # esta maquina es el unico que gira con el equipo encendido
                    # (los otros tres dan 0, sin nada conectado). Se expone
                    # ademas como cpu.fan; los fan.N.rpm quedan disponibles por
                    # si en otra placa el orden es distinto.
                    if ($mobo.Contains('fan.1.rpm')) { $mobo.'cpu.fan' = $mobo.'fan.1.rpm' }
                }
            }
        }
        # caps.lhm refleja si ESTA vuelta realmente saco algun sensor de GPU o
        # disco, no si Computer.Open() funciono al arrancar.
        $caps.lhm = $lhmOk
        $out.lhm = $l
        # Cada namespace nuevo reporta su propia capacidad: que la GPU responda
        # no dice nada sobre si el SuperIO de la placa esta accesible.
        if ($cpuh.Count -gt 0) { $caps.cpulhm = $true; $out.cpulhm = $cpuh }
        if ($mobo.Count -gt 0) { $caps.mobo = $true;   $out.mobo = $mobo }
    }

    $out.caps = $caps
    ($out | ConvertTo-Json -Compress -Depth 4)
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 900
}
