"""Backgrounds: solid, gradient and image (static), procedural, sequence and
video (animated).

`video` delegates to ffmpeg as an external process (see render/video.py) and
degrades to a flat colour -- with a warning saying how to install it -- when it is
absent: a shared profile using video has to keep opening on a machine without
ffmpeg.

The static ones are cached because they do not change between frames while the
layout stays the same; the render loop only copies the cache and draws the widgets
on top. The animated ones compute each frame from the clock, which is INJECTED: a
background depending on time.monotonic() directly cannot be tested
deterministically.

Whoever builds a BackgroundSource is the one who has to discard it and create a
new one if the layout (or the size) changes: this class does not notice those
changes on its own, there is no automatic invalidation.

Costs measured in the throughput spike (real profile, 320x1480, a 16.7 ms budget
per frame at 60 fps): rebuilt gradient 7.7 ms, sequence 2.8 ms, procedural scroll
0.5 ms.
"""
import math
import time
from pathlib import Path

from PIL import Image, ImageEnhance

from ..layout.schema import safe_asset_path
from .video import VideoSource

FALLBACK = (10, 12, 16)
ANIMADOS = {"procedural", "sequence", "video"}
PROCEDURALES = ("scroll", "pulse")
EXT_CUADROS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")


def parse_hex(color, default=FALLBACK):
    """Converts "#RRGGBB" to (r, g, b). Returns `default`, silently, for anything
    that does not match.

    validate() in layout/schema.py requires #RRGGBB for 'solid' and for every
    stop of 'gradient' via _check_color, but NOT for bg.color on
    'image'/'sequence'/'video' (BACKGROUND_KEYS allows it as a key without
    validating it): a broken colour there really can reach this point from a shared
    layout. The silent default is what keeps that validation gap from raising an
    exception instead of, at worst, painting the letterbox in a colour that is not
    the one asked for.
    """
    if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
        return default
    try:
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return default


