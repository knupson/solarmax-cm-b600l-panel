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
                    ImageWidget, LabelWidget, Layout, PanelCfg, RectWidget,
                    Rule, Size, TextWidget, Widget)

SUPPORTED_VERSION = 1

# Refresco del panel. Ver el spike de fase 2.
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

# Derivados del modelo, no escritos a mano: agregarle un campo a PanelCfg o a
# Font hacia que el validador empezara a rechazar layouts validos hasta que
# alguien se acordara de actualizar el set. El chequeo de claves de widget ya se
# derivaba de __dataclass_fields__; esto lo iguala.
PANEL_KEYS = set(PanelCfg.__dataclass_fields__)
FONT_KEYS = set(Font.__dataclass_fields__)

# claves permitidas por tipo de background. "color" se admite en solid (relleno)
# y en image/sequence/video (relleno de letterbox); en gradient no la lee el
# modelo pero build() la acepta sin error, asi que la dejamos pasar en vez de
# arriesgar un falso rechazo.
BACKGROUND_KEYS = {
    "solid": {"type", "color"},
    "gradient": {"type", "stops", "angle", "color"},
    "image": {"type", "src", "fit", "color"},
    # sequence lleva su propio fps, independiente del panel.fps: una animacion
    # de 12 cuadros por segundo se ve igual de fluida con el panel a 30 o a 60.
    "sequence": {"type", "src", "fit", "color", "fps"},
    "video": {"type", "src", "fit", "color", "fps"},
    # procedural parte del gradiente, asi que comparte stops/angle; name elige
    # el generador y speed/period lo parametrizan.
    "procedural": {"type", "name", "stops", "angle", "color", "speed", "period"},
}
PROCEDURALES = {"scroll", "pulse"}

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
    "rect": ["w", "h"],
}


