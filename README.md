# Solarmax Display — driver propio del panel del gabinete

Reemplazo de **LCD Control** (software vendor) para el panel **Solarmax CM-B600L**,
320x1480, `HL-VMAX-USB-Device` (VID_33C3 / PID_F101) en **COM3**.
SN del panel: `VMAXA170320*1480S261001155`.

Escrito el 2026-08-11 porque la app vendor mostraba **CPU 100%** con carga real de 65%.

## Empezar de cero

En una máquina nueva, en este orden:

```powershell
git clone <este repo> && cd Solarmax_Display
pip install -r requirements.txt
python -m vmaxpanel --diagnostico            # dice qué falta y qué es opcional
python -m vmaxpanel --save preview.png       # un PNG, sin tocar el panel: prueba que renderiza
python -m vmaxpanel --profile vmaxpanel\profiles\apex.json --instalar   # consola de administrador
```

**Verificado clonando el repo limpio** (2026-08-12): las 575 pruebas pasan y el panel se
dibuja completo *sin* las DLL de sensores — reloj, carga de CPU, temperatura y VCORE (esos
salen de GSA1, no de las DLL), RAM, discos con tamaños reales, uptime y procesos. Lo que
falta sin ellas es GPU, temperatura por núcleo, potencia del paquete, temperatura de discos
y RPM de fans; el diagnóstico lo marca **opcional** y dice de dónde bajarlas. Es el único
paso que no se puede automatizar: son de terceros y este repo no las redistribuye.

`--instalar` necesita **consola de administrador** porque la tarea corre elevada (sin
elevación no hay GSA1 ni SMART).

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
vmaxpanel/    motor data-driven: app.py (supervisor), tray.py (bandeja), editor.py,
              engine.py, cli.py, layout/, render/, providers/, transport/, profiles/
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

### La app de bandeja

```powershell
python -m vmaxpanel.tray --log vmaxpanel.log   # ícono + menú, supervisa el motor
python -m vmaxpanel.editor                     # editor de layout (lo abre la bandeja)
```

El menú de la bandeja tiene el estado (conexión, frames, último error, métricas sin datos),
pausar/reanudar, reiniciar el motor, abrir el editor, abrir el JSON y ver el log. **Pausar
suelta COM3**, así que es la forma de prestarle el panel a LCD Control sin cerrar nada.

El editor guarda con escritura atómica y el motor levanta el cambio en caliente: no hay
comunicación entre los dos procesos, **el archivo es el protocolo**. El editor nunca guarda un
layout inválido, porque el motor lo rechazaría y el usuario habría "guardado" algo que el panel
ignora.

Detalle de diseño en `docs/superpowers/specs/2026-08-12-vmax-panel-fase3-design.md`, incluido
**por qué no hay un servicio de Windows**: corre en la sesión 0 y desde ahí no se puede mostrar
ni un ícono ni una ventana.

### Instalación y autostart

```powershell
python -m vmaxpanel --diagnostico                          # revisa y sale, no toca nada
python -m vmaxpanel --profile <perfil> --instalar          # revisa y registra la tarea al logon
python -m vmaxpanel --desinstalar                          # borra la tarea
```

`--diagnostico` es lo que hay que correr cuando "no anda": dependencias (con el nombre de pip,
no el de import — "falta PIL" manda a buscar un paquete que no existe), la DLL de sensores, el
perfil y el panel. Tres estados y no dos: **ok**, **FALTA** (impide funcionar, y bloquea
`--instalar`) y **opcional** — sin ffmpeg no hay fondos de video y nada más; el panel
desenchufado no es un problema porque la bandeja reintenta sola. Un "Acceso denegado" del
puerto se traduce a *está en uso*: pyserial envuelve el `PermissionError` en `SerialException`
y leído crudo manda a pelear con el UAC en vez de a cerrar LCD Control.

`--instalar` registra `PanelVitals` **por XML**, no con `/SC ONLOGON`, porque los defaults de
schtasks no arrancan la tarea a batería, la matan al desenchufar y la cortan a las 72 horas: en
una notebook eso es el panel apagándose solo. **`RunLevel HighestAvailable`** — GSA1 y el SMART
de los SSD piden elevación —, y por eso registrarla necesita una consola de administrador. Con
`/F`: reinstalar reemplaza, así que correrlo dos veces deja una sola tarea. El XML va en UTF-16
porque schtasks rechaza UTF-8 con "The task XML is malformed" sin decir que el problema es la
codificación.

