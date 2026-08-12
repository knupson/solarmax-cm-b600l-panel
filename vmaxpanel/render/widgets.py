"""Dibujo de cada tipo de widget.

Todo recibe `scale` y nadie asume la geometria del panel: un layout disenado
para 320x1480 se dibuja igual en otro tamano.

Un valor ausente se dibuja como "--". UNAVAILABLE (nadie sirve la metrica) y
None (el provider no trajo dato esta vuelta) se ven igual en el panel; el
editor los distingue consultando el registry.
"""
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from ..layout import model
from ..layout.schema import safe_asset_path
from ..metrics import UNAVAILABLE, spec_for

DASH = "--"


@dataclass
class DrawCtx:
    fonts: object
    layout: model.Layout
    scale: float = 1.0
    assets_dir: Path = Path(".")
    history: dict = field(default_factory=dict)


def _num(value):
    """Numero utilizable para una fraccion de barra/arco/grafico o para
    evaluar una regla de color. Rechaza bool (isinstance(True, int) es
    True en Python) y tambien NaN/Inf: un sensor fallado que devuelve nan
    no puede tratarse como "un numero cualquiera", porque
    max(0.0, min(1.0, nan)) da 1.0 por como Python compara con NaN, y una
    lectura basura terminaria dibujandose como si fuera un 100% real.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def human_rate(bps) -> str:
    """1258291 -> "1.2 MB/s": paridad con el human_rate() de daemon/panel.py,
    que un template de str.format no puede reproducir (no hay forma de
    elegir la unidad segun la magnitud dentro de un unico campo)."""
    if bps >= 1048576:
        return f"{bps / 1048576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def human_bytes(b) -> str:
    """3221225472 -> "3.0 GiB": mismo problema que human_rate() pero en
    unidades binarias de almacenamiento en vez de una tasa por segundo."""
    for unit, div in (("GiB", 1073741824), ("MiB", 1048576), ("KiB", 1024)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b:.0f} B"


HUMANIZERS = {"rate": human_rate, "bytes": human_bytes}


def format_value(w: model.TextWidget, value) -> str:
    """Aplica humanize si corresponde, si no w.format.

    Un valor ausente deja "--" (ver _dashed): con humanize activo no hay
    template del que conservar un sufijo, asi que se devuelve DASH a secas.
    Un modo de humanize desconocido (una defensa extra: schema.validate()
    ya lo rechaza, pero format_value no vuelve a validar) cae de vuelta al
    formato normal en vez de fallar.
    """
    humanizer = HUMANIZERS.get(getattr(w, "humanize", "none"))
    if humanizer is not None:
        v = _num(value)
        return DASH if v is None else humanizer(v)
    if value is None or value is UNAVAILABLE:
        return _dashed(w.format)
    try:
        return w.format.format(value)
    except Exception:
        return _dashed(w.format)


def _dashed(fmt: str) -> str:
    """"{:.0f} MHz" -> "-- MHz": reemplaza el campo por DASH sin perder el
    resto del template."""
    try:
        return fmt.format(_Dash())
    except Exception:
        return DASH


class _Dash:
    """Sustituto que se formatea a si mismo como "--" sin importar el
    format_spec (ancho, alineacion, precision, tipo de presentacion).

    __repr__ tiene que devolver DASH tambien: una conversion "{!r}" o
    "{!s}" en el template llama a repr()/str() ANTES de invocar a
    __format__ (object.__str__ ademas delega a __repr__, asi que definir
    uno solo ya cubre !r, !s y !a). Sin esto, un layout compartido con un
    format como "{!r} MHz" filtraria el repr default de Python
    ("<...widgets._Dash object at 0x...>") al panel en vez de "-- MHz".
    """

    def __format__(self, spec):
        return DASH

    def __repr__(self):
        return DASH


def color_for(w: model.TextWidget, value) -> str:
    v = _num(value)
    if v is not None:
        for rule in w.rules:
            if rule.matches(v):
                return rule.color
    return w.color


def draw(img: Image.Image, w: model.Widget, value, ctx: DrawCtx) -> None:
    fn = _DISPATCH.get(w.type)
    if fn is None:
        return
    fn(img, ImageDraw.Draw(img), w, value, ctx)


def _s(ctx, v):
    return int(round(v * ctx.scale))


def _size(ctx, v):
    """Escala un ancho/alto/radio y nunca deja pasar un valor negativo. El
    validador de layouts solo exige que w/h/r/radius sean enteros, no que
    sean positivos; una caja con la esquina invertida (x1 < x0) hace que
    Pillow tire ValueError en vez de simplemente no dibujar nada.
    """
    return max(0, _s(ctx, v))


def _span(ctx, v):
    """Como _size(), pero un lado positivo nunca se escala hasta desaparecer.

    A escala < 1 (un panel mas chico que designed_for) un separador de h=1
    redondea a 0 px y la linea se pierde sin que nada avise: el layout se ve
    distinto y no hay error en ningun lado. Un lado <= 0 sigue dando 0, que
    es como el llamador sabe que no hay nada que dibujar.
    """
    if v <= 0:
        return 0
    return max(1, _s(ctx, v))


_ANCHORS = {"left": "la", "center": "ma", "right": "ra"}


def _draw_text(img, g, w, value, ctx):
    font = ctx.fonts.resolve(ctx.layout.fonts[w.font], ctx.scale)
    text = format_value(w, value)
    g.text((_s(ctx, w.x), _s(ctx, w.y)), text, font=font,
           fill=color_for(w, value), anchor=_ANCHORS.get(w.align, "la"))


def _draw_label(img, g, w, value, ctx):
    font = ctx.fonts.resolve(ctx.layout.fonts[w.font], ctx.scale)
    g.text((_s(ctx, w.x), _s(ctx, w.y)), w.text, font=font, fill=w.color,
           anchor=_ANCHORS.get(w.align, "la"))


def _range(w):
    """Rango efectivo (lo, hi) de un widget numerico.

    Si el widget no fija min/max, se completa con el spec de la metrica.
    Si ni el widget ni el spec fijan un extremo -- p.ej. net.down, cuyo
    spec declara max=None porque no tiene techo natural -- ese extremo
    queda en None: es un limite explicitamente abierto, no "no se
    especifico nada". Solo cuando la metrica no tiene spec conocido (o ya
    quedaron ambos extremos resueltos) se aplica el ultimo recurso 0..100.
    Inventarle un 100.0 a un extremo abierto haria que cualquier lectura
    real (miles de B/s en una bajada de red) clampeara siempre a full,
    mintiendo igual que el %CPU saturado de LCD Control que este proyecto
    ya dejo documentado.
    """
    lo, hi = w.min, w.max
    spec = spec_for(w.metric) if (lo is None or hi is None) else None
    if spec is not None:
        if lo is None:
            lo = spec.min
        if hi is None:
            hi = spec.max
    else:
        lo = 0.0 if lo is None else lo
        hi = 100.0 if hi is None else hi
    return lo, hi


def _fraction(w, value):
    v = _num(value)
    if v is None:
        return None
    lo, hi = _range(w)
    if lo is None or hi is None or hi <= lo:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _draw_bar(img, g, w, value, ctx):
    x, y = _s(ctx, w.x), _s(ctx, w.y)
    ww, hh = _size(ctx, w.w), _size(ctx, w.h)
    radius = _size(ctx, w.radius)
    g.rounded_rectangle([x, y, x + ww, y + hh], radius=radius, fill=w.track)
    frac = _fraction(w, value)
    if frac is None:
        return
    fw = int(ww * frac)
    if fw > 2:
        g.rounded_rectangle([x, y, x + fw, y + hh], radius=radius, fill=w.fill)


def _draw_arc(img, g, w, value, ctx):
    r, t = _size(ctx, w.r), max(1, _s(ctx, w.thickness))
    cx, cy = _s(ctx, w.x), _s(ctx, w.y)
    box = [cx - r, cy - r, cx + r, cy + r]
    g.arc(box, w.start_angle, w.start_angle + w.sweep, fill=w.track, width=t)
    frac = _fraction(w, value)
    if frac:
        g.arc(box, w.start_angle, w.start_angle + w.sweep * frac, fill=w.fill, width=t)


def _draw_graph(img, g, w, value, ctx):
    x, y = _s(ctx, w.x), _s(ctx, w.y)
    ww, hh = _size(ctx, w.w), _size(ctx, w.h)
    if w.track:
        g.rectangle([x, y, x + ww, y + hh], fill=w.track)
    series = list(ctx.history.get(w.metric) or [])[-w.samples:]
    if len(series) < 2:
        return
    lo, hi = _range(w)
    if lo is None or hi is None or hi <= lo:
        return                          # rango sin resolver: no hay como escalar
    span = hi - lo
    step = ww / (len(series) - 1)
    pts = []
    for i, v in enumerate(series):
        n = _num(v)
        frac = 0.0 if n is None else max(0.0, min(1.0, (n - lo) / span))
        pts.append((x + i * step, y + hh - frac * hh))
    g.line(pts, fill=w.color, width=max(1, _s(ctx, 2)))


def _draw_image(img, g, w, value, ctx):
    src = safe_asset_path(w.src)
    if src is None:
        return
    path = Path(ctx.assets_dir) / src
    try:
        asset = Image.open(path).convert("RGBA")
    except Exception:
        return                          # asset faltante o corrupto: se omite el widget
    dims = (max(1, _s(ctx, w.w)), max(1, _s(ctx, w.h)))
    resized = asset.resize(dims, Image.LANCZOS)
    img.paste(resized, (_s(ctx, w.x), _s(ctx, w.y)), resized)


def _draw_rect(img, g, w, value, ctx):
    """Divisores y marcos.

    `w`/`h` son el tamano real en pixeles, de ahi el `- 1` en la esquina
    opuesta: la caja de Pillow es inclusive, asi que [x, y, x+w, y+h]
    dibujaria un pixel de mas por lado. bar/graph tienen esa caja de mas
    desde fase 1 y se quedan como estan -- corregirlos moveria el perfil y
    los goldens 1 px sin ganancia --, pero un separador de h=1 no puede
    darse el lujo: se veria al doble del grosor pedido.
    """
    ww, hh = _span(ctx, w.w), _span(ctx, w.h)
    if ww == 0 or hh == 0:
        return
    x, y = _s(ctx, w.x), _s(ctx, w.y)
    box = [x, y, x + ww - 1, y + hh - 1]
    # Pillow rechaza un radio mayor que la mitad del lado menor y el
    # validador solo exige que radius sea entero, asi que se clampea aca.
    radius = min(_size(ctx, w.radius), min(ww, hh) // 2)
    if w.stroke:
        outline, width = w.stroke, max(1, _s(ctx, w.stroke_width))
    else:
        outline, width = None, 0
    if outline is None and not w.fill:
        return                              # nada que dibujar; validate() ya lo rechaza
    if radius > 0:
        g.rounded_rectangle(box, radius=radius, fill=w.fill,
                            outline=outline, width=width)
    else:
        g.rectangle(box, fill=w.fill, outline=outline, width=width)


_DISPATCH = {
    "text": _draw_text, "label": _draw_label, "bar": _draw_bar,
    "arc": _draw_arc, "graph": _draw_graph, "image": _draw_image,
    "rect": _draw_rect,
}
