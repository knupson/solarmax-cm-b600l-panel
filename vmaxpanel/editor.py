"""The visual layout editor.

`EditorState` is all of the behaviour -- load, edit, validate, preview, save --
and it does not import Tkinter. The window below it only wires controls to those
methods. It is the same split as between `PanelApp` and `tray.py`, and for the
same reason: what has tests is the part that can be wrong.

The editor saves through `loader.save()`, which writes atomically, and the engine
picks the change up live. There is no communication between the two processes:
the file IS the protocol.
"""
import argparse
import json
import re
import sys
import traceback
from pathlib import Path

from PIL import Image

from . import bundle, theme
from .layout import loader, model, schema
from .metrics import METRICS, group_for, spec_for
from .providers.setup import build_registry_without_sensors
from .render.renderer import Renderer

# Fields that are numbers. Anything not here is stored as text: a label reading
# "6000" must not become the integer 6000, because the validator requires `text` to
# be a string.
_INT_FIELDS = {"x", "y", "w", "h", "r", "radius", "thickness", "samples",
               "stroke_width", "size", "width", "height", "rotate",
               "brightness", "jpeg_quality"}
_FLOAT_FIELDS = {"min", "max", "start_angle", "sweep", "fps", "angle", "at"}

# A new widget has to validate the moment it is added: if the default does not
# validate, the user sees an error they did not make. They are completed with the
# layout's first font alias and a metric that always exists.
_TEMPLATES = {
    "text": {"metric": "cpu.load", "x": 24, "y": 24, "font": None,
             "color": "#FFFFFF", "format": "{:.0f}%"},
    "label": {"text": "NEW", "x": 24, "y": 24, "font": None, "color": "#FFFFFF"},
    "bar": {"metric": "cpu.load", "x": 24, "y": 24, "w": 200, "h": 16,
            "radius": 4, "fill": "#3987E5", "track": "#242834"},
    "arc": {"metric": "cpu.load", "x": 160, "y": 160, "r": 60, "thickness": 8,
            "fill": "#3987E5", "track": "#242834"},
    "graph": {"metric": "cpu.load", "x": 24, "y": 24, "w": 200, "h": 60,
              "color": "#3987E5", "samples": 120},
    "rect": {"x": 24, "y": 24, "w": 200, "h": 1, "fill": "#242834"},
}


# Demonstration values per metric, for the editor preview.
#
# By hand and not computed: 42% of the declared range gives things like "RAM used
# 107.5 G" (because the spec allows up to 256) or "5040 MT/s", which read as a bug
# rather than as an example. The point of the preview is to judge the layout, and
# for that the numbers have to look real -- including their length, which is what
# decides whether a value collides with the one next to it.
_DEMO = {
    "cpu.name": "INTEL CORE i5-12400F", "cpu.load": 55.5, "cpu.temp": 48.0,
    "cpu.clock": 4080.0, "cpu.vcore": 1.05, "cpu.vrm_temp": 41.0,
    "cpu.power": 65.0, "cpu.fan": 1250.0,
    "gpu.name": "AMD RADEON RX 6800 XT", "gpu.load": 23.0, "gpu.temp": 51.0,
    "gpu.hotspot": 68.0, "gpu.clock": 1850.0, "gpu.power": 84.0,
    "gpu.vram": 37.0, "gpu.fan": 980.0,
    "mem.load": 42.3, "mem.used": 13.5, "mem.total": 32.0, "mem.speed": 5600.0,
    "net.down": 1258291.0, "net.up": 40960.0,
    "clock.time": "14:32", "clock.date": "LUN 11 AGO",
}


def demo_sample() -> dict:
    """A plausible sample for EVERY known metric.

    The preview cannot be full of "--": metrics this machine does not serve (for
    lack of GSA1, of WinRing0, of whatever) still have to be drawn with something
    so the layout can be judged.

    Anything not in _DEMO falls back to the middle of the spec range, so a new
    metric appears with something reasonable without anybody having to touch this
    table.
    """
    out = {}
    for mid, spec in METRICS.items():
        if mid in _DEMO:
            out[mid] = _DEMO[mid]
        elif spec.kind == "text":
            out[mid] = mid.split(".")[-1].upper()
        else:
            lo = spec.min if spec.min is not None else 0.0
            hi = spec.max if spec.max is not None else lo + 100.0
            out[mid] = round(lo + (hi - lo) * 0.5, 2)
    # disk.temp.N is not in METRICS (it is a pattern), but the profiles use them.
    for n in range(4):
        out[f"disk.temp.{n}"] = 34.0 + n
    return out


