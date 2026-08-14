"""Our own layout validator.

It returns the complete list of errors in plain language, so the editor can show
them all at once. It does not use jsonschema: the messages stay tied to our own
model, and it is one less dependency to ship.

Layouts get shared between users, so a layout must NOT be able to execute
anything: the colour rules are hand-parsed comparisons, not expressions.
"""
import posixpath
import re
from string import Formatter

from ..metrics import is_metric
from .model import (ArcWidget, Background, BarWidget, Font, GraphWidget,
                    ImageWidget, LabelWidget, Layout, PanelCfg, RectWidget,
                    Rule, Size, TextWidget, Widget)

SUPPORTED_VERSION = 1

# The panel refresh rate. See the phase 2 throughput spike.
MAX_FPS = 60

WIDGET_TYPES = {
    "text": TextWidget, "label": LabelWidget, "bar": BarWidget,
    "arc": ArcWidget, "graph": GraphWidget, "image": ImageWidget,
    "rect": RectWidget,
}

BACKGROUND_TYPES = {"solid", "gradient", "image", "sequence", "video", "procedural"}
ALIGNS = {"left", "center", "right"}
FITS = {"cover", "contain", "stretch"}
ROTATIONS = {0, 90, 180, 270}
HUMANIZE_MODES = {"none", "rate", "bytes", "duration"}

# Derived from the model, not written by hand: adding a field to PanelCfg or Font
# used to make the validator reject valid layouts until somebody remembered to
# update the set. The widget key check already derived from __dataclass_fields__;
# this brings the rest in line.
PANEL_KEYS = set(PanelCfg.__dataclass_fields__)
FONT_KEYS = set(Font.__dataclass_fields__)

# Allowed keys per background type. "color" is accepted on solid (the fill) and on
# image/sequence/video (the letterbox fill); on gradient the model does not read it
# but build() accepts it without error, so it is let through rather than risking a
# false rejection.
BACKGROUND_KEYS = {
    "solid": {"type", "color"},
    "gradient": {"type", "stops", "angle", "color"},
    "image": {"type", "src", "fit", "color"},
    # sequence carries its own fps, independent of panel.fps: a 12-frames-per-second
    # animation looks equally smooth with the panel at 30 or at 60.
    "sequence": {"type", "src", "fit", "color", "fps"},
    "video": {"type", "src", "fit", "color", "fps"},
    # procedural starts from the gradient, so it shares stops/angle; name picks the
    # generator and speed/period parameterise it.
    "procedural": {"type", "name", "stops", "angle", "color", "speed", "period"},
}
PROCEDURALES = {"scroll", "pulse"}

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RULE_RE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

# Required fields beyond id/type/x/y
REQUIRED = {
    "text": ["metric", "font", "color", "format"],
    "label": ["text", "font", "color"],
    "bar": ["metric", "w", "h"],
    "arc": ["metric", "r"],
    "graph": ["metric", "w", "h"],
    "image": ["src", "w", "h"],
    "rect": ["w", "h"],
}


def safe_asset_path(src) -> str | None:
    """Normalises an asset path and rejects it if it escapes the directory.

    The engine runs elevated: without this, a '..\\..\\' makes it read any file
    on the machine.

    The same checks run twice: on the raw input and on the normalised result. A
    stray '..' can be consumed against a real preceding segment (e.g.
    'a/../C:/Windows/win.ini') and the drive letter is only exposed after
    normalising; checking the raw input alone lets that through.
    """
    if not isinstance(src, str) or not src.strip():
        return None
    s = src.replace("\\", "/")
    if _is_unsafe_normalized_path(s):
        return None
    norm = posixpath.normpath(s)
    if norm == "." or norm == ".." or norm.startswith("../"):
        return None
    if _is_unsafe_normalized_path(norm):
        return None
    return norm


# Windows devices: `open("CON")` opens the console and `open("COM1")` the serial
# port. This is not an escape from the assets directory -- which is why the path
# checks did not catch it -- but a read can block forever and hang the render
# thread. With sequence backgrounds that path is really opened, so it stopped
# being a theoretical problem.
_DISPOSITIVOS = {"con", "prn", "aux", "nul", "conin$", "conout$"}
_DISPOSITIVOS |= {f"com{i}" for i in range(1, 10)}
_DISPOSITIVOS |= {f"lpt{i}" for i in range(1, 10)}


