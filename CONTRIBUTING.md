# Contribuir a VMax Panel

Se aceptan aportes. Esto explica cómo está armado el proyecto, qué se espera de un cambio y
qué cosas **no** hay que tocar.

## Acuerdo de contribución (CLA)

Al mandar un pull request aceptás que:

1. El aporte es tuyo, o tenés derecho a cederlo.
2. Le cedés al dueño del proyecto (Alejandro, [@knupson](https://github.com/knupson)) un
   derecho de copyright **no exclusivo, mundial, perpetuo, irrevocable y libre de regalías**
   para usar, modificar, publicar y relicenciar tu aporte como parte de este proyecto.
3. Seguís siendo dueño de tu aporte y podés usarlo donde quieras.

Está para que el proyecto pueda mantener una licencia única y coherente, y para que el
dueño pueda relicenciarlo más adelante sin tener que rastrear a cada persona que alguna vez
mandó un parche. No te pide exclusividad ni te saca nada.

El código que mandes queda bajo la [PolyForm Noncommercial 1.0.0](LICENSE), como el resto.

## Arrancar

```powershell
git clone https://github.com/knupson/vmax-panel && cd vmax-panel
pip install -r requirements.txt
pip install pytest
python -m pytest                          # 594 tests, ~85 s
python -m vmaxpanel --diagnostico         # qué falta en esta máquina
python -m vmaxpanel --save preview.png    # renderiza sin tocar el panel
```

**No hace falta el panel para desarrollar.** `--save` dibuja a un PNG y los tests no tocan
hardware: se corren en cualquier Windows. Lo que sí es específico de esta máquina son
algunos sensores, y el diagnóstico dice cuáles.

Tampoco hacen falta las DLL de LibreHardwareMonitor: sin ellas se pierden GPU, temperaturas
de disco y RPM, y todo lo demás anda.

## El mapa

```
vmaxpanel/
  cli.py          argumentos, --save/--once/--estado/--instalar/--parar
  app.py          arma el motor con sus dependencias
  engine.py       el bucle: leer sensores -> renderizar -> mandar al panel
  tray.py         app de bandeja (ctypes/Win32 puro)
  editor.py       editor de layout (Tkinter puro)
  metrics.py      catálogo de métricas: id, nombre, unidad, rango
  layout/
    schema.py     valida el JSON del perfil y explica cada error
    model.py      dataclasses del layout
    loader.py     carga y recarga en caliente (por hash del contenido)
  render/
    renderer.py   compone el cuadro, escala el layout al panel real
    widgets.py    text, label, bar, arc, graph, image, rect
    background.py solid, gradient, image, sequence, procedural, video
    video.py      ffmpeg como proceso externo
    fonts.py      resuelve familias y cadenas de alternativas
  providers/
    registry.py   resuelve cada métrica al provider disponible de más prioridad
    sidecar.py    habla con sensors.ps1
    ...
  transport/
    panel_link.py protocolo serial, autodetección por VID/PID
  sensors.ps1     sidecar de PowerShell: GSA1, PDH, LibreHardwareMonitor, SMBIOS
```

## Cómo agregar cosas

**Un widget nuevo.** Una dataclass en `layout/model.py`, su entrada en `WIDGET_TYPES` de
`layout/schema.py`, y un `_draw_*` en `render/widgets.py`. Los tests van en
`tests/test_widgets.py`: dibujá sobre un canvas y afirmá sobre píxeles concretos, no sobre
"no explotó".

**Una métrica nueva.** Entrada en `metrics.py` (id, nombre, unidad, mínimo, máximo) y algún
provider que la sirva. Si sale de PowerShell, va en `sensors.ps1` y en
`providers/sidecar_providers.py`. El orden de preferencia entre providers está en
`PROVIDER_PRIORITY`, en `providers/registry.py`.

**Un fondo nuevo.** `BACKGROUND_TYPES` en `layout/schema.py` y una rama en
`render/background.py`. Si es animado, sumalo a `ANIMADOS`. Si abre un proceso o un archivo,
tiene que cerrarse en `close()`: cada fondo animado que no cierra deja un proceso huérfano
por cada guardado del perfil.

## Lo que se espera de un cambio

- **Test que falla primero.** El proyecto se hizo así y se nota: los bugs que llegaron a
  producción son justo los que ningún test miraba. Si arreglás algo, el test tiene que
  fallar antes del fix.
- **Mirá el resultado, no sólo el test.** Tres defectos visibles sobrevivieron 590 tests
  verdes hasta que alguien renderizó un PNG y lo miró. `--save` es barato.
- **Sin dependencias nuevas.** Son tres (`Pillow`, `pyserial`, `psutil`) y la idea es que
  sigan siendo tres. La bandeja es ctypes y el editor es Tkinter justo por eso.
- **Comentarios que digan por qué, no qué.** El repo está lleno de comentarios que explican
  la trampa que motivó cada línea. Es deliberado: son la razón por la que un bug no vuelve.
- Los comentarios y los mensajes están **en castellano** hoy. Hay una traducción al inglés
  en camino; hasta entonces, seguí el idioma del archivo que estés tocando.

## Lo que no hay que tocar

- **`daemon/`** es la vuelta atrás byte-idéntica de la versión anterior. `git diff` contra su
  commit base tiene que seguir dando vacío. Si necesitás cambiar algo ahí, no lo necesitás.
- **`daemon/stop.ps1` no sirve para el motor nuevo** y no se arregla, por lo de arriba. Para
  bajar el panel: `python -m vmaxpanel --parar`.
- **WinRing0 y cualquier driver ring0.** Está en la blocklist de Windows y no se va a
  habilitar. Si una métrica necesita MSR, no se lee y listo.
- **Escrituras por GSA1.** La interfaz WMI de Gigabyte expone `PIOWrite`, `MEMWrite` y
  `PCIWrite`: escritura arbitraria a puertos de I/O, memoria física y espacio PCI. El
  proyecto usa **sólo métodos de lectura** y así se queda.
- **Fuentes y DLL de terceros.** No se committean. Las fuentes se piden por familia con una
  cadena de alternativas; las DLL las baja el usuario.

## Reportar un bug

Abrí un issue con la salida de `python -m vmaxpanel --diagnostico` y, si tiene que ver con
lo que se dibuja, el PNG de `python -m vmaxpanel --save bug.png`. Con esas dos cosas casi
siempre alcanza.

Si algo se cuelga, `tests/conftest.py` mata cualquier test que pase de 60 s y escribe el
stack de todos los hilos en `pytest-hang.txt`. Ese archivo es lo que dice qué se colgó.
