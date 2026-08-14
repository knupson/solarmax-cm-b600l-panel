"""Composes the panel frame: background + widgets.

One single renderer for the engine and for the editor. With two implementations
they would diverge and the editor preview would end up lying.

The scale is uniform (the smaller of the two axes) and applies to font size as
well: scaling the axes independently would distort the text. When the real
panel's aspect ratio differs from `designed_for`, the scaled content (the
widgets) ends up smaller than the target canvas on one axis; that slack is split
in half to centre it rather than piling everything into a corner -- which is
what the design doc says and what test_scale_uses_the_smaller_axis_and_centers
checks.
"""
import io
import math
from collections import defaultdict, deque
from pathlib import Path

from PIL import Image

from ..layout.model import Size
from . import widgets as W
from .background import BackgroundSource
from .fonts import FontResolver

DEFAULT_ASSETS = Path(__file__).resolve().parent.parent / "assets"

ROTATIONS = {
    0: None,
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


def _finite_number(v):
    """A number usable for the history. Same rule as widgets._num(): it rejects
    bool (isinstance(True, int) is True) and NaN/Inf. A failed sensor pushing a
    nan must not sit in the ring buffer as if it were real data -- a future graph
    widget averaging the series instead of plotting it point by point, as
    widgets._draw_graph does today, would be poisoned by a single nan.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return math.isfinite(v)


def _aviso_fuente(font, resolver) -> str:
    """The warning for a missing family, saying WHAT it was drawn with instead.

    "X is missing" makes the user guess what they are looking at; "X is missing,
    Y was used" tells them exactly what they see. And if the profile's fallback
    chain did not help either, the warning says so, because then what is on screen
    is PIL's default font and it looks nothing like what was asked for.
    """
    usada = resolver.substitutions().get(font.family)
    if usada:
        return f"font not found: {font.family} (used {usada})"
    if font.fallbacks:
        return (f"font not found: {font.family}, nor "
                f"{', '.join(font.fallbacks)}")
    return f"font not found: {font.family}"


class History:
    """A sliding window per metric, for graph widgets."""

    def __init__(self, maxlen: int = 320):
        self.maxlen = maxlen
        self._d = defaultdict(lambda: deque(maxlen=maxlen))

    def push(self, sample: dict):
        for mid, v in sample.items():
            if _finite_number(v):
                self._d[mid].append(v)

    def series(self) -> dict:
        return {k: list(v) for k, v in self._d.items()}


class Renderer:
    """The project's only renderer: the engine uses it, and so does the editor
    for its live preview. Both have to see exactly the same thing, so this class
    cannot have a second implementation anywhere else.

    `set_panel_size(panel_size)` changes only the real panel size without touching
    the active layout (it recalculates everything by delegating to set_layout()).
    It is public API, meant for an editor that keeps the layout fixed and tries
    different panel sizes.
    """

    def __init__(self, layout, panel_size: Size | None = None, assets_dir=DEFAULT_ASSETS):
        self.assets_dir = Path(assets_dir)
        self._fonts = FontResolver()
        self._panel_size = panel_size
        self.set_layout(layout)

    def set_layout(self, layout) -> None:
        """Replaces the active layout. Wholesale on purpose: it recalculates the
        scale and the centring offset, and rebuilds the cached background, so no
        state from the previous layout stays mixed in with the new one
        (BackgroundSource does not notice changes on its own -- the comment in
        background.py says explicitly that the owner is the one who has to discard
        it and build a new one).
        """
        self.layout = layout
        d = layout.designed_for
        target = self._panel_size or d
        self.scale = min(target.width / d.width, target.height / d.height)

        # self.size is the real size of the canvas the panel expects (it never
        # depends on scale rounding): if target already comes from an integer, size
        # has to match that exact integer, not round(d.width * scale), which could
        # end up 1 px short or long from floating-point drift (scale = min(...)
        # does not always reproduce target/d exactly when multiplied back).
        self.size = Size(int(target.width), int(target.height))

        # The scaled content (the widgets) inside that canvas. round(), not int():
        # the same reason _fit() in background.py uses round() for cover -- a scale
        # that does not land exactly can give 199.9999999999997, and truncating
        # leaves a 1 px edge uncovered. It is clamped to the canvas size in case
        # rounding pushes 1 px too far.
        cw = min(self.size.width, max(1, round(d.width * self.scale)))
        ch = min(self.size.height, max(1, round(d.height * self.scale)))
        self._content_size = (cw, ch)
        self._offset = ((self.size.width - cw) // 2, (self.size.height - ch) // 2)
        # A real shortcut: when the content fills the whole canvas (the common
        # case -- panel_size None, or with the same aspect ratio as designed_for)
        # the intermediate RGBA layer for centring is not needed.
        self._exact_fit = self._content_size == (self.size.width, self.size.height)

        # The previous background is closed BEFORE creating the new one: a video
        # background has an ffmpeg behind it, and set_layout() is the hot-reload
        # path, so without this every profile edit would add a process decoding
        # for nobody.
        self._cerrar_fondo()
        self._bg = BackgroundSource(layout.background, self.size, self.assets_dir)
        # Forces the background build now, not on the first frame(): _build() is
        # what adds the degraded-background warnings (missing asset, and so on),
        # and BackgroundSource caches them forever after the first frame() (see its
        # docstring). Without this, warnings() called before any frame() would see
        # an empty background list even though the background DOES have a real
        # problem. The cost is one extra build plus one copy per layout change, not
        # per frame.
        self._bg.frame()

        # Warms the resolver with the fonts this layout is going to use, at the
        # scale this renderer has fixed. warnings() no longer depends on this
        # warm-up (see below for why), so this is purely an optimisation: it avoids
        # paying the font load -- opening the file, parsing the TTF -- inside the
        # first frame() instead of here, where at 1-10 fps it is far less
        # noticeable than inside the render loop.
        for font in layout.fonts.values():
            self._fonts.resolve(font, self.scale)

    def _cerrar_fondo(self) -> None:
        """Closes the active background if there is one.

        getattr rather than self._bg directly: __init__ calls set_layout(), so the
        first pass comes through here before the attribute exists. And it swallows
        exceptions because this runs on the hot-reload path: a background that
        refuses to close must not stop the new layout from coming in.
        """
        bg = getattr(self, "_bg", None)
        if bg is None:
            return
        try:
            bg.close()
        except Exception:
            pass

    def close(self) -> None:
        """Releases the background's resources. The renderer does not draw any more,
        but warnings() keeps answering: it is what the tray paints when the menu
        opens, from a DIFFERENT thread than the one bringing the engine down. "The
        engine closed exactly when you opened the menu" cannot be an exception, and
        the warnings from the background that was closed are precisely why it ended
        up this way.
        """
        bg = getattr(self, "_bg", None)
        if bg is not None:
            self._avisos_fondo = list(bg.warnings)
        self._cerrar_fondo()
        self._bg = None

    def set_panel_size(self, panel_size: Size | None) -> None:
        self._panel_size = panel_size
        self.set_layout(self.layout)

    def warnings(self) -> list[str]:
        """Degraded background, missing fonts and unreadable font directories.

        The missing fonts are recalculated by DERIVING them from
        `self.layout.fonts` on every call, rather than reading an accumulated
        `FontResolver.missing()`. missing() only records what an earlier resolve()
        actually saw missing -- and `resolve()` returns straight from the cache on
        a hit, without passing through there again -- so a long-lived FontResolver
        (this Renderer's, reused across successive set_layout() calls) can have a
        family cached from an earlier round and stay silent about it the next time,
        even though it is still missing and the active layout still names it.
        is_available() does not have that problem: it is a pure query against the
        font index, not a history of resolve() calls, so it gives the same answer
        the first time it is asked or the hundredth. With this, no state of its own
        about "which layout asked for what" is needed, and none has to be reset
        between set_layout() calls -- that state was, in two separate review
        rounds, the source of one warning that outlived its cause and one that
        disappeared too early.
        """
        # Deduplicated by casing: two aliases asking for the same family written
        # differently ("Arial" and "ARIAL") produced two identical lines for the
        # user. The first form seen is kept -- the one the layout wrote -- so the
        # warning matches what the user typed.
        missing = {}
        for f in self.layout.fonts.values():
            if not self._fonts.is_available(f.family):
                missing.setdefault(f.family.lower(), f)
        avisos_fondo = (list(self._bg.warnings) if self._bg is not None
                        else list(getattr(self, "_avisos_fondo", [])))
        return (avisos_fondo
                + [_aviso_fuente(f, self._fonts)
                   for f in sorted(missing.values(), key=lambda x: x.family.lower())]
                + [f"unreadable font directory: {d}"
                   for d in sorted(self._fonts.unreadable_dirs())])

    def frame(self, sample: dict, history: dict | None = None) -> Image.Image:
        img = self._bg.frame()
        ctx = W.DrawCtx(fonts=self._fonts, layout=self.layout, scale=self.scale,
                        assets_dir=self.assets_dir, history=history or {})

        if self._exact_fit:
            # Common case: the content already fills the canvas, so it is drawn
            # straight onto the copy of the background and there is no extra RGBA
            # layer to pay for per frame. At 1 fps it does not show; at the ~10 fps
            # animated backgrounds want from this same Renderer, avoiding a
            # needless allocation and composite per frame does matter.
            target = img
        else:
            target = Image.new("RGBA", self._content_size, (0, 0, 0, 0))

        for w in self.layout.widgets:
            metric = getattr(w, "metric", None)
            value = sample.get(metric) if metric else None
            W.draw(target, w, value, ctx)

        if not self._exact_fit:
            img.paste(target, self._offset, target)
        return img


def to_jpeg(img: Image.Image, rotate: int = 0, quality: int = 82) -> bytes:
    """A raw baseline 4:2:0 JPEG: exactly what the panel expects -- it starts at
    FFD8FF and ends at FFD9, with no container around it.

    The panel this was written against is mounted upside down, hence the
    rotate=180 in the profile. In another case it may be 0: the rotation is a
    parameter, never an assumption of this module.

    subsampling=2 is 4:2:0 in Pillow's convention (0=4:4:4, 1=4:2:2, 2=4:2:0).
    progressive=False is passed explicitly -- it is Pillow's default, but saying
    it by hand documents that the format has to stay baseline (progressive has a
    different byte order and the panel does not understand it) rather than
    silently depending on that default never changing.
    """
    if rotate not in ROTATIONS:
        raise ValueError(f"invalid rotate {rotate!r}, expected one of "
                          f"{sorted(ROTATIONS)}")
    transpose = ROTATIONS[rotate]
    if transpose is not None:
        img = img.transpose(transpose)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=2, progressive=False)
    return buf.getvalue()