def _es_dispositivo(segmento: str) -> bool:
    """True if the segment names a reserved device.

    The name is compared WITHOUT its extension: "NUL.jpg" is also the null device
    to the Windows file subsystem. A prefix is not enough -- "CONSOLAS.png" is a
    legitimate file -- so the comparison is exact on the base name.
    """
    base = segmento.split(".", 1)[0].strip().lower()
    return base in _DISPOSITIVOS


def _is_unsafe_normalized_path(s: str) -> bool:
    """True if `s` is absolute, UNC, a Windows drive path ('C:foo'), or if any
    segment names a reserved device."""
    if (s.startswith("/") or s.startswith("//")
            or re.match(r"^[A-Za-z]:", s) is not None or ":" in s):
        return True
    return any(_es_dispositivo(seg) for seg in s.split("/") if seg)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_fallbacks(errs, alias, v):
    """`fallbacks` has to be a list of usable family names.

    It is validated rather than accepted as-is because the error shows up far from
    its cause: an empty or numeric fallback breaks nothing at load time, and is only
    noticed as "the font was not substituted" on somebody else's machine.
    """
    if v is None:
        return
    if not isinstance(v, list):
        errs.append(f"fonts.{alias}.fallbacks: expected a list of family names")
        return
    for i, fam in enumerate(v):
        if not isinstance(fam, str) or not fam.strip():
            errs.append(f"fonts.{alias}.fallbacks[{i}]: {fam!r} is not a family name")


def _check_color(errs, where, v):
    if not isinstance(v, str) or not _COLOR_RE.match(v):
        errs.append(f"{where}: invalid color {v!r}, expected #RRGGBB")


def _check_format(errs, where, v):
    if not isinstance(v, str):
        errs.append(f"{where}: format must be text")
        return
    try:
        parsed = list(Formatter().parse(v))
    except ValueError as e:
        errs.append(f"{where}: invalid format {v!r}: {e}")
        return
    fields = [f for _, f, _, _ in parsed if f is not None]
    # Formatter().parse() only reports the top-level field: a field nested inside
    # the format_spec (e.g. "{0!r:>{1}}") would pass as "exactly one field" while
    # actually referencing a second positional argument that .format(value) does
    # not have, and that blows up in render, not in validate().
    if any(spec and "{" in spec for _, _, spec, _ in parsed):
        errs.append(f"{where}: format {v!r} cannot nest another replacement "
                    f"field inside the format_spec")
    if len(fields) != 1:
        errs.append(f"{where}: format {v!r} must have exactly one field, it has "
                    f"{len(fields)}")
    elif fields[0] not in ("", "0"):
        errs.append(f"{where}: format {v!r} cannot name the field "
                    f"({fields[0]!r}); use {{}} or {{0}}")


def _parse_rule(raw):
    if not isinstance(raw, dict):
        return None
    m = _RULE_RE.match(str(raw.get("when", "")))
    if not m:
        return None
    return Rule(m.group(1), float(m.group(2)), raw.get("color", "#FFFFFF"))


def _errores_de_stops(stops) -> list[str]:
    """Validates a gradient's stops. Shared between 'gradient' and 'procedural',
    which starts from the same gradient."""
    errs = []
    for i, s in enumerate(stops):
        if not isinstance(s, dict) or not _is_num(s.get("at")):
            errs.append(f"background.stops[{i}]: missing numeric at")
        elif not 0.0 <= s["at"] <= 1.0:
            errs.append(f"background.stops[{i}]: at out of 0..1")
        _check_color(errs, f"background.stops[{i}]", s.get("color")
                     if isinstance(s, dict) else None)
    return errs


