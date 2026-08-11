# VMax Panel — diseño

**Fecha:** 2026-08-11
**Estado:** diseño aprobado en brainstorming, pendiente de revisión escrita
**Reemplaza:** `daemon/panel.py` como script de un solo layout hardcodeado

## Objetivo

Convertir el driver actual del panel HL-VMAX (320x1480, VID_33C3/PID_F101) en una
aplicación con editor visual de layout, fondos estáticos y animados, y autostart propio
mediante servicio de Windows — **distribuible a otros usuarios del mismo panel**.

Hoy el layout vive como coordenadas literales en `Renderer.frame()`, el fondo es un PNG
estático del tema vendor, y el arranque es `start.ps1` a mano.

## Restricciones que impone la distribución

Nada específico de esta máquina puede quedar quemado:

| Hoy | Después |
|---|---|
| `PORT = 'COM3'` | autodetección por VID_33C3/PID_F101 sobre `serial.tools.list_ports.comports()`; si hay varios, el usuario elige y queda en config |
| `W, H = 320, 1480` | parseado del SN (`VMAXA170320*1480S261001155` → `320*1480`), con fallback a 320x1480 si el patrón no matchea |
| `--rotate 180` por default | setting por usuario; este panel está montado al revés, el de otro no |
| `'INTEL CORE i5-12400F'` literal | `cpu.name` desde provider |
| `'AMD RADEON RX 6800 XT'` fallback | `gpu.name` desde provider |
| `'6000'` (mem speed) literal | widget `label` con texto del usuario — no es métrica leíble sin SMBus |
| `DIAS`/`MESES` en español | locale configurable |
| GSA1 asumido presente | provider con `probe()`; sólo Gigabyte |

**Assets:** `daemon/assets/back.png` es el fondo del tema Vitals de LCD Control — arte del
vendor. No se redistribuye. El diseño lo vuelve innecesario: las etiquetas horneadas en el
PNG pasan a ser widgets `label`, y el fondo baja a una capa lisa o procedural, original.

**Licencias de terceros:** `LibreHardwareMonitorLib.dll` es MPL-2.0 y `HidSharp` MIT — ambos
redistribuibles con su aviso de licencia incluido. El paquete debe incluir un `THIRD-PARTY-NOTICES`.
`frida` no se distribuye: se usó sólo para reversear el protocolo.

**Fuentes:** `daemon/assets/consola.ttf` y `consolab.ttf` son **Consolas, de Microsoft — no
redistribuibles**. Mismo problema que `back.png`. Se deja de empaquetar TTFs: las fuentes se
resuelven **por nombre de familia**, buscando primero en `assets/fonts/` (vacío por ahora) y
después en las fuentes del sistema. En cualquier Windows Consolas está instalada, así que un
layout que la pide funciona sin que la app la distribuya. Empaquetar una mono libre
(JetBrains Mono, Apache-2.0) como default es tarea de la fase 3, cuando haya que distribuir.

**Un hardcode más, en el sidecar:** `sensors.ps1:17` tiene `$BASE_MHZ = 2500`, el base clock
del i5-12400F. El clock de CPU saldría mal en cualquier otro procesador. Se detecta con
`Win32_Processor.MaxClockSpeed`.

