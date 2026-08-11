# Solarmax Display — driver propio del panel del gabinete

Reemplazo de **LCD Control** (software vendor) para el panel **Solarmax CM-B600L**,
320x1480, `HL-VMAX-USB-Device` (VID_33C3 / PID_F101) en **COM3**.
SN del panel: `VMAXA170320*1480S261001155`.

Escrito el 2026-08-11 porque la app vendor mostraba **CPU 100%** con carga real de 65%.

## Por qué existe

`CpuUsage` y `CpuUse`, los únicos tokens de CPU de LCD Control, son
**`% Processor Utility` de PDH**: carga × (clock actual / clock base). En el i5-12400F
(base 2500 MHz, all-core ~4080) el factor es **~1,63**, así que el valor pasa de 100 y el
panel lo **clampea en 100 con cualquier carga real ≥ ~61%**.

Verificado por dos lados en el mismo segundo:

| Fuente | Valor |
|---|---|
| stdout interno de la app | `Processor 110,7` |
| mi contador PDH `% Processor Utility` | `110,5` |
| carga real (`% Processor Time`) | `69,2` |

Los 34 sensores de la app son todos contadores PDH; incluso su per-thread pasa de 100
(`CPU #9 = 107,2`). No hay ningún token con la carga time-based: probé ~38 nombres.

## Protocolo del panel

Reverseado con frida hookeando `WriteFile` en el proceso de la app.

```
open \\.\COM3                       (CDC; el baud es irrelevante)
TX  F0 A5 5A 0F                     handshake
RX  "VMAXA170320*1480S261001155"    SN, 26 bytes ASCII
TX  AA BB <brillo 0..100> CC DD
TX  <JPEG>                          un WriteFile por frame, ~1 fps
```

El frame es un **JPEG crudo 320x1480 baseline 4:2:0**, sin header ni framing: arranca en
`FFD8FF` y termina en `FFD9`. El panel está montado al revés en el gabinete: se manda
**rotado 180°** (`--rotate`).

## Sensores

El driver ring0 de LibreHardwareMonitor (**WinRing0**) está **bloqueado** en esta máquina:
`StartService → 0xE1` (`ERROR_VIRUS_INFECTED`, está en la blocklist de drivers vulnerables
de Windows). Sin MSR. Todo lo de acá abajo es **driverless y read-only**.

| Dato | Fuente |
|---|---|
| Carga CPU (real) | PDH `% Processor Time` vía psutil |
| Clock CPU | PDH `% Processor Performance` × 2500 |
| Temp CPU | Gigabyte GSA1 ACPI-WMI, `ZFCGetCurrentTemp(id=2)` |
| Temp VRM | idem, `id=4` |
| VCore | idem, `EZVGetVoltage(Id=5)` (mV) |
| GPU: load, VRAM, temp, hot spot, power, clock, fan | LibreHardwareMonitor (ADL) |
| Temp de los 3 SSD | LibreHardwareMonitor (NVMe SMART) |
| RAM, red | psutil |

**GSA1** = `root\WMI`, clase `GSA1_ACPIMethod`, instancia `ACPI\PNP0C14\GSADEV0_0`.
Identifiqué los ids por correlación con carga: id2 subió +10 °C y id4 +9 °C bajo 100% de
CPU; 0/1/3/5 no se movieron. `ECLReadByte/Word` **no está implementado** en la B760M D3HP
("Objeto no válido"). `PIORead8` sí funciona.

> **Cuidado:** GSA1 también expone `PIOWrite*`, `MEMWrite*`, `PCIWrite*` — escritura
> arbitraria a puertos, memoria física y espacio PCI. El daemon usa **solo métodos de
> lectura**. No agregar escrituras sin entender exactamente el registro destino.

### Lo que no se puede

- **Package power (W)**: necesita MSR RAPL → driver ring0.
- **Fan RPM de CPU**: necesita escrituras al índice del SuperIO (IT87xx).

Esos dos slots del layout se reetiquetaron a **VCORE** y **VRM** en `assets/back.png`.

## Estructura

```
daemon/       panel.py, sensors.ps1, start.ps1, stop.ps1, assets/, DLLs, panel.log
research/     herramientas de reversing y evidencia (sniffers frida, sondas, capturas, CSVs)
docs/memory/  copia de las memorias del proyecto
transcript/   transcript de la sesión donde se construyó esto
```

## Operación

```powershell
cd E:\Claude\Solarmax_Display\daemon
.\start.ps1                 # idempotente; -Force reinicia
.\stop.ps1                  # mata daemon + sidecar, y barre huérfanos
.\start.ps1 -Fps 2 -Brightness 60 -Rotate 180
python panel.py --save preview.png    # previsualizar sin tocar el panel
```

`start.ps1` escribe `panel.pid`. `stop.ps1` no confía solo en el pidfile: también barre por
línea de comandos, porque los sidecars huérfanos se quedan con el DLL de LHM tomado.

### Autostart

Tarea `PanelVitals` al logon, **RunLevel Highest** (GSA1 y el SMART de los SSD piden
elevación). Se deshabilita la tarea vendor `LCD ControlPowerBoot`.

```powershell
$py  = (Get-Command python).Source -replace 'python\.exe$','pythonw.exe'
$act = New-ScheduledTaskAction -Execute $py -Argument '-u panel.py --fps 1 --log panel.log' -WorkingDirectory 'E:\Claude\Solarmax_Display\daemon'
$trg = New-ScheduledTaskTrigger -AtLogOn -User 'KnuPwns'
$prn = New-ScheduledTaskPrincipal -UserId 'KnuPwns' -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'PanelVitals' -Action $act -Trigger $trg -Principal $prn -Settings $set -Force
Disable-ScheduledTask -TaskName 'LCD ControlPowerBoot'
```

Revertir: `Unregister-ScheduledTask PanelVitals` + `Enable-ScheduledTask 'LCD ControlPowerBoot'`.

Si LCD Control arranca a mano pelea por COM3; el daemon reintenta cada 5s, pero conviene
no tener los dos.

## Editar

No hay exe, no hay build. Se edita y se reinicia.

| Qué querés cambiar | Dónde |
|---|---|
| Posición/formato de un valor | `Renderer.frame()` en `panel.py` — una línea por elemento |
| Qué dato va en cada slot | `collect()` en `panel.py` |
| Colores | constantes `WHITE`/`BLUE`/`GRAY`/`BAR_FILL`/`BAR_TRACK` |
| Formato de red | `human_rate()` |
| Fecha en español | `DIAS` / `MESES` |
| Diseño del fondo, etiquetas | `assets/back.png` (320x1480) |
| Sensores | `sensors.ps1` |

Las coordenadas son las mismas del `Setting.txt` del tema Vitals original, así que mover un
elemento es cambiar dos números.

## Dependencias

Python 3.13 + `psutil`, `pyserial`, `pillow`. Los 3 DLL (`LibreHardwareMonitorLib`,
`HidSharp`, `HidLibrary`) están en `daemon/` — LHM necesita HidSharp al lado o `Open()`
falla. `frida-tools` se usó solo para reversear el protocolo; el daemon no la necesita.