class BackgroundSource:
    def __init__(self, bg, size, assets_dir=".", clock=None):
        self.bg = bg
        self.size = (size.width, size.height)
        self.assets_dir = Path(assets_dir)
        self.warnings: list[str] = []
        self._cache = None
        self._tira = None          # the gradient and its mirror, for the scroll
        self._cuadros = None       # a sequence's paths, read only once
        self._video = None         # the video background's ffmpeg, if there is one
        # monotonic and not time(): a system clock adjustment -- or a daylight
        # saving change -- must not make the animation jump backwards.
        self._clock = clock or time.monotonic

    @property
    def animated(self) -> bool:
        return self.bg.type in ANIMADOS

    def frame(self) -> Image.Image:
        """Returns a copy of the background. A copy, not the original: whoever
        receives the frame draws widgets on top of it, and if that mutated the
        cache the next frame would start with the previous one's leftovers."""
        if self.animated:
            return self._animado(self._clock())
        if self._cache is None:
            self._cache = self._build()
        return self._cache.copy()

    # --- animados ---

    def _animado(self, t) -> Image.Image:
        if self.bg.type == "video":
            return self._video_frame()
        if self.bg.type == "sequence":
            return self._sequence(t)
        if self.bg.name == "scroll":
            return self._scroll(t)
        if self.bg.name == "pulse":
            return self._pulse(t)
        self._avisar(f"unknown procedural generator {self.bg.name!r}; falling back "
                     f"to a flat colour")
        return self._solid()

    def _avisar(self, texto):
        """Warn without duplicating.

        An animated background recomputes up to 60 times a second: if every pass
        added its warning, warnings() would grow without bound and the tray would
        show the same text a thousand times.
        """
        if texto not in self.warnings:
            self.warnings.append(texto)

    def _tira_doble(self) -> Image.Image:
        """The gradient and its mirror stacked: twice the panel height.

        This is what makes the scroll close without a jump. With a single copy, on
        wrapping around the last colour collides with the first and there is a
        visible jolt every cycle; with the mirror the journey is continuous in both
        directions.
        """
        if self._tira is None:
            base = self._gradient()
            ancho, alto = self.size
            tira = Image.new("RGB", (ancho, alto * 2))
            tira.paste(base, (0, 0))
            tira.paste(base.transpose(Image.Transpose.FLIP_TOP_BOTTOM), (0, alto))
            self._tira = tira
        return self._tira

    def _scroll(self, t) -> Image.Image:
        tira = self._tira_doble()
        ancho, alto = self.size
        desp = int(round((self.bg.speed or 0.0) * t)) % (alto * 2)
        if desp + alto <= alto * 2:
            return tira.crop((0, desp, ancho, desp + alto))
        # The window ended up split between the end of the strip and its start.
        out = Image.new("RGB", self.size)
        primera = alto * 2 - desp
        out.paste(tira.crop((0, desp, ancho, alto * 2)), (0, 0))
        out.paste(tira.crop((0, 0, ancho, alto - primera)), (0, primera))
        return out

    def _pulse(self, t) -> Image.Image:
        """The gradient with its brightness breathing.

        The factor never drops below 0.55: a background that fades to black leaves
        the text floating in a void, and the point of a background is to accompany,
        not to compete.
        """
        if self._cache is None:
            self._cache = self._gradient()
        periodo = self.bg.period if self.bg.period and self.bg.period > 0 else 6.0
        fase = (t % periodo) / periodo
        k = 0.775 + 0.225 * math.cos(2 * math.pi * fase)
        return ImageEnhance.Brightness(self._cache).enhance(k)

    def _video_frame(self) -> Image.Image:
        """The last frame ffmpeg delivered, or a flat colour while there is none.

        Video does NOT use the injected clock: ffmpeg sets the pace, and it already
        paces its output at the file's natural rate. Asking it for a frame by
        timestamp would mean buffering the whole video or seeking per frame, and
        both are worse than letting the decoder do its job.
        """
        if self._video is None:
            seguro = safe_asset_path(self.bg.src) if self.bg.src else None
            if seguro is None:
                self._avisar(f"'video' background with an invalid path, or outside the "
                             f"assets directory: {self.bg.src!r}")
                return self._solid()
            self._video = VideoSource(self.assets_dir / seguro, self.size,
                                      fps=self.bg.fps).start()
        for aviso in self._video.warnings:
            self._avisar(aviso)
        img = self._video.frame()
        return img if img is not None else self._solid()

    def close(self):
        """Releases whatever this background has open.

        Today only a video's ffmpeg, but the method exists for all of them: the
        Renderer discards and recreates the BackgroundSource on every set_layout,
        which is to say on every hot reload, and without a close() each one would
        leave another ffmpeg decoding for nobody. It is exactly the orphan-process
        pattern this project already had with the sensor sidecar.
        """
        if self._video is not None:
            try:
                self._video.close()
            finally:
                self._video = None

    def _lista_cuadros(self):
        """The frames' paths, sorted and read only once.

        Only once because the set of frames cannot change between samples: the same
        reason the network adapters and the disk indices are fixed at start-up.
        """
        if self._cuadros is not None:
            return self._cuadros
        self._cuadros = []
        seguro = safe_asset_path(self.bg.src) if self.bg.src else None
        if seguro is None:
            self._avisar(f"'sequence' background with an invalid path, or outside the "
                         f"assets directory: {self.bg.src!r}")
            return self._cuadros
        try:
            self._cuadros = sorted(p for p in (self.assets_dir / seguro).iterdir()
                                   if p.suffix.lower() in EXT_CUADROS)
        except Exception as e:
            self._avisar(f"could not read the sequence {self.bg.src!r}: {e}")
            return self._cuadros
        if not self._cuadros:
            self._avisar(f"the sequence {self.bg.src!r} has no frames")
        return self._cuadros

    def _sequence(self, t) -> Image.Image:
        """Frame `int(t * fps) % n`, decoded on the spot.

        Decoded frames are deliberately NOT cached: at 320x1480 each one takes
        1.4 MB of RAM, so a 60-frame sequence would eat 85 MB to save the 2.8 ms
        decoding and scaling costs (measured in the spike). The file itself is
        already cached by the operating system.
        """
        cuadros = self._lista_cuadros()
        if not cuadros:
            return self._solid()
        fps = self.bg.fps if self.bg.fps and self.bg.fps > 0 else 10.0
        idx = int(t * fps) % len(cuadros)
        try:
            src = Image.open(cuadros[idx])
            src.load()
            return self._fit(src.convert("RGB"))
        except Exception as e:
            self._avisar(f"could not open frame {cuadros[idx].name!r}: {e}")
            return self._solid()

    # --- estaticos ---

    def _build(self) -> Image.Image:
        # Called only once (frame() caches the result), so the warnings added by
        # the branches below do not duplicate even if frame() is called many times.
        t = self.bg.type
        if t == "gradient":
            return self._gradient()
        if t == "image":
            return self._image()
        return self._solid()

    def _solid(self):
        return Image.new("RGB", self.size, parse_hex(self.bg.color))

    def _gradient(self):
        """A linear gradient between stops sorted by 'at'.

        Angle: it only distinguishes vertical from horizontal, not an arbitrary
        angle. `angle % 180` in [45, 135) is vertical; everything else is
        horizontal. There are no rotated diagonals.

        The sampled strip is 1 px on the axis perpendicular to the gradient, but it
        ALREADY has full resolution along the gradient axis (`n` is the real
        width/height, not 1). The final resize only stretches that perpendicular
        axis; since each row/column is a single colour, there is no banding and no
        intermediate stop is lost to interpolation.
        """
        stops = sorted(self.bg.stops, key=lambda s: s["at"])
        if len(stops) < 2:
            return self._solid()
        vertical = 45 <= (self.bg.angle % 180) < 135
        n = self.size[1] if vertical else self.size[0]
        line = Image.new("RGB", (1, n) if vertical else (n, 1))
        px = line.load()
        for i in range(n):
            c = self._sample(stops, i / max(1, n - 1))
            px[(0, i) if vertical else (i, 0)] = c
        return line.resize(self.size, Image.BILINEAR)

    @staticmethod
    def _sample(stops, t):
        if t <= stops[0]["at"]:
            return parse_hex(stops[0]["color"])
        if t >= stops[-1]["at"]:
            return parse_hex(stops[-1]["color"])
        for a, b in zip(stops, stops[1:]):
            if a["at"] <= t <= b["at"]:
                span = (b["at"] - a["at"]) or 1.0
                k = (t - a["at"]) / span
                ca, cb = parse_hex(a["color"]), parse_hex(b["color"])
                return tuple(int(round(ca[i] + (cb[i] - ca[i]) * k)) for i in range(3))
        return parse_hex(stops[-1]["color"])

    def _image(self):
        if not self.bg.src:
            self.warnings.append("'image' background with no src")
            return self._solid()
        # safe_asset_path() already ran in schema.build(), but BackgroundSource is
        # also instantiated directly with a hand-built Background (as in the tests).
        # Revalidating is the same defence in depth widgets._draw_image already
        # applies to w.src.
        safe_src = safe_asset_path(self.bg.src)
        if safe_src is None:
            self.warnings.append(
                f"'image' background with an invalid path: {self.bg.src!r}")
            return self._solid()
        path = self.assets_dir / safe_src
        try:
            src = Image.open(path).convert("RGB")
        except Exception as e:
            self.warnings.append(f"could not open the background {self.bg.src!r}: {e}")
            return self._solid()
        return self._fit(src)

    def _fit(self, src):
        tw, th = self.size
        if self.bg.fit == "stretch":
            return src.resize(self.size, Image.LANCZOS)
        sw, sh = src.size
        k = max(tw / sw, th / sh) if self.bg.fit == "cover" else min(tw / sw, th / sh)
        # round(), not int(): the governing axis should give sw*k == tw (or
        # sh*k == th) exactly, but in floating point it can land on
        # 199.99999999999997. int() truncates to 199 and "cover" leaves a 1 px edge
        # uncovered; round() fixes that without touching the already-exact cases.
        scaled = src.resize((max(1, round(sw * k)), max(1, round(sh * k))), Image.LANCZOS)
        out = Image.new("RGB", self.size, parse_hex(self.bg.color, (0, 0, 0)))
        out.paste(scaled, ((tw - scaled.width) // 2, (th - scaled.height) // 2))
        return out
