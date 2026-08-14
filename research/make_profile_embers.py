"""Genera vmaxpanel/profiles/embers.json: perfil ralo pensado para ver el fondo.

**Por que existe.** Apex tapa el fondo. Sus bloques de seccion son rects opacos de
304x390 y el video queda visible solo en los pocos huecos: puesto sobre Apex, un
fondo de video se ve como un tinte calido y nada mas. No es un bug del fondo, es
que ese layout esta disenado para llenar el panel de datos.

Asi que este perfil hace lo contrario: **cero rects de relleno**, solo hairlines de
separacion, y las cinco cosas que uno mira de reojo -- hora, CPU, GPU, RAM, red --
repartidas con aire en medio. Dos tercios del panel son fondo a la vista.

Uso: python research/make_profile_embers.py
"""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from vmaxpanel.layout import loader, schema  # noqa: E402

ANCHO = 320
CENTRO = ANCHO // 2
MARGEN = 16
NARANJA = "#FF4D00"
TINTA = "#F6EEE8"          # el texto principal
ROTULO = "#9A8578"         # las etiquetas, un paso atras
LINEA = "#3A2418"          # los hairlines: se ven sobre el fondo sin competir

FUENTES = {
    "hero": {"family": "Bahnschrift", "size": 66},
    "big": {"family": "Bahnschrift", "size": 52},
    "eyebrow": {"family": "Franklin Gothic Demi Cond", "size": 22},
    "dato": {"family": "Franklin Gothic Medium Cond", "size": 21},
    "micro": {"family": "Franklin Gothic Medium Cond", "size": 14},
}

widgets = []


def w(**kw):
    widgets.append(kw)


def separador(y):
    w(id=f"sep-{y}", type="rect", x=MARGEN, y=y, w=ANCHO - 2 * MARGEN, h=1,
      fill=LINEA)


def bloque(clave, titulo, y, metrica_pct, nota, filas):
    """Una seccion: eyebrow + porcentaje grande + barra + una fila de datos.

    Sin caja de fondo -- eso es todo el punto de este perfil.
    """
    w(id=f"{clave}-tit", type="label", text=titulo, x=MARGEN, y=y,
      font="eyebrow", color=TINTA)
    if nota:
        metrica_nota, fmt_nota = nota
        w(id=f"{clave}-nota", type="text", metric=metrica_nota, x=ANCHO - MARGEN,
          y=y + 4, font="micro", color=ROTULO, format=fmt_nota, align="right")
    w(id=f"{clave}-pct", type="text", metric=metrica_pct, x=ANCHO - MARGEN,
      y=y + 24, font="big", color=TINTA, format="{:.0f}%", align="right")
    w(id=f"{clave}-barra", type="bar", metric=metrica_pct, x=MARGEN, y=y + 86,
      w=ANCHO - 2 * MARGEN, h=8, min=0, max=100, fill=NARANJA, track="#241812")
    # Las filas van centradas en su columna: alinear a la izquierda deja los
    # numeros bailando segun cuantos digitos tenga cada uno.
    ancho_col = (ANCHO - 2 * MARGEN) // max(1, len(filas))
    for i, (rotulo, metrica, fmt, extra) in enumerate(filas):
        cx = MARGEN + ancho_col * i + ancho_col // 2
        w(id=f"{clave}-r{i}", type="label", text=rotulo, x=cx, y=y + 106,
          font="micro", color=ROTULO, align="center")
        w(id=f"{clave}-v{i}", type="text", metric=metrica, x=cx, y=y + 122,
          font="dato", color=TINTA, format=fmt, align="center", **(extra or {}))


# --- cabecera ---
w(id="hora", type="text", metric="clock.time_hms", x=CENTRO, y=22, font="hero",
  color=TINTA, format="{}", align="center")
w(id="fecha", type="text", metric="clock.date", x=CENTRO, y=104, font="micro",
  color=ROTULO, format="{}", align="center")
separador(136)

# --- secciones, repartidas con aire: el fondo se ve entre una y otra ---
bloque("cpu", "CPU", 176, "cpu.load", ("cpu.name_short", "{}"), [
    ("TEMP", "cpu.temp", "{:.0f}°", None),
    ("CLOCK", "cpu.clock", "{:.0f}", None),
    ("POWER", "cpu.power", "{:.0f}W", None),
])
separador(340)

bloque("gpu", "GPU", 380, "gpu.load", ("gpu.name", "{}"), [
    ("TEMP", "gpu.temp", "{:.0f}°", None),
    ("VRAM", "gpu.vram", "{:.0f}%", None),
    ("POWER", "gpu.power", "{:.0f}W", None),
])
separador(544)

bloque("mem", "MEMORIA", 584, "mem.load", ("mem.speed", "{:.0f} MT/s"), [
    ("EN USO", "mem.used", "{:.1f} GiB", None),
    ("TOTAL", "mem.total", "{:.0f} GiB", None),
])
separador(748)

# --- red: sin porcentaje, dos tasas y sus graficos ---
w(id="red-tit", type="label", text="RED", x=MARGEN, y=788, font="eyebrow",
  color=TINTA)
w(id="red-baja-r", type="label", text="BAJADA", x=MARGEN, y=822, font="micro",
  color=ROTULO)
w(id="red-baja", type="text", metric="net.down", x=ANCHO - MARGEN, y=814,
  font="dato", color=TINTA, format="{}", align="right", humanize="rate")
w(id="red-baja-g", type="graph", metric="net.down", x=MARGEN, y=846,
  w=ANCHO - 2 * MARGEN, h=34, min=0, color=NARANJA, track="#241812", samples=120)
w(id="red-sube-r", type="label", text="SUBIDA", x=MARGEN, y=900, font="micro",
  color=ROTULO)
w(id="red-sube", type="text", metric="net.up", x=ANCHO - MARGEN, y=892,
  font="dato", color=TINTA, format="{}", align="right", humanize="rate")
w(id="red-sube-g", type="graph", metric="net.up", x=MARGEN, y=924,
  w=ANCHO - 2 * MARGEN, h=34, min=0, color=NARANJA, track="#241812", samples=120)

# De 980 a 1480 no hay nada a proposito: son 500 px de fondo limpio, que es donde
# el video se ve entero. Un perfil para mirar el fondo no puede estar lleno.
separador(1000)
w(id="pie-r", type="label", text="ENCENDIDA", x=CENTRO - 46, y=1432, font="micro",
  color=ROTULO, align="center")
w(id="pie", type="text", metric="sys.uptime", x=CENTRO + 22, y=1432, font="micro",
  color=ROTULO, format="{}", align="center", humanize="duration")

perfil = {
    "version": 1,
    "name": "Embers",
    "designed_for": {"width": ANCHO, "height": 1480},
    # 30 y no 60: el video ya cuesta 15% de un nucleo de ffmpeg, y el salto
    # perceptual de 30 a 60 en este panel es chico.
    "panel": {"rotate": 180, "brightness": 100, "fps": 30, "jpeg_quality": 88},
    "fonts": FUENTES,
    "background": {"type": "video", "src": "embers.mp4", "fit": "cover",
                   "fps": 30, "color": "#07080B"},
    "widgets": widgets,
}


def main() -> int:
    errores = schema.validate(perfil)
    if errores:
        for e in errores:
            print(f"  {e}")
        return 1
    destino = RAIZ / "vmaxpanel" / "profiles" / "embers.json"
    loader.save_raw(perfil, destino)
    print(f"listo: {destino} ({len(widgets)} widgets, "
          f"{destino.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