def validate(raw) -> list[str]:
    errs: list[str] = []
    if not isinstance(raw, dict):
        return ["the layout must be a JSON object"]

    v = raw.get("version")
    if not _is_int(v):
        errs.append("version: missing or not an integer")
    elif v > SUPPORTED_VERSION:
        errs.append(f"version {v} is newer than the supported one ({SUPPORTED_VERSION}); "
                    f"update VMax Panel")
    elif v < 1:
        errs.append(f"invalid version {v}")

    if not isinstance(raw.get("name"), str) or not raw.get("name"):
        errs.append("name: missing or empty")

    df = raw.get("designed_for")
    if not isinstance(df, dict) or not _is_int(df.get("width")) or not _is_int(df.get("height")):
        errs.append("designed_for: expected integer width and height")
    elif df["width"] <= 0 or df["height"] <= 0:
        errs.append("designed_for: width and height must be positive")

    p = raw.get("panel")
    if not isinstance(p, dict):
        errs.append("panel: missing")
    else:
        for k in p:
            if k not in PANEL_KEYS:
                errs.append(f"panel: unknown key {k!r}")
        if p.get("rotate", 0) not in ROTATIONS:
            errs.append(f"panel.rotate: {p.get('rotate')!r} is invalid, "
                        f"expected 0, 90, 180 or 270")
        b = p.get("brightness", 100)
        if not _is_int(b) or not 0 <= b <= 100:
            errs.append(f"panel.brightness: {b!r} out of 0..100")
        f = p.get("fps", 1.0)
        # 60 is the panel's refresh rate. Above it the panel discards frames -- it
        # applies no backpressure, and accepts 227 fps of sustained writes without
        # slowing the host down -- so those would be frames rendered, compressed and
        # written for nothing. Measured against the real panel: 0.6% of one core at
        # 1 fps, 17% at 30, 37% at 60.
        # Fractional on purpose: 0.5 is one frame every two seconds, which on a data
        # panel is a legitimate cadence and the cheapest of all. The tray only offers
        # 1/10/30/60, but the profile has no reason to be limited to that menu.
        if not _is_num(f) or not 0.1 <= f <= MAX_FPS:
            errs.append(f"panel.fps: {f!r} out of 0.1..{MAX_FPS} "
                        f"(the panel refreshes at {MAX_FPS} Hz)")
        q = p.get("jpeg_quality", 82)
        if not _is_int(q) or not 30 <= q <= 95:
            errs.append(f"panel.jpeg_quality: {q!r} out of 30..95")

    fonts = raw.get("fonts")
    if not isinstance(fonts, dict) or not fonts:
        errs.append("fonts: the font alias table is missing")
        fonts = {}
    else:
        for alias, spec in fonts.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("family"), str):
                errs.append(f"fonts.{alias}: family is missing")
            elif not _is_int(spec.get("size")) or spec["size"] <= 0:
                errs.append(f"fonts.{alias}: size must be a positive integer")
            if isinstance(spec, dict):
                for k in spec:
                    if k not in FONT_KEYS:
                        errs.append(f"fonts.{alias}: unknown key {k!r}")
                _check_fallbacks(errs, alias, spec.get("fallbacks"))

    bg = raw.get("background")
    if not isinstance(bg, dict) or bg.get("type") not in BACKGROUND_TYPES:
        errs.append(f"background.type: {bg.get('type') if isinstance(bg, dict) else bg!r} "
                    f"is invalid, expected one of {sorted(BACKGROUND_TYPES)}")
    else:
        t = bg["type"]
        allowed_bg_keys = BACKGROUND_KEYS.get(t)
        if allowed_bg_keys is not None:
            for k in bg:
                if k not in allowed_bg_keys:
                    errs.append(f"background: unknown key {k!r} for type={t!r}")
        # `color` is validated on ANY type that accepts it, not just on solid. On
        # gradient/image/sequence it is the letterbox fill, and it used to be taken
        # unchecked: a broken value there did not fail, parse_hex silently degraded
        # it to a grey. With the editor offering the field, that is a wrong value
        # nobody reports. There is no background type where something other than
        # #RRGGBB means anything.
        if "color" in bg:
            _check_color(errs, "background", bg["color"])
        if t == "solid":
            _check_color(errs, "background", bg.get("color"))
        elif t == "gradient":
            stops = bg.get("stops")
            if not isinstance(stops, list) or len(stops) < 2:
                errs.append("background.stops: at least two stops are expected")
            else:
                errs.extend(_errores_de_stops(stops))
        elif t == "procedural":
            if bg.get("name", "scroll") not in PROCEDURALES:
                errs.append(f"background.name: {bg.get('name')!r} is not a known "
                            f"generator, expected one of "
                            f"{sorted(PROCEDURALES)}")
            # Both generators start from the gradient: with no stops there is
            # nothing to animate and it would silently be a flat colour.
            stops = bg.get("stops")
            if not isinstance(stops, list) or len(stops) < 2:
                errs.append("background.stops: at least two stops are expected")
            else:
                errs.extend(_errores_de_stops(stops))
            for clave in ("speed", "period"):
                if clave not in bg:
                    continue
                v = bg[clave]
                # speed 0 is legitimate (a still gradient); period 0 is not, it
                # would be a division by zero in the phase.
                minimo = 0 if clave == "speed" else None
                if not _is_num(v) or v < 0 or (minimo is None and v <= 0):
                    errs.append(f"background.{clave}: {v!r} is invalid, expected a number "
                                f"{'>= 0' if minimo == 0 else '> 0'}")
        elif t in ("image", "sequence", "video"):
            if safe_asset_path(bg.get("src")) is None:
                errs.append(f"background.src: invalid path, or outside the assets "
                            f"directory: {bg.get('src')!r}")
            if bg.get("fit", "cover") not in FITS:
                errs.append(f"background.fit: invalid {bg.get('fit')!r}")
            f = bg.get("fps", 10.0)
            if not _is_num(f) or not 0.1 <= f <= MAX_FPS:
                errs.append(f"background.fps: {f!r} out of 0.1..{MAX_FPS} "
                            f"(the panel refreshes at {MAX_FPS} Hz)")

    widgets = raw.get("widgets")
    if not isinstance(widgets, list):
        errs.append("widgets: a list is expected")
        return errs

    seen = set()
    for i, w in enumerate(widgets):
        errs.extend(_validate_widget(w, i, fonts, seen))
    return errs