**Registrado el 2026-08-11** a mano, apuntado a la bandeja el 2026-08-12, y regenerado con
`--instalar` el mismo día. La tarea vendor `LCD ControlPowerBoot` quedó deshabilitada
(`Disable-ScheduledTask -TaskName 'LCD ControlPowerBoot'`); revertir eso es
`Enable-ScheduledTask`.

`pythonw.exe` no tiene consola, así que **`--log` no es opcional acá**: sin él, un motor que
muere al logon deja la pantalla negra sin dejar rastro. El log va a `vmaxpanel.log` en la raíz
del repo (gitignored) y se escribe con flush por línea, incluido el traceback de una excepción
que se escape. Se ganó el sueldo en la primera corrida de la bandeja: cazó un `OverflowError`
dentro del callback de Win32 (`DefWindowProcW` sin `argtypes`, con un `LPARAM` de 64 bits) que
Python se come como "Exception ignored" y que dejaba la ventana sin responder mensajes sin que
nada fallara a la vista.

Probar la tarea sin reiniciar: `Stop-ScheduledTask PanelVitals` + `Start-ScheduledTask
PanelVitals`, y revisar el log. Ojo que dos instancias se pelean por COM3: matar la manual
antes.

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

### Bajarlo de verdad

```powershell
python -m vmaxpanel --parar
```

Tres cosas, porque hacen falta las tres: detiene la tarea (si no, vuelve al siguiente
logon), mata la bandeja y el motor, y mata el **sidecar de sensores** — un `powershell.exe`
corriendo `sensors.ps1` que sobrevive se queda con `LibreHardwareMonitorLib.dll` tomado y
bloquea mover o borrar el directorio. Es la trampa recurrente de este proyecto.

Reconoce sus procesos **por línea de comandos, no por nombre de imagen**: son todos
`python.exe`/`pythonw.exe`/`powershell.exe` y matar por nombre se lleva puesto cualquier
script del usuario. Si un proceso no se puede inspeccionar o matar — la bandeja corre elevada
— lo dice y pide una consola de administrador, en vez de informar "no había nada".

`daemon/stop.ps1` sigue sin conocer al motor nuevo **y no se puede tocar**: `daemon/` es la
vuelta atrás byte-idéntica de toda la fase (`git diff 50e146e -- daemon/` tiene que dar vacío).

### Saber si está andando

```powershell
python -m vmaxpanel --estado
```

```
dibujando — perfil Apex, panel ok, 12043 frames, 30 fps
publicado hace 2 s (pid 23060)
```

Código de salida **0** si está dibujando y **1** si no — no 2, porque "no está corriendo" es una
respuesta, no un error de uso, y un script tiene que poder distinguirlas.

El proceso que maneja el panel publica su estado a `vmaxpanel-estado.json` cada 5 s, con
reemplazo atómico. Un archivo y no un socket: el lector no necesita hablar con el proceso, sólo
saber qué ve, y así no hay puerto que abrir ni lector colgado que afecte al motor. **La
antigüedad se reporta siempre** porque un proceso puede estar vivo y no publicar — un motor
trabado en una escritura al puerto sigue existiendo —, y con más de 30 s de atraso lo dice.
También distingue *pausado* de *detenido*: pausar suelta COM3 a pedido del usuario, y confundirlo
manda a reiniciar algo que no hace falta.

Existe porque no había forma: la bandeja tiene el estado en su menú, pero desde una consola lo
único observable era el log y el CPU del proceso. Verificar que el panel andaba midiendo el CPU
de un `pythonw` es adivinar, y pasó tres veces en un día.

### Compartir y respaldar un perfil

```powershell
python -m vmaxpanel --profile <perfil> --exportar mi-perfil.vmaxpanel
python -m vmaxpanel --importar mi-perfil.vmaxpanel
python -m vmaxpanel --importar otro.vmaxpanel --si-existe renombrar
```

También desde el editor (**Exportar… / Importar…**, con diálogo de archivo) y desde la bandeja
(**Exportar el perfil…**, que guarda en `perfiles-exportados/` con la fecha en el nombre y abre
la carpeta — la bandeja es ctypes puro y no tiene ventana donde poner un mensaje).

Un `.vmaxpanel` es un zip con `perfil.json`, los assets que el fondo referencia y un
`bundle.json` con el manifiesto. **Copiar el `.json` suelto no alcanza:** un perfil referencia
assets y nombra fuentes, así que del otro lado aparece con el fondo degradado y las fuentes
cambiadas sin que nadie entienda por qué.

Cuatro decisiones que no se adivinan:

- **Las fuentes no se empaquetan.** Consolas y las Franklin Gothic son de Microsoft. Se listan
  en el manifiesto y al importar se avisa cuál falta *en esta máquina* — que es la diferencia
  entre "se ve raro" y "te falta esta fuente". La pregunta sólo se puede contestar del lado que
  recibe, así que se contesta al importar, no al exportar.
