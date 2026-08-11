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


def format_value(w: model.TextWidget, value) -> str:
    """Aplica w.format, o deja "--" conservando el sufijo del template."""
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


_DISPATCH = {
    "text": _draw_text, "label": _draw_label, "bar": _draw_bar,
    "arc": _draw_arc, "graph": _draw_graph, "image": _draw_image,
}