def safe_asset_path(src) -> str | None:
    """Normaliza una ruta de asset y la rechaza si se escapa del directorio.

    El servicio corre como SYSTEM: sin esto, un '..\\..\\' le hace leer
    cualquier archivo de la maquina.

    Las mismas comprobaciones se aplican dos veces: sobre la entrada cruda y
    sobre el resultado normalizado. Un '..' de mas puede consumirse contra un
    segmento real anterior (p.ej. 'a/../C:/Windows/win.ini') y la letra de
    unidad recien queda expuesta despues de normalizar; revisar solo la
    entrada cruda deja pasar eso.
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


# Dispositivos de Windows: `open("CON")` abre la consola y `open("COM1")` el
# puerto serie. No es un escape del directorio de assets -- por eso no lo
# atrapaban los chequeos de ruta -- pero una lectura puede quedarse esperando
# para siempre y colgar el hilo de render. Con las secuencias de fondo esa ruta
# se abre de verdad, asi que dejo de ser un problema teorico.
_DISPOSITIVOS = {"con", "prn", "aux", "nul", "conin$", "conout$"}
_DISPOSITIVOS |= {f"com{i}" for i in range(1, 10)}
_DISPOSITIVOS |= {f"lpt{i}" for i in range(1, 10)}


def _es_dispositivo(segmento: str) -> bool:
    """True si el segmento nombra un dispositivo reservado.

    Se compara el nombre SIN extension: "NUL.jpg" tambien es el dispositivo nulo
    para el subsistema de archivos de Windows. Un prefijo no alcanza --
    "CONSOLAS.png" es un archivo legitimo -- asi que la comparacion es exacta
    sobre el nombre base.
    """
    base = segmento.split(".", 1)[0].strip().lower()
    return base in _DISPOSITIVOS


def _is_unsafe_normalized_path(s: str) -> bool:
    """True si `s` es absoluta, UNC, de unidad de Windows ('C:foo'), o si algun
    segmento nombra un dispositivo reservado."""
    if (s.startswith("/") or s.startswith("//")
            or re.match(r"^[A-Za-z]:", s) is not None or ":" in s):
        return True
    return any(_es_dispositivo(seg) for seg in s.split("/") if seg)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_fallbacks(errs, alias, v):
    """`fallbacks` tiene que ser una lista de nombres de familia usables.

    Se valida en vez de aceptar cualquier cosa porque el error se manifiesta lejos:
    un fallback vacio o numerico no rompe nada al cargar, y recien se nota como "la
    fuente no se reemplazo" en la maquina de otra persona.
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
    # Formatter().parse() solo reporta el campo de nivel superior: un campo
    # anidado dentro del format_spec (p.ej. "{0!r:>{1}}") pasaria como "un
    # solo campo" sin que se note que en realidad referencia un segundo
    # argumento posicional que .format(valor) no tiene y que revienta en
    # render, no en validate().
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
    """Valida las paradas de un degradado. Compartido entre 'gradient' y
    'procedural', que parte del mismo gradiente."""
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
        # 60 es el refresco del panel. Por encima, el panel descarta los
        # frames -- no aplica contrapresion, acepta 227 fps de escritura
        # sostenida sin frenar al host -- asi que serian frames renderizados,
        # comprimidos y escritos para nada. Costo medido contra el panel real:
        # 0,6% de un nucleo a 1 fps, 17% a 30, 37% a 60.
        # Fraccionario a proposito: 0.5 es un cuadro cada dos segundos, que en un
        # panel de datos es una cadencia legitima y la mas barata de todas. La
        # bandeja solo ofrece 1/10/30/60, pero el perfil no tiene por que limitarse
        # al menu.
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
        # `color` se valida en CUALQUIER tipo que la admita, no solo en solid.
        # En gradient/image/sequence es el relleno de letterbox y antes se
        # aceptaba sin chequear: un valor roto ahi no fallaba, parse_hex lo
        # degradaba en silencio a un gris. Con el editor ofreciendo el campo eso
        # es un valor equivocado que nadie reporta. No hay ningun tipo de fondo
        # donde algo que no sea #RRGGBB signifique algo.
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
            # Los dos generadores parten del gradiente: sin paradas no hay nada
            # que animar y quedaria un color plano en silencio.
            stops = bg.get("stops")
            if not isinstance(stops, list) or len(stops) < 2:
                errs.append("background.stops: at least two stops are expected")
            else:
                errs.extend(_errores_de_stops(stops))
            for clave in ("speed", "period"):
                if clave not in bg:
                    continue
                v = bg[clave]
                # speed 0 es legitimo (un gradiente quieto); period 0 no, seria
                # una division por cero en la fase.
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

    # x/y se chequean ANTES del early return por tipo desconocido: si no, un
    # widget con el tipo mal Y las coordenadas mal solo reportaba una de las dos
    # cosas, y el editor muestra todos los errores juntos.
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
        # El isinstance() original salteaba el chequeo cuando el alias no era
        # texto, asi que un {"font": 3} pasaba entero y recien reventaba en
        # ctx.layout.fonts[w.font] dentro del render.
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
            # format_value() aplica el humanizador y ni siquiera mira
            # w.format en ese caso: un sufijo como "{} Mbps" quedaria
            # escrito en el layout pero nunca se ve en el panel, sin ningun
            # aviso de por que. Se rechaza en vez de ignorarlo en silencio.
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

    # Todo lo que sigue eran claves permitidas sin ningun chequeo de tipo: el
    # layout validaba limpio y el TypeError aparecia adentro de
    # Renderer.frame(), donde Engine.run() -- que captura solo (OSError,
    # PanelNotFound) -- no lo ataja. Con hot-reload el layout malo ademas
    # reemplaza al bueno, asi que no queda ninguno al que volver.
    # min/max admiten null: su default en el modelo ya es None y significa
    # "extremo abierto, completalo con el spec de la metrica". start_angle y
    # sweep no: su default es un numero, asi que un null llega tal cual al
    # render y revienta en w.start_angle + w.sweep.
    for k in ("min", "max"):
        if k in w and w[k] is not None and not _is_num(w[k]):
            errs.append(f"{where}: {k} must be a number, it is {w[k]!r}")
    for k in ("start_angle", "sweep"):
        if k in w and not _is_num(w[k]):
            errs.append(f"{where}: {k} must be a number, it is {w[k]!r}")

    lo, hi = w.get("min"), w.get("max")
    if _is_num(lo) and _is_num(hi) and hi <= lo:
        errs.append(f"{where}: max ({hi}) has to be greater than min ({lo}); "
                    f"si no, el widget queda vacio para cualquier valor")

    if "samples" in w and _is_int(w["samples"]) and w["samples"] < 1:
        # series[-0:] es series[0:]: un 0 grafica TODO el historial en vez de
        # nada, y un negativo corta por el frente de la serie.
        errs.append(f"{where}: samples must be >= 1, it is {w['samples']}")

    if t == "image" and "src" in w and safe_asset_path(w["src"]) is None:
        errs.append(f"{where}: src is invalid or outside the assets directory: "
                    f"{w['src']!r}")

    return errs


def build(raw) -> Layout:
    """Construye el modelo. Asume que validate(raw) devolvio []."""
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