- **El JSON viaja byte a byte**, leído y escrito en bytes. Con `read_text` Python traduce CRLF a
  LF y el perfil que volvía tenía 60 bytes menos que el original: "es el mismo" habría sido
  mentira. Lo cazó una verificación contra los perfiles reales del repo, no el test — el test
  usaba un fixture de una sola línea.
- **Importar no pisa nada por defecto.** Dos personas exportando "apex" es lo normal; el layout
  del usuario es trabajo suyo. `--si-existe renombrar` o `pisar` si es lo que se quiere.
- **Un zip ajeno se trata como hostil.** Se valida el perfil *antes* de escribir nada (un bundle
  roto no puede dejar assets a medio copiar), se rechaza cualquier miembro absoluto, con `..` o
  con letra de unidad — zip-slip, y este proceso corre elevado —, y se corta por tamaño
  declarado para no descomprimir una bomba.

`perfiles-exportados/` tiene los bundles de los perfiles que vienen con el repo, como respaldo
listo para copiar a otra máquina.

### Fondos

`background.type` acepta seis: `solid`, `gradient`, `image` (estáticos, se cachean una vez) y
`procedural`, `sequence`, `video` (animados, un cuadro por frame).

| Tipo | Claves propias | Nota |
|---|---|---|
| `solid` | `color` | |
| `gradient` | `stops`, `angle` | `angle % 180` en [45,135) = vertical; el resto horizontal. No hay diagonales |
| `image` | `src`, `fit`, `color` | `color` es el relleno del letterbox |
| `procedural` | `name` (`scroll`\|`pulse`), `speed`, `period`, `stops` | parte del degradado; `scroll` usa el gradiente **y su espejo** para que el ciclo cierre sin tirón |
| `sequence` | `src` (carpeta), `fit`, `fps`, `color` | decodifica por cuadro a propósito: cachearlos son 1,4 MB cada uno |
| `video` | `src` (archivo), `fit`, `fps`, `color` | mp4, webm, mkv, gif — lo que ffmpeg sepa abrir |

En el editor, el campo `src` tiene un botón **Elegir…** que abre el diálogo de archivos y
**copia lo elegido a `vmaxpanel/assets/`**. Eso no es comodidad: `safe_asset_path` rechaza
cualquier ruta que se escape de ese directorio — con razón, el motor corre elevado —, así que un
video del Escritorio sólo puede funcionar copiándolo adentro. Si el nombre ya existe con otro
contenido, se guarda como `-2` en vez de pisar el asset de otro perfil; si existe con el mismo
contenido, se reusa.

**El video necesita ffmpeg**, que es externo: se busca en `vmaxpanel/lib/` y después en el PATH
(`winget install Gyan.FFmpeg`, o dejar `ffmpeg.exe` en `vmaxpanel/lib/`). Si falta, el fondo
degrada a color plano y el aviso dice el comando — no es una excepción. Externo y no PyAV ni
imageio-ffmpeg porque esas son una rueda binaria por plataforma y versión de Python, y el
criterio del proyecto es no sumar dependencias.

Un ffmpeg por fondo, escupiendo rgb24 crudo del tamaño exacto del panel; un hilo lo drena y
solo publica cuadros completos (`W*H*3` bytes), porque medio cuadro es basura dibujada. El loop
lo hace ffmpeg (`-stream_loop -1`) y el ritmo también (`-re`): sin `-re` decodifica a fondo y
quema un núcleo adelantando cuadros que nadie va a ver.

**Ciclo de vida, que es donde estaba el riesgo real:** `Renderer.set_layout()` cierra el fondo
anterior y `Engine._drop_link()` cierra el renderer. Sin eso, cada guardado del perfil (recarga
en caliente) y cada reconexión dejaban un ffmpeg decodificando para nadie — el mismo patrón de
proceso huérfano que este proyecto ya tuvo con `sensors.ps1` y el DLL de LHM.

Los animados en el editor se ven quietos: la vista previa es **un** cuadro. La pista de la
pestaña Fondo lo dice, junto con si ffmpeg está o no.

## Dependencias

Python 3.13 + `psutil`, `pyserial`, `pillow`. `ffmpeg` es opcional y solo para fondos de video. Los 3 DLL (`LibreHardwareMonitorLib`,
`HidSharp`, `HidLibrary`) están en `daemon/` — LHM necesita HidSharp al lado o `Open()`
falla. `frida-tools` se usó solo para reversear el protocolo; el daemon no la necesita.
