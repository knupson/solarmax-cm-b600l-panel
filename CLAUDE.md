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

Motor nuevo (fase 1), desde la raíz del repo:

```powershell
cd E:\Claude\Solarmax_Display
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

**Fase 1 de VMax Panel implementada y revisada** (2026-08-11, rama
`fase1-motor-data-driven`, 178 tests verdes). El paquete `vmaxpanel/` reemplaza el layout
hardcodeado de `daemon/panel.py` por un motor manejado por datos. Verificado contra el panel
real: muestra el layout nuevo y editar el perfil se refleja sin reiniciar.

**`daemon/` quedó byte-idéntico a propósito** — es la vuelta atrás de toda la fase.
`git diff 50e146e -- daemon/` tiene que seguir dando vacío.

Al retomar, leer en este orden:

| Documento | Qué tiene |
|---|---|
| `docs/superpowers/specs/2026-08-11-vmax-panel-app-design.md` | Diseño de las 3 fases |
| `docs/superpowers/plans/2026-08-11-vmax-panel-fase1.md` | Plan de fase 1, 12 tareas |
| `.superpowers/sdd/2026-08-11-vmax-panel-fase1/progress.md` | **Ledger**: cada tarea, cada fix round, decisiones del usuario, ~15 minors diferidos. Gitignored |

### Pendiente

- **Decisión abierta, sin responder:** al ver el panel, el usuario notó que **se perdieron las
  líneas separadoras**. Estaban horneadas en `back.png` (arte del vendor, ya no se usa) y
  ningún widget de fase 1 dibuja líneas. Propuse un tipo `rect` (relleno o solo borde, con
  radio) que cubre divisores y marcos; quedó por decidir si va ahora, en fase 2, o junto con
  los minors. Pidió reiniciar la PC antes de contestar.
- **La revisión final de toda la rama no se corrió** — último paso antes de cerrar la fase.
- Fases 2 (fondos animados) y 3 (servicio + tray + editor) no tienen plan todavía. El de
  fase 2 arranca con un spike de throughput: cuántos fps traga el panel decide todo lo demás.
- **Autostart pendiente**: la tarea `PanelVitals` (ver README, sección Autostart) todavía no
  está registrada — hizo falta permiso del usuario y quedó sin hacer. La fase 3 la reemplaza
  por un servicio de Windows.

### Al escribir los planes de fase 2 y 3

Especificar **interfaces y tests, no la implementación**. En fase 1 puse el código completo en
el plan para que las tareas fueran transcripción, y el efecto fue que mis errores se volvieron
obligatorios: 4 de las primeras 5 tareas encontraron defectos reales en el código del plan, no
en el trabajo del implementador. Desde la tarea 6 les dije explícitamente *"no transcribas bugs
del brief, rechazá lo que veas mal"* y la calidad subió de golpe.
