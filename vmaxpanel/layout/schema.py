"""Validador propio de layouts.

Devuelve la lista completa de errores en castellano llano, para que el editor
los muestre todos juntos. No usa jsonschema: los mensajes quedan atados a
nuestro modelo y es una dependencia menos para distribuir.

Los layouts se comparten entre usuarios, asi que un layout NO puede ejecutar
nada: las reglas de color son comparadores parseados a mano, no expresiones.
"""
import posixpath
import re
from string import Formatter

from ..metrics import is_metric
from .model import (ArcWidget, Background, BarWidget, Font, GraphWidget,
                    ImageWidget, LabelWidget, Layout, PanelCfg, Rule, Size,
                    TextWidget, Widget)

SUPPORTED_VERSION = 1

WIDGET_TYPES = {
    "text": TextWidget, "label": LabelWidget, "bar": BarWidget,
    "arc": ArcWidget, "graph": GraphWidget, "image": ImageWidget,
}

BACKGROUND_TYPES = {"solid", "gradient", "image", "sequence", "video", "procedural"}
ALIGNS = {"left", "center", "right"}
FITS = {"cover", "contain", "stretch"}
ROTATIONS = {0, 90, 180, 270}

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RULE_RE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

# campos obligatorios ademas de id/type/x/y
REQUIRED = {
    "text": ["metric", "font", "color", "format"],
    "label": ["text", "font", "color"],
    "bar": ["metric", "w", "h"],
    "arc": ["metric", "r"],
    "graph": ["metric", "w", "h"],
    "image": ["src", "w", "h"],
}


def safe_asset_path(src) -> str | None:
    """Normaliza una ruta de asset y la rechaza si se escapa del directorio.

    El servicio corre como SYSTEM: sin esto, un '..\\..\\' le hace leer
    cualquier archivo de la maquina.
    """
    if not isinstance(src, str) or not src.strip():
        return None
    s = src.replace("\\", "/")
    if s.startswith("/") or s.startswith("//") or re.match(r"^[A-Za-z]:", s):
        return None
    norm = posixpath.normpath(s)
    if norm == "." or norm == ".." or norm.startswith("../"):
        return None
    return norm


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_color(errs, where, v):
    if not isinstance(v, str) or not _COLOR_RE.match(v):
        errs.append(f"{where}: color invalido {v!r}, se espera #RRGGBB")


def _check_format(errs, where, v):
    if not isinstance(v, str):
        errs.append(f"{where}: format debe ser texto")
        return
    fields = [f for _, f, _, _ in Formatter().parse(v) if f is not None]
    if len(fields) != 1:
        errs.append(f"{where}: format {v!r} debe tener exactamente un campo, "
                    f"tiene {len(fields)}")
    elif fields[0] not in ("", "0"):
        errs.append(f"{where}: format {v!r} no puede nombrar el campo "
                    f"({fields[0]!r}); use {{}} o {{0}}")


def _parse_rule(raw):
    if not isinstance(raw, dict):
        return None
    m = _RULE_RE.match(str(raw.get("when", "")))
    if not m:
        return None
    return Rule(m.group(1), float(m.group(2)), raw.get("color", "#FFFFFF"))