def _validate_widget(w, i, fonts, seen) -> list[str]:
    errs = []
    if not isinstance(w, dict):
        return [f"widgets[{i}]: an object is expected"]

    wid = w.get("id")
    where = f"widget {wid!r}" if isinstance(wid, str) and wid else f"widgets[{i}]"
    if not isinstance(wid, str) or not wid:
        errs.append(f"widgets[{i}]: id is missing")
    elif wid in seen:
        errs.append(f"{where}: duplicate id")
    else:
        seen.add(wid)

    # x/y are checked BEFORE the early return on an unknown type: otherwise a widget
    # with both a bad type AND bad coordinates only reported one of the two, and the
    # editor shows every error at once.
    for k in ("x", "y"):
        if not _is_int(w.get(k)):
            errs.append(f"{where}: {k} must be an integer")

    t = w.get("type")
    if t not in WIDGET_TYPES:
        return errs + [f"{where}: unknown type {t!r}, expected one of "
                       f"{sorted(WIDGET_TYPES)}"]

    cls = WIDGET_TYPES[t]
    allowed_keys = set(cls.__dataclass_fields__)
    for k in w:
        if k not in allowed_keys:
            errs.append(f"{where}: unknown key {k!r}")

    for k in REQUIRED[t]:
        if k not in w:
            errs.append(f"{where}: required field {k!r} is missing")

    if "metric" in REQUIRED[t] and "metric" in w and not is_metric(w["metric"]):
        errs.append(f"{where}: unknown metric {w['metric']!r}")

    if "font" in REQUIRED[t] and "font" in w:
        # The original isinstance() skipped the check when the alias was not a
        # string, so a {"font": 3} passed straight through and only blew up in
        # ctx.layout.fonts[w.font] inside the render.
        if not isinstance(w["font"], str):
            errs.append(f"{where}: font must be the name of an alias in the fonts "
                        f"table, it is {w['font']!r}")
        elif w["font"] not in fonts:
            errs.append(f"{where}: unknown font alias {w['font']!r}")

    if t == "label" and "text" in w and not isinstance(w["text"], str):
        errs.append(f"{where}: text must be a string, it is {w['text']!r}")

    for k in ("color", "fill", "track", "stroke"):
        if k in w:
            _check_color(errs, where, w[k])

    if w.get("align", "left") not in ALIGNS:
        errs.append(f"{where}: invalid align {w.get('align')!r}")

    if t == "text":
        humanize_mode = w.get("humanize", "none")
        if humanize_mode not in HUMANIZE_MODES:
            errs.append(f"{where}: invalid humanize {humanize_mode!r}, "
                        f"expected one of {sorted(HUMANIZE_MODES)}")
        elif (humanize_mode != "none" and isinstance(w.get("format"), str)
              and w["format"] not in ("{}", "{0}")):
            # format_value() applies the humaniser and does not even look at
            # w.format in that case: a suffix like "{} Mbps" would sit in the
            # layout but never appear on the panel, with no hint as to why. It is
            # rejected rather than silently ignored.
            errs.append(f"{where}: format {w['format']!r} has no effect with "
                        f"humanize={humanize_mode!r}; use '{{}}' or "
                        f"drop humanize")
        if "format" in w:
            _check_format(errs, where, w["format"])
        for j, r in enumerate(w.get("rules") or []):
            if _parse_rule(r) is None:
                errs.append(f"{where}: invalid rules[{j}].when "
                            f"{r.get('when') if isinstance(r, dict) else r!r}; "
                            f"expected a comparison like '> 85'")
            elif isinstance(r, dict):
                _check_color(errs, f"{where} rules[{j}]", r.get("color"))

    if t == "rect":
        if w.get("fill") is None and w.get("stroke") is None:
            errs.append(f"{where}: a rect needs 'fill', 'stroke' or both; with "
                        f"neither it draws nothing")
        sw = w.get("stroke_width", 1)
        if not _is_int(sw) or sw < 1:
            errs.append(f"{where}: stroke_width must be an integer >= 1, it is {sw!r}")

    for k in ("w", "h", "r", "thickness", "radius", "samples"):
        if k in w and not _is_int(w[k]):
            errs.append(f"{where}: {k} must be an integer")

    # Everything below used to be an allowed key with no type check at all: the
    # layout validated clean and the TypeError surfaced inside Renderer.frame(),
    # where Engine.run() -- which only catches (OSError, PanelNotFound) -- does not
    # catch it. With hot reload the bad layout also replaces the good one, so there
    # is none left to fall back to.
    # min/max accept null: their default in the model is already None and means
    # "open end, fill it in from the metric spec". start_angle and sweep do not:
    # their default is a number, so a null reaches the render as-is and blows up in
    # w.start_angle + w.sweep.
    for k in ("min", "max"):
        if k in w and w[k] is not None and not _is_num(w[k]):
            errs.append(f"{where}: {k} must be a number, it is {w[k]!r}")
    for k in ("start_angle", "sweep"):
        if k in w and not _is_num(w[k]):
            errs.append(f"{where}: {k} must be a number, it is {w[k]!r}")

    lo, hi = w.get("min"), w.get("max")
    if _is_num(lo) and _is_num(hi) and hi <= lo:
        errs.append(f"{where}: max ({hi}) has to be greater than min ({lo}); "
                    f"otherwise the widget is empty for every value")

    if "samples" in w and _is_int(w["samples"]) and w["samples"] < 1:
        # series[-0:] is series[0:]: a 0 plots the WHOLE history instead of none,
        # and a negative slices from the front of the series.
        errs.append(f"{where}: samples must be >= 1, it is {w['samples']}")

    if t == "image" and "src" in w and safe_asset_path(w["src"]) is None:
        errs.append(f"{where}: src is invalid or outside the assets directory: "
                    f"{w['src']!r}")

    return errs


