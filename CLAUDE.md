# Solarmax Display — instrucciones del proyecto

Driver propio para el panel del gabinete **Solarmax CM-B600L** (320x1480, `HL-VMAX-USB-Device`,
VID_33C3/PID_F101, **COM3**). Reemplaza al software vendor *LCD Control*.

**Leer `README.md` antes de tocar cualquier cosa.** Tiene el protocolo serial completo, el
mapa de sensores, la causa raíz documentada y la guía de edición.

## Lo que no hay que reinvestigar

- **`CpuUsage`/`CpuUse` de LCD Control son `% Processor Utility`** = carga × clock/2500,
  clampeado a 100 → satura con carga real ≥61%. Verificado desde adentro de la app y desde
  afuera en el mismo segundo. No existe token con la carga time-based; ya probé ~38 nombres.
- **WinRing0 (driver ring0 de LibreHardwareMonitor) está bloqueado**: `StartService → 0xE1`,
  está en la blocklist de drivers vulnerables de Windows. Sin MSR. No intentar habilitarlo.
- Por eso **package power (W) y fan RPM de CPU no se pueden leer**. Esos slots del layout
  son ahora VCORE y VRM. No prometer que vuelven.
- `ECLReadByte/Word` de GSA1 **no está implementado** en la B760M D3HP.
- El panel está montado al revés: los frames van **rotados 180°**.
- `consola.ttf`/`consolab.ttf` son **Consolas, de Microsoft**: no se redistribuyen. Las
  fuentes se piden por familia; en cualquier Windows están.
- `daemon/assets/back.png` es arte del tema Vitals de **LCD Control**: no se redistribuye.
  El fondo del perfil propio (`vmaxpanel/profiles/vitals.json`) es un `gradient`.

## Operación

Desde la raíz del repo. **Lo normal es que ya esté corriendo por la tarea `PanelVitals`**, que
levanta la bandeja al logon:

```powershell
cd E:\Claude\Solarmax_Display
python -m vmaxpanel.tray --log vmaxpanel.log   # bandeja: menú, pausa, editor, log
python -m vmaxpanel.editor                     # editor de layout
python -m vmaxpanel                       # maneja el panel con vmaxpanel/profiles/vitals.json
python -m vmaxpanel --once                # un solo frame
python -m vmaxpanel --save preview.png    # render a PNG, no toca el panel
python -m vmaxpanel --save p.png --no-sensors   # sin lanzar el sidecar
```

Editar `vmaxpanel/profiles/vitals.json` se recarga **en caliente**, sin reiniciar. Un JSON
roto no apaga el panel: se mantiene el layout anterior y el error queda en `state()`.

Daemon viejo (la vuelta atrás):

```powershell
cd E:\Claude\Solarmax_Display\daemon
.\start.ps1            # idempotente; -Force reinicia
.\stop.ps1             # mata daemon + sidecar y barre huérfanos
python panel.py --save preview.png     # previsualizar sin tocar el panel
```

**`stop.ps1` no conoce al motor nuevo:** barre por línea de comandos contra
`panel\.py|sensors\.ps1`, así que mata el sidecar nuevo (`vmaxpanel/sensors.ps1` matchea) pero
deja vivo el proceso `-m vmaxpanel`. Para ese, matar el `python.exe` correspondiente.

**Trampa recurrente:** un `powershell.exe` corriendo `sensors.ps1` que sobrevive al daemon
se queda con `LibreHardwareMonitorLib.dll` tomado y bloquea mover o borrar el directorio.
Filtrar procesos por `StartTime` no alcanza — usar `stop.ps1`, que barre por línea de
comandos.

## Seguridad

Los sensores salen de la interfaz **GSA1 ACPI-WMI** de Gigabyte (`root\WMI`,
`GSA1_ACPIMethod`), que además de lecturas expone `PIOWrite*`, `MEMWrite*`, `PCIWrite*`:
escritura arbitraria a puertos I/O, memoria física y espacio PCI. **El daemon usa solo
métodos de lectura.** No agregar escrituras sin saber exactamente a qué registro van.

## Estado

**Fases 1 y 3 completas, en `main`** (2026-08-12, 251 tests verdes). Fase 2 (fondos animados)
es lo único que falta. El paquete `vmaxpanel/` reemplaza el layout
hardcodeado de `daemon/panel.py` por un motor manejado por datos. Verificado contra el panel
real: muestra el layout nuevo y editar el perfil se refleja sin reiniciar. El widget `rect`, los
separadores del perfil y los 5 fixes de la revisión final entraron después de la revisión
inicial de las 12 tareas.

**`daemon/` quedó byte-idéntico a propósito** — es la vuelta atrás de toda la fase.
`git diff 50e146e -- daemon/` tiene que seguir dando vacío.

Al retomar, leer en este orden:

| Documento | Qué tiene |
|---|---|
| `docs/superpowers/specs/2026-08-11-vmax-panel-app-design.md` | Diseño de las 3 fases |
| `docs/superpowers/plans/2026-08-11-vmax-panel-fase1.md` | Plan de fase 1, 12 tareas |
| `.superpowers/sdd/2026-08-11-vmax-panel-fase1/progress.md` | **Ledger**: cada tarea, cada fix round, decisiones del usuario, ~15 minors diferidos. Gitignored |

### Pendiente