**Validación sin dependencias:** el schema de layout se valida con un validador propio en vez
de `jsonschema`. Da errores atados al modelo (*"widget cpu-load: métrica desconocida
cpu.powr"*) y es una dependencia menos para distribuir.

## Decisiones tomadas

| Decisión | Elección | Por qué |
|---|---|---|
| Alcance | Un spec, 3 fases | El editor necesita un motor data-driven abajo; construirlo primero evita rehacerlo |
| Stack GUI | PySide6 / Qt | Tray nativo, canvas de arrastre, mismo lenguaje que el motor |
| Editor ↔ motor | Local para el drag + push efímero por IPC | Arrastre fluido sin round-trips, y se ve en el panel real mientras editás |
| Renderers | **Uno solo** (PIL), compartido | Dos renderers divergen y el preview termina mintiendo |
| Sensores | Capa de providers con degradado | Otras placas no tienen GSA1; otras máquinas sí pueden tener WinRing0 |
| Autostart | Servicio Windows + tray de usuario | Arranca antes del logon, siempre elevado, lo reinicia el SCM |
| Empaquetado | Desde source ahora, PyInstaller al final | Iterar una coordenada no puede costar un build |

## Arquitectura

Tres procesos:

| Pieza | Corre como | Responsabilidad |
|---|---|---|
| `vmaxpanel-service` | Servicio Windows, SYSTEM, automático | Dueño único del puerto serial. Providers, loop de render, envío de frames |
| `vmaxpanel-tray` | Proceso de usuario (PySide6) | Ícono de tray, estado, brillo, cambio de perfil, lanza el editor |
| Editor | Ventana de la app tray | Canvas de widgets, inspector, paleta de métricas, fondos |

El **renderer es un módulo compartido** importado por el servicio y por el editor. El editor
nunca abre el puerto serial: sólo el servicio lo hace, así que no reaparece la pelea por COM.

### IPC

Named pipe `\\.\pipe\vmaxpanel`, JSON por línea. Comandos:

| Comando | Efecto |
|---|---|
| `get_state` | estado del panel, providers disponibles, perfil activo, fps y calidad efectivos |
| `get_sample` | última muestra de sensores (para que el preview del editor tenga datos reales) |
| `set_brightness` | 0..100 |
| `list_profiles` / `load_profile` | por nombre, no por ruta |
| `preview_layout` | layout efímero en RAM, no persistido |
| `stop_preview` | vuelve al perfil persistido |

### Seguridad del IPC

El servicio corre como SYSTEM y expone un pipe: es superficie de escalada local. El
multiplicador es que GSA1 expone `PIOWrite`, `MEMWrite` y `PCIWrite` — escritura arbitraria
a puertos I/O, memoria física y espacio PCI. Un pipe de SYSTEM que alcanzara esos métodos
sería un primitivo de escritura a kernel para cualquier proceso local.

Invariantes no negociables:

1. **El IPC nunca transporta nombres de métodos de sensor.** Los providers exponen una
   allowlist fija de lecturas compilada en el código. Ningún comando puede nombrar un método
   WMI, un puerto ni una dirección.
2. **DACL restrictiva:** sólo `Administrators` y el usuario interactivo. Nunca `Everyone`.
3. **Rutas de assets confinadas:** toda ruta de imagen o video se resuelve a canónica y se
   valida que caiga dentro del directorio de assets. Sin eso, un `..\..\` le hace leer
   archivos como SYSTEM.
4. **Ningún comando ejecuta nada.** El pipe mueve datos y enums, no rutas de ejecutables ni
   comandos de shell.
5. **Los layouts son puramente declarativos.** Las reglas de color son comparadores, no
   expresiones evaluables. Sin `eval`. Descargar el layout de otro usuario no puede correr
   código.

Perfiles y assets viven en `%PROGRAMDATA%\VMaxPanel\` con la ACL por defecto (sólo admins
escriben); el tray se eleva para guardar. Dejar que un usuario sin privilegios escriba lo que
SYSTEM lee reabriría el mismo agujero por otra puerta.

El daemon sigue usando **sólo métodos de lectura** de GSA1.

## Capa de sensores

Ids canónicos, desacoplados del origen:

```
cpu.name  cpu.load  cpu.temp  cpu.clock  cpu.vcore  cpu.vrm_temp  cpu.power  cpu.fan
gpu.name  gpu.load  gpu.temp  gpu.hotspot  gpu.clock  gpu.power  gpu.vram  gpu.fan
mem.load  mem.used  mem.total
net.down  net.up
disk.temp.0  disk.temp.1  ...
clock.time  clock.date
```

`disk.temp.N` es **posicional** sobre los discos que reporta el provider, ordenados de forma
estable. Un layout compartido que pide `disk.temp.2` en una máquina con dos discos deja ese
widget en `unavailable` en vez de mostrar el disco equivocado. `mem.speed` no está en la lista
a propósito: sin acceso SMBus no se lee, así que se pone como widget `label`.

Interfaz:

```python
class Provider:
    id: str                       # "psutil" | "lhm" | "gsa1" | "msr"
    def probe(self) -> bool       # ¿existe en esta máquina?
    def metrics(self) -> set[str]
    def read(self) -> dict[str, float | str]
```

| Provider | Disponibilidad | Aporta |
|---|---|---|
| `psutil` | siempre | `cpu.load` real (`% Processor Time`), clock, RAM, red |
| `lhm` | casi siempre (sidecar PowerShell + DLLs) | GPU completo, temps de SSD por SMART NVMe |
| `gsa1` | sólo Gigabyte con `GSA1_ACPIMethod` | `cpu.temp` (id 2), `cpu.vrm_temp` (id 4), `cpu.vcore` (EZVGetVoltage id 5) |
| `msr` | sólo si WinRing0 carga — **en esta máquina no** | `cpu.power`, `cpu.fan` |

El registry resuelve cada id por prioridad entre providers disponibles. Un id que nadie sirve
queda **`unavailable`**, estado distinto de `None`: en el editor el widget se muestra tachado
con el motivo (*"requiere placa Gigabyte"*, *"requiere WinRing0, bloqueado por Windows"*) en
vez del `--` sin explicación de hoy. Un usuario cuyo Windows permita WinRing0 gana package
power y fan RPM sin que nadie toque código.

Nota de contexto ya verificada: en esta máquina WinRing0 falla con `StartService → 0xE1`
(`ERROR_VIRUS_INFECTED`, blocklist de drivers vulnerables). No se intenta habilitarlo.

## Formato de layout

`layout.json` versionado y validado por esquema al cargar.

```json
{
  "version": 1,
  "name": "Vitals",
  "designed_for": { "width": 320, "height": 1480 },
  "panel": { "rotate": 180, "brightness": 100, "fps": 1, "jpeg_quality": 82 },
  "fonts": {
    "mono-14":      { "family": "consola",  "size": 14 },
    "mono-bold-60": { "family": "consolab", "size": 60 }
  },
  "background": { "type": "solid", "color": "#0F1218" },
  "widgets": [
    { "id": "cpu-hdr", "type": "label", "text": "CPU",
      "x": 24, "y": 230, "font": "mono-14", "color": "#898781" },

    { "id": "cpu-load", "type": "text", "metric": "cpu.load",
      "x": 20, "y": 248, "font": "mono-bold-60", "color": "#FFFFFF",
      "format": "{:.1f}%", "align": "left",
      "rules": [ { "when": "> 85", "color": "#FF4444" } ] },

    { "id": "cpu-bar", "type": "bar", "metric": "cpu.load",
      "x": 24, "y": 316, "w": 272, "h": 16, "radius": 5,
      "fill": "#3987E5", "track": "#242834", "min": 0, "max": 100 }
  ]
}
```

Tipos de widget:

| Tipo | Para qué |
|---|---|
| `text` | métrica formateada |
| `label` | texto fijo — reemplaza las etiquetas horneadas en `back.png` |
| `bar` | barra lineal, como las tres actuales |
| `arc` | barra radial |
| `graph` | historial en ventana deslizante (carga, temps) |
| `image` | logo o ícono propio del usuario |

Decisiones del formato:

- **`designed_for`** declara la geometría asumida al diseñar; el renderer escala si el panel
  real difiere. Un layout compartido no se rompe en otro modelo. La escala es **uniforme**
  (`min(w_real/w_dis, h_real/h_dis)`) y se aplica a coordenadas, tamaños **y tamaños de
  fuente**, centrando el resultado si la relación de aspecto difiere. Escalar los ejes por
  separado deformaría el texto, así que no se hace.
- **Las fuentes son alias** resueltos contra la tabla `fonts`, no rutas de archivo. Un layout
  que pide una fuente ausente cae a la mono empaquetada en vez de crashear.
- **`rules`** son comparadores simples (`> 85`, `< 30`, `>= 0.9`) sobre el valor de la
  métrica. Sin expresiones evaluables: los layouts se comparten entre usuarios.
- **`format`** es una plantilla de `str.format` restringida a un solo campo posicional; se
  valida en el schema.
- **`humanize`** (`"none" | "rate" | "bytes"`) cubre lo que una plantilla de `format` no
  puede: convertir 1258291 en `"1.2 MB/s"`. Es lo que hoy hace `human_rate()` a mano y hace
  falta para tener paridad con el layout actual.

Migración de formato: `version` permite convertir perfiles viejos al cargar. Un perfil con
`version` mayor a la soportada se rechaza con mensaje claro en vez de renderizar mal.

## Fondos

| Tipo | Fuente |
|---|---|
| `solid` | color |
| `gradient` | dos o más paradas, ángulo |
| `image` | PNG/JPG, con modo de encaje (cover / contain / stretch) |
| `sequence` | GIF, APNG o carpeta de PNG numerados — Pillow, sin dependencias nuevas |
| `video` | MP4/WebM vía `imageio-ffmpeg` |
| `procedural` | gradiente en movimiento, scanlines, partículas, waveform reactivo a `cpu.load` |

**Fps desacoplado.** El fondo tiene su propio fps; los datos se refrescan a 1 Hz. El loop
corre al ritmo del fondo y sólo recalcula texto cuando entró muestra nueva.

**El techo es bytes por segundo, no frames.** 320x1480 a calidad 82 son ~60 KB; 15 fps piden
~900 KB/s por un CDC cuyo techo práctico, si es full-speed USB 2.0, ronda 700–900 KB/s. Fps y
calidad JPEG son la misma perilla. El servicio mide el tiempo de escritura real y degrada
cuando no alcanza — primero calidad, después fps — en vez de acumular retraso. Expectativa
honesta: **8–12 fps**, suficiente para paneos lentos y gradientes, no para video con
movimiento rápido. Se confirma en el spike que abre la fase 2.

**Caché.** Frames pre-escalados, pre-rotados, en RGB crudo: 320x1480x3 = 1,36 MiB cada uno.
Presupuesto por defecto 256 MiB (~188 frames); más largo que eso se decodifica en streaming.
Composición por frame: copia del fondo (memcpy barato) + dibujo de widgets + encode JPEG.

## Editor

Tres columnas: lista de capas, canvas con el preview, inspector de propiedades y paleta de
métricas.

- Zoom por defecto ajustado a la altura (320x1480 es muy alto para 1:1).
- Arrastre con snap a guías, nudge con flechas, `x`/`y` numérico.
- Undo/redo (`QUndoStack`).
- Toggle *como se diseña* / *como queda montado* (con rotación aplicada).
- Import/export de perfil como `.json` para compartir.
- Modo **live**: el layout en curso va al panel real por `preview_layout`; al cerrar sin
  guardar, el servicio vuelve al perfil persistido.
- El preview usa el renderer compartido con datos reales vía `get_sample`, o datos sintéticos
  si el servicio no corre — el editor abre igual.

## Servicio y convivencia

Servicio `VMaxPanel` (display: *VMax Panel Service*) vía `pywin32`, arranque automático, con
*FailureActions* para que el SCM lo reinicie: 3 intentos, intervalo de 1 minuto. Instalación y
desinstalación desde la GUI (elevándose) y también por CLI.

La tarea vendor `LCD ControlPowerBoot` (hoy en estado *Ready*) se deshabilita como **paso
opt-in con checkbox**, guardando el estado original para restaurarlo al desinstalar. Un
instalador que apaga en silencio la tarea de otro fabricante está mal, incluso cuando
conviene. Si LCD Control igual toma el puerto, el tray lo reporta como conflicto en vez de
pelear por él.

**Dependencia declarada del entorno:** la configuración de UAC varía por máquina y
puede pedir confirmación al elevar. El servicio como SYSTEM no
depende de eso; el tray, para guardar en `%PROGRAMDATA%`, sí necesita elevarse y en otra
máquina con prompt activo mostrará el diálogo de UAC. Es correcto que lo haga.

## Manejo de errores

| Falla | Respuesta |
|---|---|
| Layout inválido | Rechazado por el schema; **se mantiene el anterior**. El panel nunca queda negro por un JSON roto |
| Sidecar muerto | Relanzar con backoff; las métricas de ese provider pasan a `unavailable` |
| Panel desconectado / puerto tomado | Reintento con backoff (ya existe); estado visible en el tray |
| Resume de suspensión | Cubierto por el reintento serial |
| Servicio caído | Lo reinicia el SCM |
| Asset faltante | Fondo cae a `solid`; el widget `image` se omite y se reporta |
| Provider que tira excepción | Se marca degradado y se reintenta con backoff; no tumba el loop |

## Testing

Casi todo se testea **sin el hardware**, que es lo que importa para distribuir:

- **Renderer:** golden-image tests — layout fijo + muestra fija → PNG comparado con tolerancia.
- **Providers:** providers falsos; el registry se testea con disponibilidad sintética, incluido
  el caso "nadie sirve esta métrica".
- **Schema:** batería de layouts válidos e inválidos, incluidos `format` malicioso, `version`
  futura y rutas de asset con `..`.
- **IPC:** servidor de pipe en proceso; validar que comandos desconocidos o malformados se
  rechacen sin efecto.
- **Serial:** transporte falso que captura bytes — verificar handshake `F0 A5 5A 0F`, comando
  de brillo `AA BB xx CC DD`, geometría del JPEG, y que abra en `FFD8` y cierre en `FFD9`.
- **Fondos:** que el presupuesto de caché se respete y que el degradado de calidad/fps se
  active bajo un transporte falso lento.

Con hardware queda sólo el spike de throughput y una prueba de humo de extremo a extremo.

## Fases

### Fase 1 — motor data-driven

Registry de métricas, capa de providers, `layout.json` con schema, renderer de widgets,
hot-reload al guardar. Paridad visual con el layout actual pero con las etiquetas como
widgets `label` y **fondo original propio** en lugar de `back.png` del vendor. Golden tests
verdes. Sigue arrancando con `start.ps1`: al final de la fase el panel muestra lo mismo que
hoy, manejado por datos.

### Fase 2 — fondos

Arranca con el **spike de throughput**: medir fps y bytes/s reales del panel, y fijar la
relación calidad/fps. Después los seis tipos de fondo, fps desacoplado, presupuesto de caché
y degradado adaptativo.

### Fase 3 — aplicación

Servicio + IPC con su DACL, tray, editor completo, perfiles, instalar/desinstalar servicio
desde la GUI, paso opt-in de la tarea vendor, y empaquetado opcional con PyInstaller + Inno
Setup.

## Fuera de alcance

- Habilitar WinRing0 o cualquier driver ring0. Está bloqueado y no se intenta.
- Cualquier método de escritura de GSA1 (`PIOWrite`, `MEMWrite`, `PCIWrite`).
- Soporte de paneles que no sean HL-VMAX.
- Sincronización de perfiles en la nube o repositorio comunitario. Compartir es exportar un
  `.json`.
- Localización más allá de hacer configurable el idioma de fecha.
