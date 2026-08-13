"""Drawing each widget type.

Formatting the value (template, humanize, colour rules) lives in text_format.py:
it depends on neither PIL nor fonts and reads without any drawing at all. What is
left here is only what paints pixels.

Everything receives `scale` and nothing assumes the panel geometry: a layout
designed for 320x1480 draws the same at another size.

An absent value is drawn as "--". UNAVAILABLE (nobody serves the metric) and None
(the provider brought no data this round) look the same on the panel; the editor
tells them apart by asking the registry.
"""
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from ..layout import model
from ..layout.schema import safe_asset_path
from ..metrics import spec_for
# Re-exported: text formatting lives in text_format.py (which does not depend on
# PIL), but anybody importing widgets.format_value or widgets.human_rate keeps
# working.
from .text_format import (DASH, HUMANIZERS, _dashed, _Dash, _num,  # noqa: F401
                          color_for, format_value, human_bytes,
                          human_duration, human_rate)

@dataclass
class DrawCtx:
    """What a draw needs besides the widget and its value.

    `fonts` is a render.fonts.FontResolver and `history` is
    {metric_id: [samples]} as renderer.History.series() returns it; they are
    loosely annotated (object/dict) because annotating them properly would create a
    circular import with renderer.py, which imports this module.
    """

    fonts: object                  # render.fonts.FontResolver
    layout: model.Layout
    scale: float = 1.0
    assets_dir: Path = Path(".")
    history: dict = field(default_factory=dict)   # {metric_id: [float, ...]}


def draw(img: Image.Image, w: model.Widget, value, ctx: DrawCtx) -> None:
    fn = _DISPATCH.get(w.type)
    if fn is None:
        return
    fn(img, ImageDraw.Draw(img), w, value, ctx)


def _s(ctx, v):
    return int(round(v * ctx.scale))


def _size(ctx, v):
    """Scales a width/height/radius and never lets a negative value through. The
    layout validator only requires that w/h/r/radius be integers, not that they be
    positive; a box with an inverted corner (x1 < x0) makes Pillow raise ValueError
    instead of simply drawing nothing.
    """
    return max(0, _s(ctx, v))


def _span(ctx, v):
    """Like _size(), but a positive side never scales away to nothing.

    At a scale < 1 (a panel smaller than designed_for) a separator of h=1 rounds to
    0 px and the line is lost with nothing warning about it: the layout looks
    different and there is no error anywhere. A side <= 0 still gives 0, which is
    how the caller knows there is nothing to draw.
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
    """The effective (lo, hi) range of a numeric widget.

    If the widget does not set min/max, it is filled in from the metric spec. If
    neither the widget nor the spec sets an end -- net.down, for instance, whose
    spec declares max=None because it has no natural ceiling -- that end stays
    None: it is an explicitly open bound, not "nothing was specified". Only when
    the metric has no known spec (or both ends are already resolved) does the
    0..100 last resort apply. Inventing a 100.0 for an open end would make any real
    reading (thousands of B/s on a network download) clamp to full every time,
    lying exactly the way LCD Control's saturated CPU% did, which is the thing this
    project was written to stop doing.
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
    if not series:
        # No history yet -- the first frame of a run, and the only case for --save,
        # which draws one and exits -- but the current value already arrived as an
        # argument. Using it is the same as having one sample.
        actual = _num(value)
        if actual is None:
            return
        series = [actual]
    if len(series) == 1:
        # One sample is the normal case for the first frame of any run, and for
        # --save, which draws one and exits. It used to fall into the same return as
        # "no history" and left an empty box, which reads as a broken widget. It is
        # duplicated so a flat line comes out at that height: the same convention
        # the rest of this function already uses, stretching whatever samples exist
        # across the width of the box.
        series = series * 2
    lo, hi = _range(w)
    if lo is None or hi is None or hi <= lo:
        return                          # unresolved range: nothing to scale against
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
        return                          # missing or corrupt asset: the widget is skipped
    dims = (max(1, _s(ctx, w.w)), max(1, _s(ctx, w.h)))
    resized = asset.resize(dims, Image.LANCZOS)
    img.paste(resized, (_s(ctx, w.x), _s(ctx, w.y)), resized)


def _draw_rect(img, g, w, value, ctx):
    """Divisores y marcos.

    `w`/`h` are the real size in pixels, hence the `- 1` on the opposite corner:
    Pillow's box is inclusive, so [x, y, x+w, y+h]
    dibujaria un pixel de mas por lado. bar/graph tienen esa caja de mas
    from the start and are left as they are -- fixing them would move the profiles
    and the goldens by 1 px for no gain -- but a separator of h=1 cannot afford
    that: it would appear at twice the thickness asked for.
    """
    ww, hh = _span(ctx, w.w), _span(ctx, w.h)
    if ww == 0 or hh == 0:
        return
    x, y = _s(ctx, w.x), _s(ctx, w.y)
    box = [x, y, x + ww - 1, y + hh - 1]
    # Pillow rejects a radius larger than half the shorter side, and the validator
    # only requires that radius be an integer, so it is clamped here.
    radius = min(_size(ctx, w.radius), min(ww, hh) // 2)
    if w.stroke:
        outline, width = w.stroke, max(1, _s(ctx, w.stroke_width))
    else:
        outline, width = None, 0
    if outline is None and not w.fill:
        return                              # nothing to draw; validate() already rejects it
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