def validate(raw) -> list[str]:
    errs: list[str] = []
    if not isinstance(raw, dict):
        return ["el layout debe ser un objeto JSON"]

    v = raw.get("version")
    if not _is_int(v):
        errs.append("version: falta o no es entero")
    elif v > SUPPORTED_VERSION:
        errs.append(f"version {v} es mayor que la soportada ({SUPPORTED_VERSION}); "
                    f"actualiza VMax Panel")
    elif v < 1:
        errs.append(f"version {v} invalida")

    if not isinstance(raw.get("name"), str) or not raw.get("name"):
        errs.append("name: falta o esta vacio")

    df = raw.get("designed_for")
    if not isinstance(df, dict) or not _is_int(df.get("width")) or not _is_int(df.get("height")):
        errs.append("designed_for: se esperan width y height enteros")
    elif df["width"] <= 0 or df["height"] <= 0:
        errs.append("designed_for: width y height deben ser positivos")

    p = raw.get("panel")
    if not isinstance(p, dict):
        errs.append("panel: falta")
    else:
        if p.get("rotate", 0) not in ROTATIONS:
            errs.append(f"panel.rotate: {p.get('rotate')!r} invalido, "
                        f"se espera 0, 90, 180 o 270")
        b = p.get("brightness", 100)
        if not _is_int(b) or not 0 <= b <= 100:
            errs.append(f"panel.brightness: {b!r} fuera de 0..100")
        f = p.get("fps", 1.0)
        if not _is_num(f) or not 0.1 <= f <= 30:
            errs.append(f"panel.fps: {f!r} fuera de 0.1..30")
        q = p.get("jpeg_quality", 82)
        if not _is_int(q) or not 30 <= q <= 95:
            errs.append(f"panel.jpeg_quality: {q!r} fuera de 30..95")

    fonts = raw.get("fonts")
    if not isinstance(fonts, dict) or not fonts:
        errs.append("fonts: falta la tabla de alias de fuente")
        fonts = {}
    else:
        for alias, spec in fonts.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("family"), str):
                errs.append(f"fonts.{alias}: falta family")
            elif not _is_int(spec.get("size")) or spec["size"] <= 0:
                errs.append(f"fonts.{alias}: size debe ser entero positivo")

    bg = raw.get("background")
    if not isinstance(bg, dict) or bg.get("type") not in BACKGROUND_TYPES:
        errs.append(f"background.type: {bg.get('type') if isinstance(bg, dict) else bg!r} "
                    f"invalido, se espera uno de {sorted(BACKGROUND_TYPES)}")
    else:
        t = bg["type"]
        if t == "solid":
            _check_color(errs, "background", bg.get("color"))
        elif t == "gradient":
            stops = bg.get("stops")
            if not isinstance(stops, list) or len(stops) < 2:
                errs.append("background.stops: se esperan al menos dos paradas")
            else:
                for i, s in enumerate(stops):
                    if not isinstance(s, dict) or not _is_num(s.get("at")):
                        errs.append(f"background.stops[{i}]: falta at numerico")
                    elif not 0.0 <= s["at"] <= 1.0:
                        errs.append(f"background.stops[{i}]: at fuera de 0..1")
                    _check_color(errs, f"background.stops[{i}]", s.get("color")
                                 if isinstance(s, dict) else None)
        elif t in ("image", "sequence", "video"):
            if safe_asset_path(bg.get("src")) is None:
                errs.append(f"background.src: ruta invalida o fuera del directorio "
                            f"de assets: {bg.get('src')!r}")
            if bg.get("fit", "cover") not in FITS:
                errs.append(f"background.fit: {bg.get('fit')!r} invalido")

    widgets = raw.get("widgets")
    if not isinstance(widgets, list):
        errs.append("widgets: se espera una lista")
        return errs

    seen = set()
    for i, w in enumerate(widgets):
        errs.extend(_validate_widget(w, i, fonts, seen))
    return errs


def _validate_widget(w, i, fonts, seen) -> list[str]:
    errs = []
    if not isinstance(w, dict):
        return [f"widgets[{i}]: se espera un objeto"]

    wid = w.get("id")
    where = f"widget {wid!r}" if isinstance(wid, str) and wid else f"widgets[{i}]"
    if not isinstance(wid, str) or not wid:
        errs.append(f"widgets[{i}]: falta id")
    elif wid in seen:
        errs.append(f"{where}: id repetido")
    else:
        seen.add(wid)

    t = w.get("type")
    if t not in WIDGET_TYPES:
        return errs + [f"{where}: tipo desconocido {t!r}, se espera uno de "
                       f"{sorted(WIDGET_TYPES)}"]

    for k in ("x", "y"):
        if not _is_int(w.get(k)):
            errs.append(f"{where}: {k} debe ser entero")

    for k in REQUIRED[t]:
        if k not in w:
            errs.append(f"{where}: falta el campo obligatorio {k!r}")

    if "metric" in REQUIRED[t] and "metric" in w and not is_metric(w["metric"]):
        errs.append(f"{where}: metrica desconocida {w['metric']!r}")

    if "font" in REQUIRED[t] and isinstance(w.get("font"), str) and w["font"] not in fonts:
        errs.append(f"{where}: alias de fuente desconocido {w['font']!r}")

    for k in ("color", "fill", "track"):
        if k in w:
            _check_color(errs, where, w[k])

    if w.get("align", "left") not in ALIGNS:
        errs.append(f"{where}: align {w.get('align')!r} invalido")

    if t == "text":
        if "format" in w:
            _check_format(errs, where, w["format"])
        for j, r in enumerate(w.get("rules") or []):
            if _parse_rule(r) is None:
                errs.append(f"{where}: rules[{j}].when invalido "
                            f"{r.get('when') if isinstance(r, dict) else r!r}; "
                            f"se espera un comparador como '> 85'")
            elif isinstance(r, dict):
                _check_color(errs, f"{where} rules[{j}]", r.get("color"))

    for k in ("w", "h", "r", "thickness", "radius", "samples"):
        if k in w and not _is_int(w[k]):
            errs.append(f"{where}: {k} debe ser entero")

    if t == "image" and "src" in w and safe_asset_path(w["src"]) is None:
        errs.append(f"{where}: src invalido o fuera del directorio de assets: "
                    f"{w['src']!r}")

    return errs


def build(raw) -> Layout:
    """Construye el modelo. Asume que validate(raw) devolvio []."""
    fonts = {a: Font(s["family"], s["size"], bool(s.get("bold", False)))
             for a, s in raw["fonts"].items()}
    bgr = raw["background"]
    bg = Background(
        type=bgr["type"],
        color=bgr.get("color", "#000000"),
        stops=[{"at": float(s["at"]), "color": s["color"]} for s in bgr.get("stops", [])],
        angle=float(bgr.get("angle", 90.0)),
        src=safe_asset_path(bgr["src"]) if bgr.get("src") else None,
        fit=bgr.get("fit", "cover"))

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