def build(raw) -> Layout:
    """Builds the model. Assumes validate(raw) returned []."""
    fonts = {a: Font(s["family"], s["size"], bool(s.get("bold", False)),
                     tuple(s.get("fallbacks") or ()))
             for a, s in raw["fonts"].items()}
    bgr = raw["background"]
    bg = Background(
        type=bgr["type"],
        color=bgr.get("color", "#000000"),
        stops=[{"at": float(s["at"]), "color": s["color"]} for s in bgr.get("stops", [])],
        angle=float(bgr.get("angle", 90.0)),
        src=safe_asset_path(bgr["src"]) if bgr.get("src") else None,
        fit=bgr.get("fit", "cover"),
        name=bgr.get("name", "scroll"),
        speed=float(bgr.get("speed", 20.0)),
        period=float(bgr.get("period", 6.0)),
        fps=float(bgr.get("fps", 10.0)))

    widgets: list[Widget] = []
    for w in raw["widgets"]:
        cls = WIDGET_TYPES[w["type"]]
        kwargs = {k: v for k, v in w.items() if k in cls.__dataclass_fields__}
        if cls is TextWidget:
            kwargs["rules"] = [r for r in (_parse_rule(x) for x in w.get("rules") or [])
                               if r is not None]
        if cls is ImageWidget:
            kwargs["src"] = safe_asset_path(w["src"])
        widgets.append(cls(**kwargs))

    p = raw["panel"]
    return Layout(
        version=raw["version"],
        name=raw["name"],
        designed_for=Size(raw["designed_for"]["width"], raw["designed_for"]["height"]),
        panel=PanelCfg(p.get("rotate", 0), p.get("brightness", 100),
                       float(p.get("fps", 1.0)), p.get("jpeg_quality", 82)),
        fonts=fonts, background=bg, widgets=widgets)