class EditorState:
    """El layout en edicion: JSON crudo + validacion + preview.

    It works on the raw dict, not on the typed model, because the user passes
    through invalid intermediate states (a half-typed colour) and the model cannot
    represent those. `errors` says whether what is there right now validates;
    `preview()` keeps returning the last valid render while it does not, the same
    way the panel keeps the last good layout.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.raw = {}
        self.errors: list[str] = []
        self.dirty = False
        self._sample = demo_sample()
        self._last_good = None          # the last valid preview
        self._cache_catalogo = None     # the catalogue costs a WMI query
        self._fuentes = None            # FontResolver, to measure text boxes
        self._drag = None               # (id, offset x, offset y) of the drag
        self._historial = []            # copies of the layout, for undo
        self.reload()

    # --- carga y guardado ---

    def reload(self):
        try:
            self.raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.raw = {}
            self.errors = [f"could not read the profile: {e}"]
            self.dirty = False
            return
        self.errors = schema.validate(self.raw)
        self.dirty = False
        self._historial = []      # what is on disk is the new zero point

    def save(self) -> list[str]:
        """Saves only if it validates. Returns whatever errors prevented it.

        It never writes an invalid layout: the engine would reject it and keep the
        previous one, so the user would have "saved" something the panel ignores
        without being told why.
        """
        self.errors = schema.validate(self.raw)
        if self.errors:
            return list(self.errors)
        # save_raw and not save(build(raw)): going through the model rewrites the
        # file in the serialiser's formatting, and the profile is also edited by
        # hand. It is still atomic.
        loader.save_raw(self.raw, self.path)
        self.dirty = False
        return []

    # --- consulta ---

    def widget_ids(self) -> list[str]:
        return [w.get("id") for w in self.raw.get("widgets", [])]

    def widget(self, wid) -> dict | None:
        for w in self.raw.get("widgets", []):
            if w.get("id") == wid:
                return w
        return None

    def fonts(self) -> list[str]:
        return sorted(self.raw.get("fonts", {}))

    def metric_groups(self) -> dict:
        """{device: [(id, label), ...]} for the metric selector.

        The registry is asked, because it is the only thing that knows which devices
        exist on THIS machine and what they are called -- "vol.D.free" does not know
        that D is called GAMES. With no sensor backend (another machine, no
        permissions, no DLLs) it falls back to the registered metrics: the editor
        has to open anyway, with generic labels.

        The metrics the profile ALREADY uses are added too, even when the registry
        does not offer them. Otherwise, changing a widget's metric on a machine that
        does not serve it would make it vanish from the selector with no way back.
        """
        catalogo, grupos = self._catalogo()
        usadas = {w.get("metric") for w in self.raw.get("widgets", [])
                  if w.get("metric")}
        for mid in usadas:
            if mid not in catalogo:
                base = spec_for(mid)
                catalogo[mid] = base
                grupos.setdefault(mid, group_for(mid))

        salida = {}
        for mid, base in catalogo.items():
            if base is None:
                continue
            etiqueta = base.label or mid
            salida.setdefault(grupos.get(mid, "Other"), []).append((mid, etiqueta))
        for entradas in salida.values():
            entradas.sort(key=lambda par: par[1].lower())
        return dict(sorted(salida.items()))

    # --- the widget tree, for the list on the left ---

    DECORATION = "Decoration"

    # What a widget with no metric is called. It has no reading to name it, so
    # the type is the most informative thing left.
    _SIN_METRICA = {"label": "Label", "rect": "Rectangle", "image": "Image"}

    def widget_tree(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """[(group, [(widget id, row label), ...]), ...] for the editor's list.

        Grouped because a flat list of 47 ids makes the user remember what each
        one is; each row carries the friendly name AND the id because the name
        alone cannot be matched against the JSON they also edit by hand.

        The group comes from the same catalogue the metric selector uses, so it
        is refined by the registry when there is one -- `vol.D.free` lands under
        the disk's real name and not under "vol". Widgets that measure nothing
        (a label, a rect) go to their own group: there is no metric group that is
        theirs, and leaving them ungrouped would scatter them for no reason.

        Inside each group the profile's order is preserved. Grouping already
        gives up the global view of the paint order; scrambling it within a group
        as well would leave no way to reason about what covers what.
        """
        etiquetas = {mid: etiqueta
                     for filas in self.metric_groups().values()
                     for mid, etiqueta in filas}
        grupos_por_metrica = {mid: grupo
                              for grupo, filas in self.metric_groups().items()
                              for mid, _ in filas}

        salida: dict[str, list[tuple[str, str]]] = {}
        for w in self.raw.get("widgets", []):
            wid = w.get("id")
            if not isinstance(wid, str) or not wid:
                continue
            grupo, nombre = self._fila_de(w, etiquetas, grupos_por_metrica)
            salida.setdefault(grupo, []).append((wid, f"{nombre} ({wid})"))

        # Decoration last: it is the scaffolding, not what the panel is for.
        orden = sorted(k for k in salida if k != self.DECORATION)
        if self.DECORATION in salida:
            orden.append(self.DECORATION)
        return [(g, salida[g]) for g in orden]

    def _fila_de(self, w, etiquetas, grupos_por_metrica) -> tuple[str, str]:
        mid = w.get("metric")
        if isinstance(mid, str) and mid:
            return (grupos_por_metrica.get(mid) or group_for(mid),
                    etiquetas.get(mid) or mid)
        tipo = w.get("type", "widget")
        texto = w.get("text")
        # A label is recognised on the panel by the text it draws, so that is a
        # better name than "Label" repeated fifteen times.
        if tipo == "label" and isinstance(texto, str) and texto.strip():
            return self.DECORATION, texto.strip()
        return self.DECORATION, self._SIN_METRICA.get(tipo, tipo.capitalize())

    def _catalogo(self):
        """(catalogue, groups) from the registry, or from METRICS with no backend."""
        if self._cache_catalogo is None:
            catalogo, grupos = {}, {}
            registry = None
            try:
                registry, _cliente = build_registry_without_sensors()
                catalogo, grupos = registry.catalog(), registry.groups()
            except Exception:
                # No backend: the editor opens anyway with the registered
                # registradas y etiquetas genericas.
                catalogo = {mid: spec for mid, spec in METRICS.items()}
                grupos = {mid: group_for(mid) for mid in METRICS}
            finally:
                if registry is not None:
                    try:
                        registry.close()
                    except Exception:
                        pass
            self._cache_catalogo = (catalogo, grupos)
        catalogo, grupos = self._cache_catalogo
        return dict(catalogo), dict(grupos)

    # --- deshacer ---
    #
    # A copy of the raw layout is kept BEFORE every change, not a diff: with 154
    # widgets the JSON is ~40 KB and copying it costs less than reasoning about how
    # to reverse each kind of operation. A diff would be worth it if the layout were
    # genuinely large; here it would only add ways to get it wrong.
    MAX_UNDO = 60

    def _snapshot(self):
        """Stores the current state as a point to return to.

        Capped: unbounded, a 300 px drag would store 300 copies and the editor would
        end up with tens of MB of history for moving one widget.
        """
        self._historial.append(json.dumps(self.raw, ensure_ascii=False))
        if len(self._historial) > self.MAX_UNDO:
            del self._historial[0]

    def undo(self) -> bool:
        """Returns to the previous point. False if there is nothing to undo."""
        if not self._historial:
            return False
        self.raw = json.loads(self._historial.pop())
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return True

    # --- edicion ---

    def set_field(self, wid, key, value) -> list[str]:
        w = self.widget(wid)
        if w is None:
            return [f"there is no widget {wid!r}"]
        self._snapshot()
        w[key] = _coerce(key, value)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def add_widget(self, tipo, wid) -> list[str]:
        if tipo not in _TEMPLATES:
            return [f"tipo desconocido {tipo!r}"]
        if wid in self.widget_ids():
            return [f"there is already a widget with id {wid!r}"]
        self._snapshot()
        nuevo = {"id": wid, "type": tipo, **_TEMPLATES[tipo]}
        if nuevo.get("font", "no-alias") is None:
            aliases = self.fonts()
            if not aliases:
                return ["the layout defines no fonts at all"]
            nuevo["font"] = aliases[0]
        self.raw.setdefault("widgets", []).append(nuevo)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def remove_widget(self, wid) -> list[str]:
        self._snapshot()
        widgets = self.raw.get("widgets", [])
        self.raw["widgets"] = [w for w in widgets if w.get("id") != wid]
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    # --- fuentes ---

    _FUENTE_DEFAULT = {"family": "Consolas", "size": 20, "bold": False}

    def font_fields(self) -> list[str]:
        return ["family", "size", "bold"]

    def font_families(self) -> list[str]:
        """Installed families, for the combo box.

        Typing the family by hand is how one that does not exist gets written: the
        renderer falls back to the default font and the widget looks different with
        nothing warning about it. FontResolver already has the system's index.
        """
        from .render.fonts import FontResolver
        if self._fuentes is None:
            self._fuentes = FontResolver()
        try:
            return sorted(self._fuentes.index())
        except Exception:
            return [self._FUENTE_DEFAULT["family"]]

    def set_font_field(self, alias, clave, valor) -> list[str]:
        fuentes = self.raw.setdefault("fonts", {})
        if alias not in fuentes:
            return [f"there is no font alias {alias!r}"]
        self._snapshot()
        if clave == "size":
            fuentes[alias][clave] = _coerce("size", valor)
        elif clave == "bold":
            # From a text control this arrives as "true"/"false"; the validator
            # requires a JSON boolean, not the string.
            fuentes[alias][clave] = str(valor).strip().lower() in ("1", "true",
                                                                  "si", "sí", "yes")
        else:
            fuentes[alias][clave] = str(valor).strip()
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def add_font(self, alias) -> list[str]:
        alias = str(alias).strip()
        if not alias:
            return ["the alias cannot be empty"]
        fuentes = self.raw.setdefault("fonts", {})
        if alias in fuentes:
            return [f"the alias {alias!r} already exists"]
        self._snapshot()
        fuentes[alias] = dict(self._FUENTE_DEFAULT)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def font_users(self, alias) -> list[str]:
        return [w.get("id") for w in self.raw.get("widgets") or []
                if w.get("font") == alias]

    def remove_font(self, alias) -> list[str]:
        """Se niega si algun widget lo usa.

        Deleting it would leave the layout invalid -- "unknown font alias" -- and the
        engine would reject the whole profile, keeping the previous one. The user
        would have deleted a font and the panel would not change.
        """
        usuarios = self.font_users(alias)
        if usuarios:
            return [f"font {alias!r} is used by {len(usuarios)} widgets "
                    f"({', '.join(usuarios[:3])}{'...' if len(usuarios) > 3 else ''})"]
        fuentes = self.raw.get("fonts") or {}
        if alias not in fuentes:
            return [f"there is no alias {alias!r}"]
        if len(fuentes) <= 1:
            return ["the layout needs at least one font"]
        self._snapshot()
        del fuentes[alias]
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    # --- cajas, hit test y arrastre ---
    #
    # Everything in PANEL coordinates (320x1480), not preview ones: the window
    # converts by dividing by its scale. That way this logic does not depend on how
    # it is being displayed.

    def _canvas(self):
        d = self.raw.get("designed_for") or {}
        return int(d.get("width") or 320), int(d.get("height") or 1480)

    def widget_bbox(self, wid):
        """(x0, y0, x1, y1) de un widget, o None si no existe.

        For texts the font is MEASURED with the demonstration value instead of using
        a fixed radius: a 74 px clock and a 14 px label cannot have the same hit
        area, and with an invented radius grabbing the small one next to the big one
        would be impossible.
        """
        w = self.widget(wid)
        if w is None:
            return None
        x, y = int(w.get("x", 0)), int(w.get("y", 0))
        tipo = w.get("type")
        if tipo in ("bar", "graph", "rect", "image"):
            return (x, y, x + max(1, int(w.get("w", 1))),
                    y + max(1, int(w.get("h", 1))))
        if tipo == "arc":
            r = max(1, int(w.get("r", 1)))
            return (x - r, y - r, x + r, y + r)
        ancho, alto = self._medir_texto(w)
        # The renderer's anchor is "la"/"ma"/"ra": the height hangs downwards and the
        # width is distributed according to the alignment.
        alineacion = w.get("align", "left")
        if alineacion == "center":
            x0 = x - ancho // 2
        elif alineacion == "right":
            x0 = x - ancho
        else:
            x0 = x
        return (x0, y, x0 + max(6, ancho), y + max(6, alto))

    def _medir_texto(self, w):
        """(width, height) of the text this widget would draw."""
        from .render import widgets as W
        from .render.fonts import FontResolver

        if self._fuentes is None:
            self._fuentes = FontResolver()
        alias = w.get("font")
        spec = (self.raw.get("fonts") or {}).get(alias) or {}
        try:
            fuente = self._fuentes.resolve(
                model.Font(spec.get("family", "Consolas"),
                           int(spec.get("size", 14)), bool(spec.get("bold"))), 1.0)
        except Exception:
            fuente = None
        if w.get("type") == "label":
            texto = str(w.get("text", ""))
        else:
            texto = W.format_value(schema.WIDGET_TYPES["text"](
                id=w.get("id", "?"), type="text", x=0, y=0,
                metric=w.get("metric", ""), font=alias or "",
                format=w.get("format", "{}"),
                humanize=w.get("humanize", "none")),
                self._sample.get(w.get("metric")))
        if fuente is None or not texto:
            tam = int(spec.get("size", 14))
            return max(6, len(texto or "") * tam // 2), tam
        caja = fuente.getbbox(texto)
        return max(1, caja[2] - caja[0]), max(1, caja[3] - caja[1])

    def hit_test(self, x, y):
        """The id of the widget under the point, or None.

        It walks BACKWARDS because the list order is the paint order: the last one
        drawn is the one the user sees on top, and therefore the one they expect
        to grab.
        """
        for w in reversed(self.raw.get("widgets") or []):
            caja = self.widget_bbox(w.get("id"))
            if caja and caja[0] <= x <= caja[2] and caja[1] <= y <= caja[3]:
                return w.get("id")
        return None

    def begin_drag(self, wid, x, y):
        """Starts a drag. It stores the offset inside the widget so it moves by
        delta: putting the corner under the cursor would make the widget jump the
        moment it is grabbed anywhere other than that corner."""
        w = self.widget(wid)
        if w is None:
            return
        # The snapshot goes here and not in drag_to(): a drag fires one change per
        # mouse pixel, and with one snapshot per change undoing a drag would take
        # fifty Ctrl+Z presses. The whole gesture is ONE step.
        self._snapshot()
        self._drag = (wid, x - int(w.get("x", 0)), y - int(w.get("y", 0)))

    def drag_to(self, x, y) -> list[str]:
        if not self._drag:
            return []
        wid, dx, dy = self._drag
        w = self.widget(wid)
        if w is None:
            return []
        ancho, alto = self._canvas()
        # Clamped to the canvas: a widget dragged outside disappears from the panel
        # and there is no way left to grab it with the mouse.
        w["x"] = max(0, min(ancho - 1, int(x - dx)))
        w["y"] = max(0, min(alto - 1, int(y - dy)))
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def end_drag(self):
        self._drag = None

    # --- reglas de color ---
    #
    # In the JSON a rule is {"when": "> 90", "color": "#FF4D00"}: the comparison and
    # the number travel together in one string. The UI needs the three pieces
    # separately -- a combo for the operator, a field for the number, another for the
    # colour -- so they are split on read and reassembled on write. It is the only
    # place in the editor that translates between the file's shape and the controls'
    # shape, and it lives here rather than in the window so it can have tests.
    _RULE_RE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

    def rule_operators(self) -> list[str]:
        """The ones the validator accepts, in order of use."""
        return [">", ">=", "<", "<="]

    def rules(self, wid) -> list[dict]:
        """[{op, value, color}] for the widget, with the comparison already split."""
        w = self.widget(wid) or {}
        salida = []
        for r in w.get("rules") or []:
            m = self._RULE_RE.match(str(r.get("when", "")))
            salida.append({"op": m.group(1) if m else ">",
                           "value": m.group(2) if m else "",
                           "color": r.get("color", "#FFFFFF")})
        return salida

    def add_rule(self, wid) -> list[str]:
        """Adds a rule that already validates.

        The default threshold comes from the metric's spec -- 85% of its maximum --
        rather than a fixed 90: a ">= 90" rule on a voltage of 1.05 V never fires and
        the user cannot see why their rule does nothing.
        """
        w = self.widget(wid)
        if w is None:
            return [f"there is no widget {wid!r}"]
        spec = spec_for(w.get("metric", ""))
        techo = spec.max if (spec and spec.max) else 100.0
        self._snapshot()
        w.setdefault("rules", []).append(
            {"when": f"> {round(techo * 0.85, 2):g}", "color": "#FF8A1F"})
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def remove_rule(self, wid, i) -> list[str]:
        reglas = (self.widget(wid) or {}).get("rules") or []
        if not 0 <= i < len(reglas):
            return [f"there is no rule {i}"]
        self._snapshot()
        del reglas[i]
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def set_rule(self, wid, i, campo, valor) -> list[str]:
        """Changes one piece of a rule. It does not write if the result is invalid.

        Unlike the rest of the editor's fields -- where a half-typed value is
        legitimate and merely reported -- a malformed rule breaks ALL of that
        widget's rules, because the validator rejects the whole layout. So here it is
        tried first and the change is discarded if it does not validate.
        """
        reglas = (self.widget(wid) or {}).get("rules") or []
        if not 0 <= i < len(reglas):
            return [f"there is no rule {i}"]
        actual = self.rules(wid)[i]
        nuevo = dict(actual)
        nuevo[campo] = str(valor).strip()
        candidata = {"when": f"{nuevo['op']} {nuevo['value']}",
                     "color": nuevo["color"]}
        anterior = dict(reglas[i])
        reglas[i] = candidata
        errores = schema.validate(self.raw)
        if errores:
            reglas[i] = anterior          # reverted: one broken rule kills them all
            self.errors = schema.validate(self.raw)
            return errores
        reglas[i] = anterior              # so the snapshot stores the previous state
        self._snapshot()
        reglas[i] = candidata
        self.dirty = True
        self.errors = []
        return []

    # --- fondo ---
    #
    # The fields each type accepts come from schema.BACKGROUND_KEYS, not from a list
    # written by hand here: if the UI offered a field the type does not accept, it
    # would write a key the validator rejects and the user would see an error they
    # did not make. `stops` is edited separately because it is a list.
    _DEFAULTS_FONDO = {
        "color": "#0B0F17", "angle": 90.0, "fit": "cover", "src": "fondos",
        "name": "scroll", "speed": 20.0, "period": 6.0, "fps": 10.0,
    }
    _STOPS_DEFAULT = [{"at": 0.0, "color": "#101725"},
                      {"at": 1.0, "color": "#141A26"}]

    def background_fields(self, tipo=None) -> list[str]:
        """The scalar fields this background type accepts, in a stable order."""
        tipo = tipo or (self.raw.get("background") or {}).get("type", "solid")
        permitidas = schema.BACKGROUND_KEYS.get(tipo, {"type", "color"})
        orden = ["name", "color", "angle", "speed", "period", "src", "fit", "fps"]
        return [c for c in orden if c in permitidas]

    def background_types(self) -> list[str]:
        return sorted(schema.BACKGROUND_TYPES)

    def has_stops(self, tipo=None) -> bool:
        tipo = tipo or (self.raw.get("background") or {}).get("type", "solid")
        return "stops" in schema.BACKGROUND_KEYS.get(tipo, set())

    def set_background_type(self, tipo) -> list[str]:
        """Changes the type and fills in whatever that type needs.

        Keys the new type also accepts are kept -- going from 'gradient' to
        'procedural' must not lose the gradient the user already tuned, which is
        precisely the point of procedural starting from it -- and the rest are
        dropped, because they would be left as unknown keys.
        """
        self._snapshot()
        viejo = dict(self.raw.get("background") or {})
        permitidas = schema.BACKGROUND_KEYS.get(tipo, {"type", "color"})
        nuevo = {"type": tipo}
        for clave in permitidas - {"type"}:
            if clave == "stops":
                stops = viejo.get("stops")
                nuevo["stops"] = stops if (isinstance(stops, list) and len(stops) >= 2) \
                    else [dict(s) for s in self._STOPS_DEFAULT]
            elif clave in viejo:
                nuevo[clave] = viejo[clave]
            elif clave in self._DEFAULTS_FONDO:
                nuevo[clave] = self._DEFAULTS_FONDO[clave]
        self.raw["background"] = nuevo
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def set_background_field(self, clave, valor) -> list[str]:
        self._snapshot()
        self.raw.setdefault("background", {})[clave] = _coerce_fondo(clave, valor)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    # --- gradient stops ---

    def stops(self) -> list:
        return (self.raw.get("background") or {}).get("stops") or []

    def add_stop(self) -> list[str]:
        """Adds a stop in the middle of the largest gap.

        In the middle and not at the end: a new stop on top of an existing one is
        invisible, and the user cannot tell what happened.
        """
        self._snapshot()
        stops = self.raw.setdefault("background", {}).setdefault("stops", [])
        if len(stops) < 2:
            stops[:] = [dict(s) for s in self._STOPS_DEFAULT]
        else:
            ordenadas = sorted(stops, key=lambda s: s.get("at", 0))
            hueco, donde = -1.0, 0.5
            for a, b in zip(ordenadas, ordenadas[1:]):
                d = b.get("at", 0) - a.get("at", 0)
                if d > hueco:
                    hueco, donde = d, (a.get("at", 0) + b.get("at", 0)) / 2
            stops.append({"at": round(donde, 3), "color": "#3987E5"})
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def remove_stop(self, i) -> list[str]:
        """It never leaves fewer than two: a one-stop gradient is not a gradient and
        the validator rejects it."""
        stops = self.stops()
        if len(stops) <= 2:
            return ["a gradient needs at least two stops"]
        if not 0 <= i < len(stops):
            return [f"there is no stop {i}"]
        self._snapshot()
        del stops[i]
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def set_stop(self, i, clave, valor) -> list[str]:
        stops = self.stops()
        if not 0 <= i < len(stops):
            return [f"there is no stop {i}"]
        self._snapshot()
        stops[i][clave] = float(valor) if clave == "at" else str(valor).strip()
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    # --- panel ---

    def panel_fields(self) -> list[str]:
        return ["fps", "brightness", "rotate", "jpeg_quality"]

    def set_panel_field(self, clave, valor) -> list[str]:
        self._snapshot()
        self.raw.setdefault("panel", {})[clave] = _coerce(clave, valor)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def move_widget(self, wid, dx, dy) -> list[str]:
        w = self.widget(wid)
        if w is None:
            return [f"there is no widget {wid!r}"]
        self._snapshot()
        w["x"] = int(w.get("x", 0)) + int(dx)
        w["y"] = int(w.get("y", 0)) + int(dy)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    # --- preview ---

    def preview(self) -> Image.Image:
        """The frame as the panel would see it. With an invalid layout it returns the
        last valid one, or an empty canvas if there never was one."""
        if not self.errors:
            try:
                layout = schema.build(self.raw)
                self._last_good = Renderer(layout).frame(self._sample)
            except Exception:
                pass                    # build/render failed: the previous one stays
        if self._last_good is None:
            return Image.new("RGB", (320, 1480), (0, 0, 0))
        return self._last_good


ANIMADO = ("Animated background: the preview shows a single frame, so it looks "
           "frozen here. On the panel it animates. Raising the panel fps "
           "(Panel tab) makes it more visible.")


def _mismo_contenido(a, b) -> bool:
    """Two paths with the same content.

    It compares size and then bytes; for folders the list of names and sizes is
    enough -- a frame sequence with the same names and sizes is the same sequence,
    and reading 300 PNGs to confirm it does not pay.
    """
    a, b = Path(a), Path(b)
    if a.is_dir() != b.is_dir():
        return False
    if a.is_dir():
        def censo(d):
            return sorted((p.relative_to(d).as_posix(), p.stat().st_size)
                          for p in d.rglob("*") if p.is_file())
        return censo(a) == censo(b)
    if a.stat().st_size != b.stat().st_size:
        return False
    # In chunks and not read_bytes(): a 900 MB video compared that way is 1.8 GB of
    # memory at once, to answer a yes/no question. This project has already had one
    # memory-consumption episode and it is not worth repeating for convenience.
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ta, tb = fa.read(1 << 20), fb.read(1 << 20)
            if ta != tb:
                return False
            if not ta:
                return True


def pista_fondo(tipo) -> str:
    """The hint for a background type, or "" when none is needed.

    A module function and not a method of the window: it is pure text, decided by
    the type and by whether ffmpeg is installed, and that way it is tested without
    opening Tkinter.

    The one for animated backgrounds matters: the preview is ONE frame, so a moving
    background looks frozen there and that reads as a bug.
    """
    if tipo == "procedural":
        return ANIMADO
    if tipo == "sequence":
        return (ANIMADO + " src is a folder of images, relative to "
                "vmaxpanel/assets.")
    if tipo == "video":
        # The path is looked up on every call -- not cached at import -- because the
        # user can install ffmpeg with the editor open, and the hint has to stop
        # asking for it when they reopen the tab.
        from .render.video import COMO_INSTALAR, buscar_ffmpeg
        if buscar_ffmpeg() is None:
            return ANIMADO + " " + COMO_INSTALAR
        return (ANIMADO + " src is a video relative to vmaxpanel/assets: mp4, "
                "webm, mkv, gif, whatever ffmpeg can open.")
    return ""


def _coerce_fondo(clave, valor):
    """Like _coerce, but for the background keys.

    `fps` on the background is a sequence's cadence and takes decimals; on the panel
    it is an integer. Same key, different type depending on where it sits: hence the
    background having its own conversion rather than sharing the table.
    """
    if clave in ("angle", "speed", "period", "fps"):
        s = str(valor).strip()
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return s
    return str(valor).strip()


def _coerce(key, value):
    """Converts what came out of a text control into the type the schema expects
    for that key. Anything non-numeric is left as text: `text`, `format` and the
    colours have to stay str, even when they look like numbers.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    s = str(value).strip()
    if key in _INT_FIELDS:
        try:
            return int(s)
        except ValueError:
            try:
                return int(float(s))
            except ValueError:
                return s                # stored as-is and the validator warns
    if key in _FLOAT_FIELDS:
        if s == "":
            return None                 # an open end (min/max left unset)
        try:
            return float(s)
        except ValueError:
            return s
    return s


