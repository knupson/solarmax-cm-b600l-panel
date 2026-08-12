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
| RAM (uso), red | psutil |
| Velocidad de RAM | `Win32_PhysicalMemory.ConfiguredClockSpeed` (MT/s), con `Speed` de respaldo |

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

**Registrado el 2026-08-11**: tarea `PanelVitals` al logon, **RunLevel Highest** (GSA1 y el
SMART de los SSD piden elevación), corriendo el motor nuevo. La tarea vendor
`LCD ControlPowerBoot` quedó deshabilitada.

```powershell
$pyw = 'C:\Users\KnuPwns\AppData\Local\Programs\Python\Python313\pythonw.exe'
$act = New-ScheduledTaskAction -Execute $pyw -Argument '-u -m vmaxpanel --log E:\Claude\Solarmax_Display\vmaxpanel.log' -WorkingDirectory 'E:\Claude\Solarmax_Display'
$trg = New-ScheduledTaskTrigger -AtLogOn -User 'KnuPwns'
$prn = New-ScheduledTaskPrincipal -UserId 'KnuPwns' -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'PanelVitals' -Action $act -Trigger $trg -Principal $prn -Settings $set -Force
Disable-ScheduledTask -TaskName 'LCD ControlPowerBoot'
```

`pythonw.exe` no tiene consola, así que **`--log` no es opcional acá**: sin él, un motor que
muere al logon deja la pantalla negra sin dejar rastro. El log va a `vmaxpanel.log` en la raíz
del repo (gitignored) y se escribe con flush por línea, incluido el traceback de una excepción
que se escape.

Probar la tarea sin reiniciar: `Stop-ScheduledTask PanelVitals` + `Start-ScheduledTask
PanelVitals`, y revisar el log. Ojo que dos instancias se pelean por COM3: matar la manual
antes.

Revertir: `Unregister-ScheduledTask PanelVitals` + `Enable-ScheduledTask 'LCD ControlPowerBoot'`.

Para volver al daemon viejo en el autostart, la versión anterior de esta sección está en el
historial de git (`-Execute pythonw.exe -Argument '-u panel.py --fps 1 --log panel.log'` con
`-WorkingDirectory` en `daemon/`).

Si LCD Control arranca a mano pelea por COM3; el daemon reintenta cada 5s, pero conviene
no tener los dos.

## Editar

Fase 1 (`vmaxpanel/`) reemplaza al `daemon/` original por un motor data-driven: el layout
vive en JSON, no hardcodeado en Python, y se recarga en caliente al guardar. `daemon/` sigue
intacto y es lo que corre hoy en producción — el cutover (autostart) todavía no se hizo, ver
"Estado" en `CLAUDE.md`. Mientras tanto, para probar el motor nuevo sin tocar el panel real:
`python -m vmaxpanel --save preview.png --no-sensors`.

| Qué querés cambiar | Dónde |
|---|---|
| Posición, formato o color de un valor | `vmaxpanel/profiles/vitals.json` — se recarga en caliente al guardar |
| Etiquetas de texto | widgets de tipo `label` en el mismo JSON |
| Líneas separadoras, marcos, bloques de color | widgets de tipo `rect` en el mismo JSON |
| Fondo | el bloque `background` del perfil |
| Qué métricas existen | `vmaxpanel/metrics.py` |
| De dónde sale cada métrica | `vmaxpanel/providers/` |
| Sensores nuevos del sidecar | `vmaxpanel/sensors.ps1` |

El `daemon/panel.py` viejo (sin motor, todo hardcodeado) se sigue editando como antes: ver el
historial de este archivo antes de la fase 1 si hace falta esa tabla.

### El widget `rect`

Cubre divisores, marcos y bloques de color. `fill` y `stroke` son opcionales por separado,
pero al menos uno tiene que estar — un rect sin ninguno de los dos no dibuja nada, así que el
validador lo rechaza en vez de dejarlo invisible.

```json
{ "id": "cpu-rule", "type": "rect", "x": 24, "y": 164, "w": 272, "h": 1, "fill": "#242834" }
{ "id": "marco", "type": "rect", "x": 14, "y": 540, "w": 292, "h": 320,
  "radius": 8, "stroke": "#242834", "stroke_width": 2 }
```

Dos cosas que no se adivinan:

- **`w`/`h` son el tamaño real en píxeles**: `"h": 1` es una línea de 1 px. `bar` y `graph`
  usan la caja inclusive de Pillow y quedan un píxel más grandes que lo escrito (`"h": 16` →
  17 px); no se corrigieron para no mover el perfil ni los goldens, pero un separador no puede
  darse ese lujo. El `radius` se clampea a la mitad del lado menor.
- **El orden de la lista `widgets` es el orden de pintado.** No hay campo `z`: un `rect` con
  `fill` puesto después de un texto lo tapa. Los separadores del perfil van antes del header
  de su sección.

## Dependencias

Python 3.13 + `psutil`, `pyserial`, `pillow`. Los 3 DLL (`LibreHardwareMonitorLib`,
`HidSharp`, `HidLibrary`) están en `daemon/` — LHM necesita HidSharp al lado o `Open()`
falla. `frida-tools` se usó solo para reversear el protocolo; el daemon no la necesita.