**Fase 3 implementada** (2026-08-12, `6661493`): bandeja + editor, sin dependencias nuevas.
Ver `docs/superpowers/specs/2026-08-12-vmax-panel-fase3-design.md`. **El servicio de Windows no
va y no volver a proponerlo**: sesión 0, sin acceso a la bandeja ni a ventanas.

Revisión final de la fase 1 corrida (nivel high, 30 commits / 47 archivos): 9 hallazgos,
ninguno falso positivo. **Los 5 que rompían invariantes se arreglaron en `89fb271` y los otros
5 en `9fb5c66`** — ya no queda nada de esa revisión pendiente. El diagnóstico de cada uno está
en el mensaje de esos dos commits, que es el lugar donde buscarlo si algo se rompe otra vez:

- `registry.py` — failover entre providers (arreglado)
- `sidecar.py` — `close()` sin `wait()` y la carrera del respawn (arreglado)
- `engine.py` — no relee el perfil sin layout válido (arreglado)
- `engine.py` — `rotate` 90/270 que no encaja en el panel (arreglado)
- `loader.py` — hot-reload por `st_mtime_ns`, ciego a dos escrituras en el mismo tick
  (arreglado: ahora es un hash del contenido)

- **Fase 2 (fondos animados) es lo único que queda sin plan.** El spike de throughput **ya está
  hecho** (`docs/superpowers/specs/2026-08-12-vmax-panel-fase2-spike.md`, herramienta en
  `research/spike_throughput.py`): nada del host es el cuello — 10 ms por frame extremo a
  extremo, 100 fps de techo, y un fondo animado suma 0,5–7,7 ms según la estrategia. **Lo que
  quedó sin medir es el refresco real del panel**, porque no aplica contrapresión: acepta 227 fps
  de escritura sostenida y descarta lo que no dibuja. Para saberlo hay que filmar el panel con un
  celular; es la única medición que falta y necesita a alguien delante del gabinete.
- **Verificación visual pendiente:** el ícono de la bandeja y la ventana del editor no se
  llegaron a mirar en pantalla — la máquina se bloqueó y en un escritorio bloqueado la captura
  sale negra y `FindWindow` no enumera las ventanas del escritorio del usuario. Lo funcional sí
  está verificado (la tarea levanta la bandeja, el motor toma COM3, el editor construye y
  guarda).
- El editor no tiene arrastrar-y-soltar ni UI para `fonts`/`background`/`rules`: eso sigue
  siendo edición del JSON, que la bandeja abre con un clic.
- **La RAM está corriendo a 5600, no a 6000**: una actualización de BIOS reseteó el XMP/EXPO y
  quedó en la base JEDEC. Si el kit es de 6000, hay que volver a activar el perfil en BIOS. El
  panel mostraba 6000 porque estaba **horneado** como un `label` en el perfil; ya se lee de
  `Win32_PhysicalMemory` (métrica `mem.speed`, provider `smbios` del sidecar). Es el mejor
  argumento contra los valores escritos a mano en un perfil que se comparte: el número cambió
  solo y nadie se enteró.

**Autostart hecho** (2026-08-11, apuntado a la bandeja el 2026-08-12): tarea `PanelVitals`
corriendo `pythonw -m vmaxpanel.tray --log`, tarea vendor `LCD ControlPowerBoot` deshabilitada.
Ver README, sección Autostart. **No la reemplaza un servicio de Windows: eso estaba mal en el
diseño original** — un servicio corre en la sesión 0 y desde ahí no se puede mostrar ni un ícono
de bandeja ni una ventana. Ver el spec de fase 3.

**Con el autostart puesto, `stop.ps1` alcanza todavía menos:** además de no conocer al proceso
de la bandeja, la tarea lo levanta de nuevo al siguiente logon. Para bajarlo de verdad,
`Stop-ScheduledTask PanelVitals` y matar el `pythonw.exe`.

### Nunca dejar una corrida de tests en background sin confirmar que murió

El 2026-08-12 un test del engine entró en un bucle infinito con reloj virtual —sin ningún
`sleep`—, la corrida se fue a background y **el proceso quedó comiéndose un núcleo entero
durante 7,28 horas**, hasta que el usuario avisó que la máquina no respondía. El `timeout` del
comando que la lanzó mató al envoltorio, no al `python` hijo.

`tests/conftest.py` ahora mata cualquier test que pase de 60 s (`VMAXPANEL_TEST_TIMEOUT` para
cambiarlo) y escribe el stack de todos los hilos en `pytest-hang.txt`, que es lo que dice **qué**
se colgó. Va a un archivo y no a stderr porque pytest captura stderr a nivel de descriptor y
`os._exit()` se lleva ese buffer sin escribir nada.

Igual, la regla de operación: si una corrida se va a background, **confirmar que terminó**. No
alcanza con verla fallar y seguir.

### Al escribir los planes de fase 2 y 3

Especificar **interfaces y tests, no la implementación**. En fase 1 puse el código completo en
el plan para que las tareas fueran transcripción, y el efecto fue que mis errores se volvieron
obligatorios: 4 de las primeras 5 tareas encontraron defectos reales en el código del plan, no
en el trabajo del implementador. Desde la tarea 6 les dije explícitamente *"no transcribas bugs
del brief, rechazá lo que veas mal"* y la calidad subió de golpe.