# --------------------------------------------------------------------------
# The window. Tkinter is imported inside so EditorState can be used (and tested)
# on a machine without Tk.
# --------------------------------------------------------------------------

PREVIEW_SCALE = 0.36     # initial scale, before the window has a size

# Manual zoom. The ceiling is 4x and not the 1.0 that _escala_disponible() uses:
# that cap exists because *fitting* beyond 1:1 is blurry upscaling nobody asked
# for, but zooming in is asked for -- it is how you place a widget to the pixel.
# The floor keeps the whole 1480 px panel visible on any window.
ZOOM_MIN, ZOOM_MAX = 0.05, 4.0
ZOOM_PASO = 1.15         # per wheel notch; ~5 notches to double

# Two densities. One alone either does not help you line anything up or turns the
# preview into graph paper: the fine one measures, the heavy one gives the eye
# something to count.
GRILLA_FINA, GRILLA_GRUESA = 20, 100


def zoom_step(escala: float, hacia_arriba: bool) -> float:
    """The scale after one notch of the wheel, clamped.

    Multiplicative and not additive: a fixed +0.1 is imperceptible when zoomed out
    and a huge jump when zoomed in.
    """
    k = escala * (ZOOM_PASO if hacia_arriba else 1 / ZOOM_PASO)
    return max(ZOOM_MIN, min(ZOOM_MAX, k))


def vista_tras_zoom(punto_panel: float, escala: float, puntero: float,
                    total: float) -> float:
    """Scroll fraction that leaves `punto_panel` under the pointer after zooming.

    Zoom that ignores the pointer walks away from whatever the user was looking at.
    The point sits `punto_panel * escala` from the document's origin, and it has to
    end up `puntero` pixels into the viewport, so the origin goes to the difference.

    Clamped to 0..1 because that is what xview_moveto accepts; outside it the view
    silently stops moving.
    """
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, (punto_panel * escala - puntero) / total))


def lineas_grilla(ancho: int, alto: int, paso: int):
    """(vertical xs, horizontal ys) of the grid, in PANEL coordinates.

    Guarded against a non-positive step: range() raises on 0, and it would do it
    inside a redraw, where Tkinter swallows the exception and the preview just
    stops updating with nothing in the log.
    """
    if paso <= 0:
        return [], []
    return (list(range(0, int(ancho) + 1, paso)),
            list(range(0, int(alto) + 1, paso)))


