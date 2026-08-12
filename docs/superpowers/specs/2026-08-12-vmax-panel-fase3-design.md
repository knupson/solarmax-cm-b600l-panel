# VMax Panel — fase 3: app de bandeja + editor

**Fecha:** 2026-08-12
**Estado:** implementado

## Qué cambia respecto del diseño original

El diseño de las tres fases decía *"servicio de Windows + tray + editor"*. **El servicio no
va, y no es un recorte de alcance: es que estaba mal.**

Un servicio de Windows corre en la **sesión 0**, aislada de la sesión del usuario desde Windows
Vista. Desde ahí es imposible mostrar un ícono en el área de notificación o abrir una ventana:
las dos cosas que definen la fase. Además, un servicio en Python exige `pywin32`, una
dependencia nueva en un proyecto que se reparte a otros dueños del panel.

La tarea programada al logon que ya existía cumple la misma función —levantar el panel sin
intervención— y sí corre en la sesión del usuario. Fase 3 queda:

| Pieza | Módulo | Qué hace |
|---|---|---|
| Autostart | tarea `PanelVitals` | levanta la bandeja al logon, `RunLevel Highest` |
| Supervisor | `vmaxpanel/app.py` | el motor en un thread: arrancar, pausar, reanudar, estado |
| Bandeja | `vmaxpanel/tray.py` | ícono + menú, Win32 por ctypes |
| Editor | `vmaxpanel/editor.py` | `EditorState` (lógica) + `EditorWindow` (Tkinter) |
| Log | `vmaxpanel/logsetup.py` | redirección compartida por CLI y bandeja |

## Principio de diseño: la UI no tiene lógica

`tray.py` y `EditorWindow` no deciden nada. Todo lo que hacen se lo piden a `PanelApp` y a
`EditorState`, que no importan ni Win32 ni Tkinter y tienen tests. Es lo que permite que la
parte que puede estar mal esté cubierta, y que la parte sin cobertura sea solo pegamento.

La bandeja es el único módulo del proyecto sin tests automáticos, a propósito. Lo que sí se
testea de ella es el texto del tooltip, porque es lo único que el usuario ve sin abrir el menú
y porque `szTip` es un `WCHAR[128]` que hay que respetar.

## Sin dependencias nuevas

- **Bandeja:** `Shell_NotifyIcon` por ctypes, no `pystray` (LGPL-3.0) ni `pywin32`.
- **Editor:** Tkinter de la stdlib. `ImageTk` de Pillow si está, con respaldo a PNG en base64,
  que Tk 8.6 lee nativo.

## Decisiones que no se adivinan

**`pause()` suelta el puerto, no solo deja de dibujar.** Es como el usuario le presta el panel
a LCD Control sin cerrar la app.

**El sleep del motor es interrumpible.** `_InterruptibleClock` usa un `Event` en vez de
`time.sleep`. Sin eso, "Salir" en la bandeja tarda hasta 10 s —lo que dure el backoff de
reconexión— con el menú ya cerrado y el usuario pensando que se colgó.

**El editor corre en su propio proceso.** Tkinter quiere ser el thread principal, y ahí está el
bombeo de mensajes de Win32: los dos en el mismo proceso son un cuelgue. Aparte también
significa que si el editor se cae, el panel sigue dibujando.

**El archivo es el protocolo.** No hay IPC entre el editor y el motor: el editor guarda con
`loader.save()` (atómico) y el motor lo levanta en caliente. Por eso la detección de cambios
tuvo que pasar de `st_mtime_ns` a un hash del contenido: el editor guarda dos veces seguidas y
las dos escrituras caían en el mismo tick.

**El editor nunca guarda un layout inválido.** El motor lo rechazaría y se quedaría con el
anterior, así que el usuario habría "guardado" algo que el panel ignora sin decirle por qué.
Mientras el layout no valida, el preview mantiene el último válido: la misma regla que el panel.

**El preview usa una muestra de demostración.** Las métricas que esta máquina no sirve
(`cpu.power`, `cpu.fan`) igual se dibujan con valores plausibles del medio del rango declarado.
Un preview lleno de `--` no sirve para diseñar.

## Lo que la bandeja muestra

Estado (`ok` / `desconectado` / `en pausa` / `detenido`) con el contador de frames, el último
error si hay, y las métricas sin datos. Después: pausar/reanudar, reiniciar el motor, abrir el
editor, abrir el JSON, ver el log, salir.

## Lo que falta

- Verificación visual del ícono y del editor en pantalla: quedó pendiente porque la máquina se
  bloqueó (en un escritorio bloqueado la captura sale negra y `FindWindow` no enumera las
  ventanas del escritorio del usuario).
- El editor no tiene arrastrar-y-soltar: se mueve con botones y flechas del teclado. Alcanza
  para ajustar un layout, no para diseñar uno de cero cómodamente.
- No hay UI para editar `fonts`, `background` ni las `rules` de color: eso sigue siendo edición
  del JSON a mano, que la bandeja abre con un clic.
- Fase 2 (fondos animados) sigue sin plan. Arranca con un spike de throughput.