class EditorWindow:
    def __init__(self, state: EditorState):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.state = state
        self.root = tk.Tk()
        self.root.title(f"VMax Panel — {state.path.name}")
        # The same icon as the tray, so the window does not appear with the generic
        # Python one in the taskbar. If it is missing, nothing happens.
        try:
            icono = Path(__file__).resolve().parent / "assets" / "vmaxpanel.ico"
            if icono.exists():
                self.root.iconbitmap(default=str(icono))
        except Exception:
            pass
        self._preview_img = None
        self._escala = PREVIEW_SCALE
        # None means "fit to the container", which is what the editor does until
        # somebody turns the wheel. Once set it OUTRANKS the fit, and it has to
        # survive every redraw -- see _draw_preview().
        self._zoom = None
        self._sel_rect = None
        self._doc = (1, 1)          # scrollregion, for the zoom anchoring
        self._grupos = set()        # the iids inserted as group rows
        self._fields = {}
        self._pickers = {}
        self._metric_por_etiqueta = {}
        self._rule_rows = []
        self._rules_frame = None
        # (type, key) of every field with something typed and unconfirmed. See
        # _aplicar_pendientes().
        self._pendientes = set()
        # Before _build(): the theme swaps the base ttk theme, and doing that
        # after the controls exist leaves some of them drawn by the previous one.
        # It follows the Windows setting; see theme.py for why `clam`.
        self.palette = theme.apply(self.root, self.ttk)
        self._build()
        # The initial size is requested explicitly: without this Tkinter gives it the
        # minimum the controls need, the widget list and the properties eat the
        # width, and the preview of a 320x1480 panel ends up a ~60 px strip. The
        # responsive scale already fixed the maximised case; this fixes the opening
        # case, which is 100% of the time.
        self.root.geometry(self._geometria_inicial(
            self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        self._refresh(select_first=True)

    # What each column needs: the left one (list + properties + move) and the preview
    # of a vertical panel with some room. Measured on the real window.
    ANCHO_CONTROLES = 700
    ANCHO_PREVIEW = 420

    @staticmethod
    def _geometria_inicial(ancho_pantalla, alto_pantalla) -> str:
        """The "WxH" it is best to open with, bounded by the screen.

        Bounded and not fixed: on a 1366x768 laptop, asking for 1200x950 puts the
        action bar below the edge, which is to say with no way to save. 85% leaves
        room for the
        barra de tareas.
        """
        ancho = min(EditorWindow.ANCHO_CONTROLES + EditorWindow.ANCHO_PREVIEW,
                    int(ancho_pantalla * 0.9))
        alto = min(950, int(alto_pantalla * 0.85))
        return f"{max(900, ancho)}x{max(600, alto)}"

    # --- construccion ---

    def _build(self):
        tk, ttk = self.tk, self.ttk
        raiz = ttk.Frame(self.root, padding=8)
        # The footer is packed BEFORE the root even though it is filled afterwards:
        # pack distributes the space in order, and the root goes with expand=True.
        # The other way around, the root takes everything and the footer -- the one
        # with Save on it -- can end up outside the window.
        self._pie = ttk.Frame(self.root)
        self._pie.pack(side="bottom", fill="x")
        raiz.pack(fill="both", expand=True)

        # Tabs: the background and the panel are not widgets, and putting them in the
        # same column would force a choice between seeing the list and seeing the
        # background.
        self.tabs = ttk.Notebook(raiz)
        self.tabs.pack(side="left", fill="both", expand=True)
        tab_widgets = ttk.Frame(self.tabs, padding=6)
        self.tab_fondo = ttk.Frame(self.tabs, padding=6)
        self.tab_fuentes = ttk.Frame(self.tabs, padding=6)
        self.tab_panel = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(tab_widgets, text="Widgets")
        self.tabs.add(self.tab_fondo, text="Background")
        self.tabs.add(self.tab_fuentes, text="Fonts")
        self.tabs.add(self.tab_panel, text="Panel")

        izq = ttk.Frame(tab_widgets)
        izq.pack(side="left", fill="y")
        ttk.Label(izq, text="Widgets", style="Hint.TLabel").pack(anchor="w",
                                                                 pady=(0, 4))
        # A Treeview and not a Listbox: the groups are real parent nodes that
        # fold, the row keeps the widget id in its iid instead of hiding it in the
        # text, and it is a ttk widget -- so the theme reaches it. A classic
        # Listbox stays white on a dark window no matter what.
        marco = ttk.Frame(izq)
        marco.pack(fill="both", expand=True)
        self.lista = ttk.Treeview(marco, show="tree", selectmode="browse",
                                  height=26)
        barra = ttk.Scrollbar(marco, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=barra.set)
        self.lista.column("#0", width=232, stretch=True)
        self.lista.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        self.lista.bind("<<TreeviewSelect>>", lambda e: self._on_select())

        botones = ttk.Frame(izq)
        botones.pack(fill="x", pady=(6, 0))
        for tipo in ("text", "label", "bar", "rect"):
            # padx, or the four butt-join into one long bar and stop reading as
            # four separate buttons.
            ttk.Button(botones, text=f"+{tipo}", width=6,
                       command=lambda t=tipo: self._add(t)).pack(side="left",
                                                                 padx=(0, 3))
        # Destructive, and one click away from the four that create: it is named in
        # `error` so the eye does not land on it by accident.
        ttk.Button(izq, text="Delete", style="Danger.TButton",
                   command=self._remove).pack(fill="x", pady=(6, 0))

        centro = ttk.Frame(tab_widgets, padding=(12, 0))
        centro.pack(side="left", fill="both", expand=True)
        self.props = ttk.Frame(centro)
        self.props.pack(fill="both", expand=True)

        flechas = ttk.Frame(centro)
        flechas.pack(fill="x", pady=6)
        ttk.Label(flechas, text="Move", style="Hint.TLabel").pack(side="left",
                                                                  padx=(0, 8))
        # A gap after the 1 px group, so "by one" and "by ten" read as two groups
        # instead of eight identical buttons in a row.
        for i, (texto, (dx, dy)) in enumerate((("←", (-1, 0)), ("→", (1, 0)),
                                               ("↑", (0, -1)), ("↓", (0, 1)),
                                               ("←10", (-10, 0)), ("→10", (10, 0)),
                                               ("↑10", (0, -10)), ("↓10", (0, 10)))):
            ttk.Button(flechas, text=texto, width=4,
                       command=lambda a=dx, b=dy: self._move(a, b)).pack(
                           side="left", padx=(12 if i == 4 else 0, 3))

        # The action bar and the status bar live in the FOOTER, outside the Notebook.
        # They used to be inside the Widgets tab, and from the Background tab there
        # was then neither a save button nor a single message: the user changed the
        # background, could not find where to apply it, restarted the engine -- which
        # re-reads the file, where the change never arrived -- and the change was
        # lost. Reported verbatim: "there is no apply button and it does not save".
        # Bottom-up, because `side="bottom"` stacks in call order: the status line
        # is packed FIRST so it ends up at the very bottom edge, under the buttons.
        # It used to sit above them, jammed against the notebook's border, where it
        # read as a stray caption of the tab rather than as the window's answer to
        # "did that work?".
        self.estado = ttk.Label(self._pie, text="", wraplength=900,
                                justify="left", padding=(10, 6))
        self.estado.pack(side="bottom", fill="x")
        self._acciones = ttk.Frame(self._pie, padding=(10, 8, 10, 4))
        self._acciones.pack(side="bottom", fill="x")
        # A rule between the tabs and the footer: without it the action bar floats
        # on the same flat colour as the tab it belongs to none of.
        ttk.Separator(self._pie, orient="horizontal").pack(side="bottom", fill="x")

        # Save is the primary action and is filled; the other three are ordinary.
        # Equal padx on every button, so the gaps do not depend on which neighbour
        # happened to declare one.
        ttk.Button(self._acciones, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="left", padx=(0, 6))
        ttk.Button(self._acciones, text="Discard changes",
                   command=self._discard).pack(side="left", padx=(0, 6))
        ttk.Button(self._acciones, text="Export…",
                   command=self._pedir_exportar).pack(side="left", padx=(0, 6))
        ttk.Button(self._acciones, text="Import…",
                   command=self._pedir_importar).pack(side="left")

        self._build_fondo()
        self._build_fuentes()
        self._build_panel()

        self.der = ttk.Frame(raiz)
        self.der.pack(side="left", fill="both", expand=True)
        cabecera = ttk.Frame(self.der)
        cabecera.pack(fill="x", pady=(0, 4))
        ttk.Label(cabecera, text="Preview", style="Hint.TLabel").pack(side="left")
        # Off by default. A grid helps place things and gets in the way of judging
        # how the layout LOOKS, which is the other half of what the preview is for.
        self._grilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(cabecera, text="Grid", variable=self._grilla,
                        command=self._draw_preview).pack(side="right")
        self._zoom_lbl = ttk.Label(cabecera, text="", style="Hint.TLabel")
        self._zoom_lbl.pack(side="right", padx=(0, 12))

        # A tk.Canvas and not the tk.Label it used to be: zoomed past the point
        # where the frame fits, the surplus needs somewhere to go, and the canvas
        # brings the scroll region with it. It also lets the grid and the selection
        # outline be canvas ITEMS -- drawn over the frame, never into it, so what
        # --save writes and what the panel receives stay the layout alone.
        # Classic widgets are outside the ttk theme, so its colours are handed over
        # by hand or the preview sits in a white box on a dark window.
        marco_prev = ttk.Frame(self.der)
        marco_prev.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(marco_prev, borderwidth=0,
                                background=self.palette["surface"],
                                highlightthickness=1,
                                highlightbackground=self.palette["border"])
        vsb = ttk.Scrollbar(marco_prev, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(marco_prev, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        marco_prev.rowconfigure(0, weight=1)
        marco_prev.columnconfigure(0, weight=1)
        # The <Configure> is listened for on the CONTAINER, not on the canvas:
        # changing the image changes what the canvas holds and that would fire
        # another Configure -- a redraw loop.
        self.der.bind("<Configure>", self._on_resize)
        # Dragging on the preview: it is the natural way to position things, and a
        # list of 47 names means remembering what each one is called.
        self.canvas.bind("<Button-1>", self._on_preview_press)
        self.canvas.bind("<B1-Motion>", self._on_preview_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_preview_release)
        # Plain wheel scrolls and Ctrl+wheel zooms, as in every other editor. With
        # 1480 px of panel, scrolling is the gesture used most.
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_wheel_h)
        self.canvas.bind("<Control-MouseWheel>", self._on_wheel_zoom)

        self.root.report_callback_exception = self._report_error
        self.root.bind("<Control-s>", lambda e: self._save())
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.root.bind("<Control-Key-0>", self._ajustar_zoom)
        for tecla, (dx, dy) in (("<Left>", (-1, 0)), ("<Right>", (1, 0)),
                                ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.root.bind(tecla, lambda e, a=dx, b=dy: self._nudge(a, b))

    # --- dragging on the preview ---

    def _offset_preview(self):
        """(x, y) of the image's corner inside the canvas's document.

        A frame narrower than its slot is centred, which means a click lands
        several pixels off and grabs the widget next door unless that margin is
        subtracted. Zoomed in there is no margin: the frame is the wider one.
        """
        ancho_canvas = self.canvas.winfo_width()
        ancho_img = self._preview_img.width() if self._preview_img else 0
        return max(0, (ancho_canvas - ancho_img) // 2), 0

    def _a_panel(self, px, py):
        """Viewport coordinates -> panel coordinates (320x1480).

        canvasx/canvasy and not the raw event: with the preview scrolled, the same
        screen position is a DIFFERENT panel point, and every click would grab the
        widget next door. This is the whole reason the scroll offset exists here.
        """
        ox, oy = self._offset_preview()
        k = self._escala or 1.0
        dx, dy = self.canvas.canvasx(px), self.canvas.canvasy(py)
        return int(round((dx - ox) / k)), int(round((dy - oy) / k))

    def _a_pantalla(self, x, y):
        ox, oy = self._offset_preview()
        k = self._escala or 1.0
        dx = int(round(x * k)) + ox - int(self.canvas.canvasx(0))
        dy = int(round(y * k)) + oy - int(self.canvas.canvasy(0))
        return dx, dy

    # --- zoom ---

    def _on_wheel(self, evento):
        self.canvas.yview_scroll(-1 if evento.delta > 0 else 1, "units")
        return "break"

    def _on_wheel_h(self, evento):
        self.canvas.xview_scroll(-1 if evento.delta > 0 else 1, "units")
        return "break"

    def _on_wheel_zoom(self, evento):
        # The panel point under the cursor is read BEFORE the scale changes: it is
        # the thing the user is looking at, and it has to stay put.
        x, y = self._a_panel(evento.x, evento.y)
        self._zoom_a(zoom_step(self._escala, evento.delta > 0),
                     ancla=(x, y, evento.x, evento.y))
        return "break"

    def _zoom_a(self, k, ancla=None):
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, k))
        self._draw_preview()
        if ancla:
            x, y, px, py = ancla
            ancho, alto = self._doc
            self.canvas.xview_moveto(vista_tras_zoom(x, self._escala, px, ancho))
            self.canvas.yview_moveto(vista_tras_zoom(y, self._escala, py, alto))

    def _ajustar_zoom(self, _evento=None):
        """Back to fitting the container. Ctrl+0, as everywhere else."""
        self._zoom = None
        self._draw_preview()
        return "break"

    def _on_preview_press(self, evento):
        x, y = self._a_panel(evento.x, evento.y)
        wid = self.state.hit_test(x, y)
        if wid is None:
            # A click on empty space does NOT deselect: the properties panel would
            # empty out and the user would lose what they were editing.
            return
        self._seleccionar_en_arbol(wid)
        self._show_props()
        self._show_errors()
        self.state.begin_drag(wid, x, y)

    def _on_preview_drag(self, evento):
        x, y = self._a_panel(evento.x, evento.y)
        self.state.drag_to(x, y)
        self._draw_preview()
        self._show_errors()

    def _on_preview_release(self, _evento=None):
        self.state.end_drag()
        # The x/y fields show the old value until they are repainted.
        self._show_props()

    # --- pestana Fondo ---

    def _build_fondo(self):
        ttk = self.ttk
        cab = ttk.Frame(self.tab_fondo)
        cab.pack(fill="x")
        ttk.Label(cab, text="Type", style="Hint.TLabel").pack(side="left",
                                                              padx=(0, 8))
        self._bg_type = self.tk.StringVar()
        combo = ttk.Combobox(cab, textvariable=self._bg_type, width=16,
                             state="readonly", values=self.state.background_types())
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_pick_bg_type())

        self._bg_hint = ttk.Label(self.tab_fondo, text="", wraplength=560,
                                  justify="left", style="Hint.TLabel")
        self._bg_hint.pack(fill="x", pady=(8, 0))

        self._bg_campos = ttk.Frame(self.tab_fondo)
        self._bg_campos.pack(fill="x", pady=(10, 0))

        # `fill="x"` and not `expand=True`: the stops belong under the fields they
        # belong to, not pinned to the bottom of an empty tab.
        self._bg_stops = ttk.Frame(self.tab_fondo)
        self._bg_stops.pack(fill="x")

        self._bg_fields = {}
        self._stop_rows = []

    def _on_pick_bg_type(self):
        self.state.set_background_type(self._bg_type.get())
        self._show_background()
        self._draw_preview()
        self._show_errors()

    def _show_background(self):
        ttk = self.ttk
        fondo = self.state.raw.get("background") or {}
        tipo = fondo.get("type", "solid")
        self._bg_type.set(tipo)

        for hijo in self._bg_campos.winfo_children():
            hijo.destroy()
        self._bg_fields = {}
        for fila, clave in enumerate(self.state.background_fields(tipo)):
            ttk.Label(self._bg_campos, text=clave, style="Hint.TLabel").grid(
                row=fila, column=0, sticky="e", padx=(0, 8), pady=3)
            valor = fondo.get(clave, "")
            if clave == "name":
                control = ttk.Combobox(self._bg_campos, width=14, state="readonly",
                                       values=list(schema.PROCEDURALES))
                control.set(str(valor) or "scroll")
                control.bind("<<ComboboxSelected>>",
                             lambda e, k=clave, c=None: self._apply_bg(clave))
                self._bg_fields[clave] = control
            elif clave == "fit":
                control = ttk.Combobox(self._bg_campos, width=14, state="readonly",
                                       values=sorted(schema.FITS))
                control.set(str(valor) or "cover")
                control.bind("<<ComboboxSelected>>", lambda e, k=clave: self._apply_bg(k))
                self._bg_fields[clave] = control
            else:
                var = self.tk.StringVar(value="" if valor is None else str(valor))
                control = ttk.Entry(self._bg_campos, textvariable=var, width=24)
                control.bind("<FocusOut>", lambda e, k=clave: self._apply_bg(k))
                control.bind("<Return>", lambda e, k=clave: self._apply_bg(k))
                self._pendiente_al_tipear(var, "bg", clave)
                self._bg_fields[clave] = var
            control.grid(row=fila, column=1, sticky="w", pady=3)
            if clave == "src":
                # Beside the field, not somewhere else: it is what most people will
                # use instead of typing a path, and it has to be where the value it
                # replaces is visible.
                self._btn_asset = ttk.Button(self._bg_campos, text="Choose…",
                                             width=9, command=self._pedir_asset)
                self._btn_asset.grid(row=fila, column=2, padx=(6, 0), pady=3)

        self._show_stops()
        self._bg_hint.config(text=pista_fondo(tipo))

    # --- choosing the background file ---

    def _pedir_asset(self):
        """Dialog for choosing the background's video, image or folder."""
        from tkinter import filedialog
        tipo = self._bg_type.get()
        if tipo == "sequence":
            elegido = filedialog.askdirectory(
                parent=self.root, title="Folder holding the sequence frames")
        else:
            filtros = ([("Video", "*.mp4 *.webm *.mkv *.gif *.avi *.mov")]
                       if tipo == "video" else
                       [("Imagen", "*.png *.jpg *.jpeg *.bmp *.gif")])
            elegido = filedialog.askopenfilename(
                parent=self.root, title="Background file",
                filetypes=filtros + [("All files", "*.*")])
        if elegido:
            self._usar_asset(Path(elegido))

    def _usar_asset(self, origen, assets_dir=None):
        """Makes `origen` available as an asset and writes it into `src`.
        -> name | None.

        **It copies the file into vmaxpanel/assets when it is outside**, and that is
        the whole point: `safe_asset_path` rejects any path escaping that directory
        -- rightly so, the process runs elevated -- so choosing a video from the
        Desktop can ONLY work by copying it. Without this the editor would save a
        path the engine rejects and the background would sit at a flat colour with
        nothing
        lo explique.
        """
        import shutil
        origen = Path(origen)
        destino_raiz = Path(assets_dir) if assets_dir else self._carpetas()[1]
        if not origen.exists():
            self.estado.config(text=f"{origen.name} does not exist", foreground=self.palette["error"])
            return None
        try:
            destino_raiz.mkdir(parents=True, exist_ok=True)
            adentro = self._ya_esta_adentro(origen, destino_raiz)
            if adentro is not None:
                nombre = adentro
            else:
                nombre = self._copiar_asset(origen, destino_raiz, shutil)
        except OSError as e:
            self.estado.config(text=f"could not copy {origen.name}: {e}",
                               foreground=self.palette["error"])
            return None

        self.state.set_background_field("src", nombre)
        if "src" in getattr(self, "_bg_fields", {}):
            self._bg_fields["src"].set(nombre)
        self._pendientes.discard(("bg", "src"))
        self._draw_preview()
        self._show_errors()
        self.estado.config(text=f"background: {nombre}", foreground=self.palette["ok"])
        return nombre

    @staticmethod
    def _ya_esta_adentro(origen, raiz):
        """El nombre relativo si `origen` ya vive bajo `raiz`, o None.

        With / and not os.sep: it goes into a JSON shared between machines, and
        safe_asset_path normalises both forms, but the file reads better this way.
        """
        try:
            return origen.resolve().relative_to(raiz.resolve()).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _copiar_asset(origen, raiz, shutil):
        """Copies (a file or a folder) and returns the name it ended up with.

        If the name already exists with DIFFERENT content it is renamed to `-2`:
        overwriting another profile's asset destroys work over a repeated name. If it
        exists with the SAME content it is reused, so pressing the button twice does
        not leave two
        copias identicas.
        """
        destino = raiz / origen.name
        i = 2
        while destino.exists():
            if _mismo_contenido(origen, destino):
                return destino.relative_to(raiz).as_posix()
            destino = raiz / f"{origen.stem}-{i}{origen.suffix}"
            i += 1
        if origen.is_dir():
            shutil.copytree(origen, destino)
        else:
            shutil.copy2(origen, destino)
        return destino.relative_to(raiz).as_posix()

    def _apply_bg(self, clave):
        control = self._bg_fields.get(clave)
        if control is None:
            return
        valor = control.get()
        self.state.set_background_field(clave, valor)
        self._draw_preview()
        self._show_errors()

    # --- gradient stops ---

    def _show_stops(self):
        ttk = self.ttk
        for hijo in self._bg_stops.winfo_children():
            hijo.destroy()
        self._stop_rows = []
        if not self.state.has_stops():
            return
        ttk.Label(self._bg_stops, text="GRADIENT STOPS", style="Hint.TLabel").grid(
            row=0, column=0, columnspan=5, sticky="w", pady=(16, 6))
        # A header, because "0.0" and "#101725" do not say what they are. Column 0
        # holds the index and is padded so the boxes land on the same left rail as
        # the fields above -- the two blocks used to start 25 px apart.
        for col, texto in ((1, "at"), (2, "colour")):
            ttk.Label(self._bg_stops, text=texto, style="Hint.TLabel").grid(
                row=1, column=col, sticky="w", padx=(2, 0))
        for i, parada in enumerate(self.state.stops()):
            fila = i + 2
            ttk.Label(self._bg_stops, text=f"{i}", style="Hint.TLabel").grid(
                row=fila, column=0, padx=(0, 8), pady=3)
            at = self.tk.StringVar(value=str(parada.get("at", 0)))
            color = self.tk.StringVar(value=str(parada.get("color", "#000000")))
            e1 = ttk.Entry(self._bg_stops, textvariable=at, width=8)
            e2 = ttk.Entry(self._bg_stops, textvariable=color, width=12)
            e1.grid(row=fila, column=1, padx=(0, 4), pady=3)
            e2.grid(row=fila, column=2, padx=(0, 4), pady=3)
            for control, clave in ((e1, "at"), (e2, "color")):
                control.bind("<FocusOut>",
                             lambda e, j=i, k=clave: self._apply_stop(j, k))
                control.bind("<Return>",
                             lambda e, j=i, k=clave: self._apply_stop(j, k))
            # pady, or the three remove buttons stack edge to edge into one tall
            # rectangle that reads as a panel rather than as three buttons.
            ttk.Button(self._bg_stops, text="−", width=3,
                       command=lambda j=i: self._remove_stop(j)).grid(
                           row=fila, column=3, pady=3)
            self._stop_rows.append({"at": at, "color": color})
        ttk.Button(self._bg_stops, text="+ stop",
                   command=self._add_stop).grid(row=len(self._stop_rows) + 2,
                                                column=1, columnspan=2,
                                                sticky="w", pady=(6, 0))

    def _apply_stop(self, i, clave):
        if not 0 <= i < len(self._stop_rows):
            return
        self.state.set_stop(i, clave, self._stop_rows[i][clave].get())
        self._draw_preview()
        self._show_errors()

    def _add_stop(self):
        self.state.add_stop()
        self._show_stops()
        self._draw_preview()
        self._show_errors()

    def _remove_stop(self, i):
        errores = self.state.remove_stop(i)
        self._show_stops()
        self._draw_preview()
        if errores:
            self.estado.config(text=" / ".join(errores), foreground=self.palette["error"])
        else:
            self._show_errors()

    # --- pestana Fuentes ---

    def _build_fuentes(self):
        ttk = self.ttk
        ttk.Label(self.tab_fuentes,
                  text="Fonts are requested by FAMILY, not by file: profiles get "
                       "shared and Consolas is not redistributable.\n"
                       "A family that is not installed falls back to the "
                       "default font silently, which is why the combo\nonly "
                       "offers the ones present on this machine.",
                  justify="left", style="Hint.TLabel").pack(anchor="w",
                                                            pady=(0, 12))
        # `fill="x"` and not `expand=True`: expanding pinned the "add" row to the
        # bottom of the tab, 500 px below the list it adds to -- while the Background
        # tab puts "+ stop" right under its list. Same gesture, two places.
        self._font_grid = ttk.Frame(self.tab_fuentes)
        self._font_grid.pack(fill="x")
        agregar = ttk.Frame(self.tab_fuentes)
        agregar.pack(fill="x", pady=(12, 0))
        self._font_nuevo = self.tk.StringVar()
        # Labelled: an empty box beside "+ alias" does not say what goes in it.
        ttk.Label(agregar, text="New alias", style="Hint.TLabel").pack(
            side="left", padx=(0, 8))
        ttk.Entry(agregar, textvariable=self._font_nuevo, width=16).pack(side="left")
        ttk.Button(agregar, text="+ alias",
                   command=self._add_font).pack(side="left", padx=(6, 0))
        self._font_rows = {}
        self._familias = None

    def _show_fonts(self):
        ttk = self.ttk
        for hijo in self._font_grid.winfo_children():
            hijo.destroy()
        self._font_rows = {}
        if self._familias is None:
            # Once only: indexing the system fonts walks directories.
            self._familias = self.state.font_families()
        # A header row: without it "60" and "14" are unlabelled numbers, and which
        # column is the size is a guess.
        for col, texto in ((0, "alias"), (1, "family"), (2, "size"), (4, "used by")):
            ttk.Label(self._font_grid, text=texto, style="Hint.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0, 6), pady=(0, 4))
        for i, alias in enumerate(self.state.fonts()):
            fila = i + 1
            spec = self.state.raw["fonts"][alias]
            ttk.Label(self._font_grid, text=alias, width=12).grid(
                row=fila, column=0, sticky="w", padx=(0, 6), pady=3)
            familia = ttk.Combobox(self._font_grid, width=26, state="readonly",
                                   values=self._familias)
            familia.set(str(spec.get("family", "")))
            familia.grid(row=fila, column=1, padx=(0, 6), pady=3)
            familia.bind("<<ComboboxSelected>>",
                         lambda e, a=alias: self._apply_font(a, "family"))

            size = self.tk.StringVar(value=str(spec.get("size", "")))
            entrada = ttk.Entry(self._font_grid, textvariable=size, width=6)
            entrada.grid(row=fila, column=2, padx=(0, 6), pady=3)
            for evento in ("<FocusOut>", "<Return>"):
                entrada.bind(evento, lambda e, a=alias: self._apply_font(a, "size"))

            bold = self.tk.BooleanVar(value=bool(spec.get("bold")))
            ttk.Checkbutton(self._font_grid, text="bold", variable=bold,
                            command=lambda a=alias: self._apply_font(a, "bold")
                            ).grid(row=fila, column=3, padx=(0, 12), pady=3)

            usuarios = len(self.state.font_users(alias))
            # "1 widgets" was on screen seven rows at a time.
            ttk.Label(self._font_grid, style="Hint.TLabel",
                      text=f"{usuarios} widget" + ("" if usuarios == 1 else "s")
                      ).grid(row=fila, column=4, sticky="w", padx=(0, 12), pady=3)
            ttk.Button(self._font_grid, text="−", width=3,
                       command=lambda a=alias: self._remove_font(a)
                       ).grid(row=fila, column=5, pady=3)
            self._font_rows[alias] = {"family_combo": familia, "family": familia,
                                      "size": size, "bold": bold}

    def _apply_font(self, alias, clave):
        fila = self._font_rows.get(alias)
        if fila is None:
            return
        control = fila[clave]
        valor = control.get() if hasattr(control, "get") else control
        self.state.set_font_field(alias, clave, valor)
        self._draw_preview()
        self._show_errors()

    def _add_font(self):
        errores = self.state.add_font(self._font_nuevo.get())
        self._font_nuevo.set("")
        self._show_fonts()
        self._draw_preview()
        if errores:
            self.estado.config(text=" / ".join(errores), foreground=self.palette["error"])
        else:
            self._show_errors()

    def _remove_font(self, alias):
        errores = self.state.remove_font(alias)
        self._show_fonts()
        self._draw_preview()
        if errores:
            self.estado.config(text=" / ".join(errores), foreground=self.palette["error"])
        else:
            self._show_errors()

    # --- pestana Panel ---

    def _build_panel(self):
        ttk = self.ttk
        self._panel_fields = {}
        ttk.Label(self.tab_panel,
                  text="The panel refreshes at 60 Hz: above that, frames are "
                       "discarded.\nMeasured cost: 1 fps ≈ 1% of one core, "
                       "30 ≈ 17%, 60 ≈ 37%.",
                  justify="left", style="Hint.TLabel").pack(anchor="w", pady=(0, 12))
        campos = ttk.Frame(self.tab_panel)
        campos.pack(fill="x")
        # The range beside the name. Four bare numbers with no units left the user
        # to find out from the validator that brightness is not 0-255 and that
        # rotate is not free.
        unidades = {"fps": "frames per second, 1-60",
                    "brightness": "0-100 %", "rotate": "degrees: 0, 90, 180, 270",
                    "jpeg_quality": "1-100; the panel is fed JPEG"}
        for fila, clave in enumerate(self.state.panel_fields()):
            ttk.Label(campos, text=clave, style="Hint.TLabel").grid(
                row=fila, column=0, sticky="e", padx=(0, 8), pady=3)
            var = self.tk.StringVar()
            entrada = ttk.Entry(campos, textvariable=var, width=12)
            entrada.grid(row=fila, column=1, sticky="w", pady=3)
            entrada.bind("<FocusOut>", lambda e, k=clave: self._apply_panel(k))
            entrada.bind("<Return>", lambda e, k=clave: self._apply_panel(k))
            ttk.Label(campos, text=unidades.get(clave, ""),
                      style="Hint.TLabel").grid(row=fila, column=2, sticky="w",
                                                padx=(10, 0), pady=3)
            self._pendiente_al_tipear(var, "panel", clave)
            self._panel_fields[clave] = var

    def _show_panel(self):
        panel = self.state.raw.get("panel") or {}
        for clave, var in self._panel_fields.items():
            var.set(str(panel.get(clave, "")))

    def _apply_panel(self, clave):
        self.state.set_panel_field(clave, self._panel_fields[clave].get())
        self._draw_preview()
        self._show_errors()

    # --- refresco ---

    # Prefix for the group nodes' iids. A widget id could collide with a group
    # name, and then selecting a section would edit a widget: the prefix keeps
    # the two namespaces apart, and `_selected()` uses it to tell them apart.
    # PRINTABLE. It used to start with a NUL byte, and a Tcl string is not
    # binary-safe the way a Python one is: the Tk shipped with Python 3.11
    # truncates at the NUL, so
    # every group row got the same empty iid and the second insert died with
    # "Item  already exists", taking the editor window down with it. Python 3.13's
    # newer Tk tolerated it, which is why the suite was green here and red on the
    # minimum version the project promises.
    GRUPO = "::group::"

    def _seleccionar_en_arbol(self, wid):
        """Selects `wid`, opening its group and scrolling to it.

        see() alone is not enough: a row inside a folded group is not visible at
        all, so selecting it from the preview would look like nothing happened.
        """
        if not self.lista.exists(wid):
            return
        padre = self.lista.parent(wid)
        if padre:
            self.lista.item(padre, open=True)
        self.lista.selection_set(wid)
        self.lista.see(wid)

    def _selected(self):
        """The selected widget's id, or None on a group node.

        The Treeview's iid IS the widget id, so nothing has to be parsed back out
        of the row text -- which is what made showing a friendly name impossible
        before. Group nodes carry a prefixed iid and read as no selection.
        """
        sel = self.lista.selection()
        # Membership, not startswith(): the prefix is a convention and a widget may
        # be called anything, so the ids actually inserted as group rows are what
        # tells the two apart.
        if not sel or sel[0] in self._grupos:
            return None
        return sel[0]

    def _on_select(self):
        """Selection changed in the list.

        This was missing entirely: the bind existed and this method did not, so every
        click raised an AttributeError that Tkinter prints to stderr and swallows.
        Under pythonw -- which is how the tray opens it -- that goes nowhere: the
        properties panel just kept showing the first widget forever, with no visible
        error at all.
        """
        self._show_props()
        self._show_errors()
        # So the outline on the preview follows the selection.
        self._draw_preview()

    def _report_error(self, exc_type, exc, tb):
        """An exception from a Tkinter callback, made visible instead of lost.

        The default prints to stderr and carries on, which under pythonw is an
        invisible failure. It is shown in the status bar and re-emitted to the log.
        """
        texto = f"internal error: {exc_type.__name__}: {exc}"
        try:
            self.estado.config(text=texto, foreground=self.palette["error"])
        except Exception:
            pass
        print(texto, file=sys.stderr)
        if tb is not None:
            traceback.print_exception(exc_type, exc, tb, file=sys.stderr)

    def _refresh(self, select_first=False, keep=None):
        keep = keep or self._selected()
        # Which groups the user had folded. Every edit triggers a refresh, so
        # rebuilding them all open would undo a fold on the next keystroke.
        plegados = {g for g in self.lista.get_children("")
                    if not self.lista.item(g, "open")}
        self.lista.delete(*self.lista.get_children(""))
        ids = self.state.widget_ids()
        self._grupos = set()
        for grupo, filas in self.state.widget_tree():
            gid = self.GRUPO + grupo
            # A widget calling itself "::group::CPU" would collide with the iid Tk
            # keys on and insert() would raise. Suffixing until it is free costs
            # nothing and cannot fail.
            while gid in ids or gid in self._grupos:
                gid += "_"
            self._grupos.add(gid)
            self.lista.insert("", "end", iid=gid, text=grupo,
                              open=gid not in plegados)
            for wid, etiqueta in filas:
                self.lista.insert(gid, "end", iid=wid, text=etiqueta)
        objetivo = keep if keep in ids else (ids[0] if (select_first and ids) else None)
        if objetivo is not None:
            self._seleccionar_en_arbol(objetivo)
        self._show_props()
        self._show_background()
        self._show_fonts()
        self._show_panel()
        self._draw_preview()
        self._show_errors()

    def _show_errors(self):
        marca = "•" if self.state.dirty else ""
        if self.state.errors:
            self.estado.config(text=f"{marca} " + " / ".join(self.state.errors[:3]),
                               foreground=self.palette["error"])
        else:
            self.estado.config(text=f"{marca} no errors", foreground=self.palette["ok"])

    def _escala_disponible(self) -> float:
        """The largest scale at which the whole frame fits in its slot.

        The container is measured and not the window: that way the preview uses
        whatever is spare when the user maximises, which is the whole point of an
        editor -- judging a layout in miniature is useless.

        Capped at 1.0: beyond that is blurry upscaling, and 1480 px of height does
        not fit on a 1080 screen anyway.
        """
        # The header row plus the horizontal scrollbar, and the vertical one plus
        # the canvas border. Measured allowances rather than the canvas's own size:
        # asking the canvas would feed its size back into the scale that sets it.
        alto = self.der.winfo_height() - 52
        ancho = self.der.winfo_width() - 22
        d = self.state.raw.get("designed_for") or {}
        pw = float(d.get("width") or 320) or 320
        ph = float(d.get("height") or 1480) or 1480
        if alto <= 1 or ancho <= 1:
            return PREVIEW_SCALE                    # no real geometry yet
        return max(0.05, min(1.0, ancho / pw, alto / ph))

    def _on_resize(self, _evento=None):
        """Redraws only if the scale really changed.

        A resize fires many <Configure> events in a row and each redraw means
        rescaling a 320x1480 image and converting it to a PhotoImage. The 2%
        threshold cuts the noise without the jump being noticeable.
        """
        if self._zoom is not None:
            return              # the user set the scale by hand; resizing is not a vote
        nueva = self._escala_disponible()
        if abs(nueva - self._escala) / max(nueva, self._escala) > 0.02:
            self._escala = nueva
            self._draw_preview()

    def _draw_preview(self):
        # The title is updated here and not on every mutation: _draw_preview() is the
        # common path of ALL of them -- moving, editing a field, changing the
        # background, undoing -- so it is the one place nobody has to remember to add
        # it to.
        self._marcar_titulo()
        img = self.state.preview()
        # A manual zoom OUTRANKS the fit. Recomputing it unconditionally here is
        # what made zooming impossible before: this method runs on every edit, so
        # the next keystroke wiped whatever the wheel had set.
        self._escala = (self._escala_disponible() if self._zoom is None
                        else self._zoom)
        dims = (max(1, int(img.width * self._escala)),
                max(1, int(img.height * self._escala)))
        chico = img.resize(dims, Image.LANCZOS)
        # A PhotoImage with no live reference gets collected and the preview goes
        # blank: the classic Tkinter-with-images trap.
        self._preview_img = _to_photoimage(chico, self.tk)
        self.canvas.delete("all")
        ox, oy = self._offset_preview()
        self.canvas.create_image(ox, oy, anchor="nw", image=self._preview_img,
                                 tags="frame")
        self._doc = (ox * 2 + dims[0], dims[1])
        self.canvas.configure(scrollregion=(0, 0, self._doc[0], self._doc[1]))
        self._dibujar_grilla(ox, oy)
        self._dibujar_seleccion(ox, oy)
        self._zoom_lbl.config(text=f"{self._escala * 100:.0f}%")

    def _dibujar_grilla(self, ox, oy):
        """The grid, over the frame and never into it.

        The fine step is dropped below 12 px on screen: at the scale the preview
        opens at it lands 11 px apart, and a solid mesh that close hides the layout
        it is supposed to help position -- which was visible the moment somebody
        looked at a capture instead of at the passing tests.

        It is also stippled. Tk canvas lines have no alpha, and a flat `muted` grey
        over a mostly-black panel is louder than the design underneath; a gray25
        stipple reads as roughly a quarter of the ink.
        """
        if not self._grilla.get():
            return
        k = self._escala or 1.0
        d = self.state.raw.get("designed_for") or {}
        pw, ph = int(d.get("width") or 320), int(d.get("height") or 1480)
        pasos = [(GRILLA_GRUESA, self.palette["accent"], "")]
        if GRILLA_FINA * k >= 12:
            pasos.insert(0, (GRILLA_FINA, self.palette["muted"], "gray25"))
        for paso, color, trama in pasos:
            verticales, horizontales = lineas_grilla(pw, ph, paso)
            for x in verticales:
                sx = x * k + ox
                self.canvas.create_line(sx, oy, sx, ph * k + oy, fill=color,
                                        stipple=trama, tags="grilla")
            for y in horizontales:
                sy = y * k + oy
                self.canvas.create_line(ox, sy, pw * k + ox, sy, fill=color,
                                        stipple=trama, tags="grilla")

    def _dibujar_seleccion(self, ox, oy):
        """Outlines the selected widget.

        Without it there is no telling which of a hundred widgets the properties
        panel is describing, which is most of what the preview is for. No resize
        handles: there is no resize gesture, and drawing them would promise one.
        """
        self._sel_rect = None
        wid = self._selected()
        if not wid:
            return
        caja = self.state.widget_bbox(wid)
        if not caja:
            return
        k = self._escala or 1.0
        self._sel_rect = self.canvas.create_rectangle(
            caja[0] * k + ox, caja[1] * k + oy,
            caja[2] * k + ox, caja[3] * k + oy,
            outline=self.palette["accent"], width=2, tags="seleccion")

    def _show_props(self):
        for hijo in self.props.winfo_children():
            hijo.destroy()
        self._fields = {}
        wid = self._selected()
        w = self.state.widget(wid) if wid else None
        if w is None:
            return
        self._pickers = {}
        for fila, (clave, valor) in enumerate(w.items()):
            if clave in ("id", "type", "rules"):
                continue
            # Labels right-aligned against their field, and every row given the
            # same vertical air. Left-aligned in a column as wide as the longest
            # name, "x" ended up 90 px from its box while "jpeg_quality" touched
            # its own; and with no pady the entries butt-joined into one slab
            # instead of reading as separate fields.
            self.ttk.Label(self.props, text=clave, style="Hint.TLabel").grid(
                row=fila, column=0, sticky="e", padx=(0, 8), pady=3)
            if clave == "metric":
                self._metric_picker(fila, valor)
                continue
            if clave == "font":
                self._font_picker(fila, valor)
                continue
            var = self.tk.StringVar(value="" if valor is None else str(valor))
            entrada = self.ttk.Entry(self.props, textvariable=var, width=32)
            # sticky="w", with no weight on the column: with the window maximised, a
            # field that expands leaves a 1500 px box for typing
            # "18".
            entrada.grid(row=fila, column=1, sticky="w", pady=3)
            entrada.bind("<FocusOut>", lambda e, k=clave: self._apply(k))
            entrada.bind("<Return>", lambda e, k=clave: self._apply(k))
            self._pendiente_al_tipear(var, "widget", clave)
            self._fields[clave] = var
        self._show_rules(len(w) + 1)

    # --- reglas de color ---

    def _show_rules(self, fila_base):
        """The rules editor, below the properties.

        Only for text widgets: they are the only ones with rules in this
        engine, and showing the section on a bar would promise something that does
        not exist.
        """
        ttk = self.ttk
        # Nothing has to be destroyed here: _show_props() already deleted every child
        # of self.props, and the rules frame is one of them. An earlier version kept
        # a reference and asked an already-destroyed widget for its children, which
        # is "bad window path name".
        self._rule_rows = []
        wid = self._selected()
        w = self.state.widget(wid) if wid else None
        if w is None or w.get("type") != "text":
            return

        marco = ttk.Frame(self.props)
        marco.grid(row=fila_base, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._rules_frame = marco
        ttk.Label(marco, text="COLOUR BY VALUE", style="Hint.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for i, regla in enumerate(self.state.rules(wid)):
            op = ttk.Combobox(marco, width=4, state="readonly",
                              values=self.state.rule_operators())
            op.set(regla["op"])
            op.grid(row=i + 1, column=0, padx=(0, 4), pady=3)
            op.bind("<<ComboboxSelected>>", lambda e, j=i: self._apply_rule(j, "op"))

            valor = self.tk.StringVar(value=regla["value"])
            color = self.tk.StringVar(value=regla["color"])
            e_valor = ttk.Entry(marco, textvariable=valor, width=8)
            e_color = ttk.Entry(marco, textvariable=color, width=10)
            e_valor.grid(row=i + 1, column=1, padx=(0, 4), pady=3)
            e_color.grid(row=i + 1, column=2, padx=(0, 4), pady=3)
            for control, campo in ((e_valor, "value"), (e_color, "color")):
                for evento in ("<FocusOut>", "<Return>"):
                    control.bind(evento,
                                 lambda e, j=i, c=campo: self._apply_rule(j, c))
            ttk.Button(marco, text="−", width=3,
                       command=lambda j=i: self._remove_rule(j)).grid(row=i + 1,
                                                                     column=3,
                                                                     pady=3)
            self._rule_rows.append({"op": op, "value": valor, "color": color})
        ttk.Button(marco, text="+ rule", command=self._add_rule).grid(
            row=len(self._rule_rows) + 1, column=0, columnspan=3, sticky="w",
            pady=(4, 0))

    def _apply_rule(self, i, campo):
        wid = self._selected()
        if wid is None or not 0 <= i < len(self._rule_rows):
            return
        control = self._rule_rows[i][campo]
        valor = control.get()
        errores = self.state.set_rule(wid, i, campo, valor)
        # It always repaints: a rejected rule is reverted in the state, so the
        # control has to go back to showing the value that stuck and not the one the
        # user typed.
        self._show_props()
        self._draw_preview()
        if errores:
            self.estado.config(text=" / ".join(errores), foreground=self.palette["error"])
        else:
            self._show_errors()

    def _add_rule(self):
        wid = self._selected()
        if wid is None:
            return
        self.state.add_rule(wid)
        self._show_props()
        self._draw_preview()
        self._show_errors()

    def _remove_rule(self, i):
        wid = self._selected()
        if wid is None:
            return
        self.state.remove_rule(wid, i)
        self._show_props()
        self._draw_preview()
        self._show_errors()

    # --- acciones ---

    # --- selectores ---

    _ENCABEZADO = "——"

    def _metric_picker(self, fila, actual):
        """A metric combo with friendly labels, grouped by device.

        It used to be a free text field: to place D's free space you had to remember
        that the id is `vol.D.free`. The group headings go in as non-selectable items
        because ttk.Combobox has no real groups; _on_pick_metric() ignores them.
        """
        opciones, self._metric_por_etiqueta = [], {}
        for dispositivo, entradas in self.state.metric_groups().items():
            opciones.append(f"{self._ENCABEZADO} {dispositivo} {self._ENCABEZADO}")
            for mid, etiqueta in entradas:
                texto = f"   {etiqueta}"
                opciones.append(texto)
                self._metric_por_etiqueta[texto] = mid

        combo = self.ttk.Combobox(self.props, values=opciones, width=44,
                                  state="readonly")
        actuales = [t for t, mid in self._metric_por_etiqueta.items() if mid == actual]
        combo.set(actuales[0].strip() if actuales else (actual or ""))
        if actuales:
            combo.set(actuales[0])
        combo.grid(row=fila, column=1, sticky="w", pady=3)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_pick_metric())
        self._pickers["metric"] = combo

    def _on_pick_metric(self):
        wid = self._selected()
        combo = self._pickers.get("metric")
        if wid is None or combo is None:
            return
        mid = self._metric_por_etiqueta.get(combo.get())
        if mid is None:
            # A group heading, or something not in the catalogue: whatever the widget
            # already had is put back instead of writing garbage.
            actual = (self.state.widget(wid) or {}).get("metric")
            actuales = [t for t, m in self._metric_por_etiqueta.items() if m == actual]
            if actuales:
                combo.set(actuales[0])
            return
        self.state.set_field(wid, "metric", mid)
        self._draw_preview()
        self._show_errors()

    def _font_picker(self, fila, actual):
        """The font aliases are a closed set belonging to the layout itself: a combo
        prevents the mistake of typing an alias that does not exist."""
        combo = self.ttk.Combobox(self.props, values=self.state.fonts(),
                                  width=20, state="readonly")
        combo.set(actual or "")
        combo.grid(row=fila, column=1, sticky="w", pady=3)

        def elegido(_e=None):
            wid = self._selected()
            if wid is not None:
                self.state.set_field(wid, "font", combo.get())
                self._draw_preview()
                self._show_errors()

        combo.bind("<<ComboboxSelected>>", elegido)
        self._pickers["font"] = combo

    def _apply(self, clave):
        wid = self._selected()
        if wid is None:
            return
        self.state.set_field(wid, clave, self._fields[clave].get())
        self._draw_preview()
        self._show_errors()

    def _nudge(self, dx, dy):
        """Arrow keys: they move the widget, unless you are typing.

        The bind is on the window, so without this filter the arrow reaches both
        places: you correct a digit in the `x` field and the widget shifts a pixel
        along with it. That is layout corruption nobody notices.
        """
        foco = self.root.focus_get()
        if foco is not None and foco.winfo_class() in ("TEntry", "Entry"):
            return
        self._move(dx, dy)

    def _move(self, dx, dy):
        wid = self._selected()
        if wid is None:
            return
        self.state.move_widget(wid, dx, dy)
        self._show_props()
        self._draw_preview()
        self._show_errors()

    def _add(self, tipo):
        base = f"{tipo}-nuevo"
        wid, n = base, 2
        while wid in self.state.widget_ids():
            wid, n = f"{base}{n}", n + 1
        self.state.add_widget(tipo, wid)
        self._refresh(keep=wid)

    def _remove(self):
        wid = self._selected()
        if wid is None:
            return
        self.state.remove_widget(wid)
        self._refresh(select_first=True)

    def _save(self):
        self._aplicar_pendientes()
        errores = self.state.save()
        self._show_errors()
        self._marcar_titulo()
        if not errores:
            self.estado.config(text="saved; the panel picks it up on its own",
                               foreground=self.palette["ok"])

    def _pendiente_al_tipear(self, var, tipo, clave):
        """Marks (type, key) as unconfirmed as soon as the text changes.

        The VARIABLE is followed and not the keyboard: a write trace covers typing,
        pasting with the mouse, dragging text and autocompletion; `<KeyRelease>` only
        covers keys -- and on top of that cannot be simulated without a keysym, so it
        also cannot be
        podia probar.

        The trace is added AFTER creating the variable with its initial value, so
        rebuilding a panel marks nothing: only a later change marks it, and that is
        always the user's.

        This is what allows committing ONLY what the user touched on save. Both
        alternatives are worse: re-reading every control resurrects old values from
        tabs that are not on screen -- undoing a background change and saving would
        reapply it -- and depending on system focus fails exactly when the window
        does not have the focus.
        """
        var.trace_add("write",
                      lambda *_, t=tipo, k=clave: self._pendientes.add((t, k)))

    def _aplicar_pendientes(self):
        """Confirma lo tipeado y no aplicado, antes de guardar.

        Entries apply on <Return> or <FocusOut>. A direct click on Save does not
        always fire either of them -- it depends on whether the button takes focus --
        and then the OLD value of the field just typed into was saved. Silently,
        which is the worst part: the file stays valid, the panel reloads it and
        nothing changes. It is the bug the user reported: change the background, fail
        to find where to apply it, and watch restarting the engine "ignore" the
        change.
        """
        handlers = {"widget": self._apply, "bg": self._apply_bg,
                    "panel": self._apply_panel}
        for tipo, clave in sorted(self._pendientes):
            handler = handlers.get(tipo)
            if handler is None:
                continue
            try:
                handler(clave)
            except Exception:
                # A control that no longer exists (the selection changed, the tab was
                # rebuilt) must not stop the rest from being saved.
                pass
        self._pendientes.clear()

    def _marcar_titulo(self):
        """The title says whether there are unsaved changes.

        Without that signal, restarting the engine from the tray looks like it is
        ignoring the edit -- when what is happening is that the edit never reached
        the disk, because the engine re-reads the file. It is exactly the symptom the
        user reported.
        """
        base = f"Layout editor — {self.state.path.name}"
        self.root.title(base + (" • unsaved changes" if self.state.dirty
                                else ""))

    def _undo(self):
        """Ctrl+Z. It repaints EVERYTHING: an undo may have changed the background,
        the fonts or the widget list, not only the field being edited. Without
        repainting, the controls keep showing the undone value."""
        if not self.state.undo():
            self.estado.config(text="there is nothing to undo",
                               foreground=self.palette["muted"])
            return
        self._refresh()

    def _discard(self):
        # Anything typed and unconfirmed is discarded too: it is exactly what the
        # button promises, and leaving it pending would make it reappear on the next
        # save.
        self._pendientes.clear()
        self.state.reload()
        self._refresh(select_first=True)

    # --- exportar / importar ---
    #
    # The logic lives in bundle.py; here is the glue and the two messages that
    # matter: why it was not exported, and where what was imported ended up.

    def _carpetas(self):
        """The project's (profiles, assets).

        They are derived from the package and not from the open profile: the profile
        can be anywhere -- the tests open it from a tmp_path -- but a bundle's assets
        always go to vmaxpanel/assets, which is the only place the engine looks for
        them.
        """
        from .cli import assets_dir, profiles_dir
        return profiles_dir(), assets_dir()

    def _pedir_exportar(self):
        from tkinter import filedialog
        sugerido = f"{self.state.path.stem}{bundle.EXT}"
        destino = filedialog.asksaveasfilename(
            parent=self.root, title="Export profile",
            initialfile=sugerido, defaultextension=bundle.EXT,
            filetypes=[("VMax Panel profile", f"*{bundle.EXT}")])
        if destino:
            self._exportar_a(Path(destino))

    def _exportar_a(self, destino):
        destino = Path(destino)
        if self.state.dirty:
            # Exporting reads the FILE, not what is on screen. With unsaved changes
            # the bundle would carry the old version, and that error is not noticed
            # until somebody else opens it.
            self.estado.config(text="there are unsaved changes: save first, then "
                                    "export", foreground=self.palette["warn"])
            return
        if destino.exists():
            # asksaveasfilename already asks, but this method is also called
            # directly: overwriting a bundle the user may already have shared cannot
            # depend on the dialog having asked.
            self.estado.config(text=f"{destino.name} already exists: pick another name",
                               foreground=self.palette["warn"])
            return
        try:
            info = bundle.export_profile(self.state.path, destino, self._carpetas()[1])
        except bundle.BundleError as e:
            self.estado.config(text=str(e), foreground=self.palette["error"])
            return
        assets = ", ".join(info["assets"]) or "no assets"
        self.estado.config(
            text=f"exported to {destino.name} ({assets}). Fonts do not travel: "
                 f"they are requested by family.", foreground=self.palette["ok"])

    def _pedir_importar(self):
        from tkinter import filedialog
        origen = filedialog.askopenfilename(
            parent=self.root, title="Import profile",
            filetypes=[("VMax Panel profile", f"*{bundle.EXT}"),
                       ("All files", "*.*")])
        if origen:
            self._importar_de(Path(origen))

    def _importar_de(self, origen, profiles_dir=None, assets_dir=None):
        """Imports and switches to editing the imported profile.

        Importar y no abrirlo dejaria al usuario adivinando si funciono.
        """
        perfiles, assets = self._carpetas()
        try:
            info = bundle.import_bundle(origen, profiles_dir or perfiles,
                                        assets_dir or assets, si_existe="renombrar")
        except bundle.BundleError as e:
            self.estado.config(text=str(e), foreground=self.palette["error"])
            return
        self.state = EditorState(info["profile"])
        self.state.reload()
        self._refresh(select_first=True)
        faltan = info["fuentes_faltantes"]
        aviso = (f" Careful: you do not have {', '.join(faltan)}, so it looks different."
                 if faltan else "")
        self.estado.config(
            text=f"imported as {info['profile'].name}, and opened.{aviso}",
            foreground=self.palette["warn"] if faltan else self.palette["ok"])

    def run(self):
        self.root.mainloop()


def _to_photoimage(img, tk):
    """PIL -> PhotoImage.

    Pillow's `ImageTk` is preferred, but not depended on: it ships with the package
    and can still be absent depending on how Tk's binding was compiled. The fallback
    is base64 PNG, which Tk 8.6 reads natively -- PPM would also be native as a
    file, but PhotoImage(data=...) does not recognise it.
    """
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(img)
    except Exception:
        import base64
        import io
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"))


def main(argv=None) -> int:
    from .cli import default_profile_path

    ap = argparse.ArgumentParser(prog="vmaxpanel-editor")
    ap.add_argument("--profile", type=Path, default=default_profile_path())
    a = ap.parse_args(argv)

    state = EditorState(a.profile)
    try:
        EditorWindow(state).run()
    except ImportError as e:
        print(f"the editor needs Tkinter: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
