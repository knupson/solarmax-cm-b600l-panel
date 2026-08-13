"""Editor visual del layout.

`EditorState` es todo el comportamiento -- cargar, editar, validar, previsualizar,
guardar -- y no importa Tkinter. La ventana de abajo solo ata controles a esos
metodos. Es la misma division que entre `PanelApp` y `tray.py`, y por la misma
razon: lo que tiene tests es la parte que puede estar mal.

El editor guarda con `loader.save()`, que escribe atomico, y el motor levanta el
cambio en caliente. No hay ninguna comunicacion entre los dos procesos: el
archivo ES el protocolo.
"""
import argparse
import json
import re
import sys
import traceback
from pathlib import Path

from PIL import Image

from . import bundle
from .layout import loader, model, schema
from .metrics import METRICS, group_for, spec_for
from .providers.setup import build_registry_without_sensors
from .render.renderer import Renderer

# Campos que son numeros. Todo lo que no este aca se guarda como texto: un
# label con el texto "6000" no puede volverse el entero 6000, porque el
# validador exige que `text` sea texto.
_INT_FIELDS = {"x", "y", "w", "h", "r", "radius", "thickness", "samples",
               "stroke_width", "size", "width", "height", "rotate",
               "brightness", "jpeg_quality"}
_FLOAT_FIELDS = {"min", "max", "start_angle", "sweep", "fps", "angle", "at"}

# Un widget nuevo tiene que validar apenas se agrega: si el default no valida,
# el usuario ve un error que no cometio. Se completan con el primer alias de
# fuente del layout y una metrica que siempre existe.
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


# Valores de demostracion por metrica, para el preview del editor.
#
# A mano y no calculados: el 42% del rango declarado da cosas como "RAM usada
# 107,5 G" (porque el spec admite hasta 256) o "5040 MT/s", que se ven como un
# bug en vez de como un ejemplo. El punto del preview es juzgar el layout, y
# para eso los numeros tienen que parecer reales -- incluido el largo, que es
# lo que decide si un valor se pisa con el de al lado.
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
    """Una muestra plausible para TODAS las metricas conocidas.

    El preview no puede estar lleno de "--": las metricas que esta maquina no
    sirve (por falta de GSA1, de WinRing0, de lo que sea) igual tienen que
    dibujarse con algo para poder juzgar el layout.

    Lo que no este en _DEMO cae a la mitad del rango del spec, para que una
    metrica nueva aparezca con algo razonable sin tener que tocar esta tabla.
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
    # disk.temp.N no esta en METRICS (es un patron), pero el perfil los usa.
    for n in range(4):
        out[f"disk.temp.{n}"] = 34.0 + n
    return out


class EditorState:
    """El layout en edicion: JSON crudo + validacion + preview.

    Se trabaja sobre el dict crudo, no sobre el modelo tipado, porque el
    usuario pasa por estados intermedios invalidos (un color a medio tipear) y
    el modelo no los puede representar. `errors` dice si lo que hay ahora
    valida; `preview()` sigue devolviendo el ultimo render valido mientras no
    valide, igual que el panel mantiene el ultimo layout bueno.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.raw = {}
        self.errors: list[str] = []
        self.dirty = False
        self._sample = demo_sample()
        self._last_good = None          # ultimo preview valido
        self._cache_catalogo = None     # el catalogo cuesta consultar WMI
        self._fuentes = None            # FontResolver, para medir cajas de texto
        self._drag = None               # (id, offset x, offset y) del arrastre
        self._historial = []            # copias del layout, para deshacer
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
        self._historial = []      # lo que hay en disco es el nuevo punto cero

    def save(self) -> list[str]:
        """Guarda solo si valida. Devuelve los errores que lo impidieron.

        Nunca escribe un layout invalido: el motor lo rechazaria y se quedaria
        con el anterior, asi que el usuario habria "guardado" algo que el panel
        ignora sin decirle por que.
        """
        self.errors = schema.validate(self.raw)
        if self.errors:
            return list(self.errors)
        # save_raw y no save(build(raw)): pasar por el modelo reescribe el
        # archivo con el formato del serializador y el perfil se edita tambien
        # a mano. Igual queda atomico.
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
        """{dispositivo: [(id, etiqueta), ...]} para el selector de metricas.

        Se consulta al registry, que es el unico que sabe que dispositivos hay
        en ESTA maquina y como se llaman -- "vol.D.free" no sabe que la D se
        llama JUEGOS. Si no hay backend de sensores (otra maquina, sin permisos,
        sin DLLs), cae a las metricas registradas: el editor tiene que abrir
        igual, con etiquetas genericas.

        Se agregan tambien las metricas que el perfil YA usa aunque el registry
        no las ofrezca. Si no, cambiar la metrica de un widget en una maquina
        que no la sirve la haria desaparecer del selector y no se podria volver
        atras.
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
            salida.setdefault(grupos.get(mid, "Otras"), []).append((mid, etiqueta))
        for entradas in salida.values():
            entradas.sort(key=lambda par: par[1].lower())
        return dict(sorted(salida.items()))

    def _catalogo(self):
        """(catalogo, grupos) del registry, o de METRICS si no hay backend."""
        if self._cache_catalogo is None:
            catalogo, grupos = {}, {}
            registry = None
            try:
                registry, _cliente = build_registry_without_sensors()
                catalogo, grupos = registry.catalog(), registry.groups()
            except Exception:
                # Sin backend: el editor abre igual con las metricas
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
    # Se guarda una copia del layout crudo ANTES de cada cambio, no un diff: con
    # 154 widgets el JSON son ~40 KB y copiarlo cuesta menos que razonar sobre
    # como revertir cada tipo de operacion. Un diff serviria si el layout fuera
    # grande de verdad; aca solo agregaria formas de equivocarse.
    MAX_UNDO = 60

    def _snapshot(self):
        """Guarda el estado actual como punto de retorno.

        Topeado: sin limite, un arrastre de 300 px guardaria 300 copias, y el
        editor terminaria con decenas de MB de historial por mover un widget.
        """
        self._historial.append(json.dumps(self.raw, ensure_ascii=False))
        if len(self._historial) > self.MAX_UNDO:
            del self._historial[0]

    def undo(self) -> bool:
        """Vuelve al punto anterior. False si no hay nada que deshacer."""
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
        """Familias instaladas, para el combo.

        Tipear la familia a mano es como se escribe una que no existe: el
        renderer cae a la fuente por defecto y el widget se ve distinto sin que
        nada avise. FontResolver ya tiene el indice del sistema.
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
            # Desde un control de texto llega "true"/"false"; el validador
            # exige un booleano de JSON, no la cadena.
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

        Borrarlo dejaria el layout invalido -- "unknown font alias" --
        y el motor rechazaria el perfil entero, quedandose con el anterior. El
        usuario habria borrado una fuente y el panel no cambiaria.
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
    # Todo en coordenadas del PANEL (320x1480), no de la vista previa: la ventana
    # convierte dividiendo por su escala. Asi esta logica no depende de como se
    # este mostrando.

    def _canvas(self):
        d = self.raw.get("designed_for") or {}
        return int(d.get("width") or 320), int(d.get("height") or 1480)

    def widget_bbox(self, wid):
        """(x0, y0, x1, y1) de un widget, o None si no existe.

        Para los textos se MIDE la fuente con el valor de demostracion en vez de
        usar un radio fijo: el reloj de 74 px y una etiqueta de 14 no pueden
        tener la misma zona sensible, y con un radio inventado agarrar el chico
        al lado del grande seria imposible.
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
        # El ancla del renderer es "la"/"ma"/"ra": el alto cuelga hacia abajo y
        # el ancho se reparte segun la alineacion.
        alineacion = w.get("align", "left")
        if alineacion == "center":
            x0 = x - ancho // 2
        elif alineacion == "right":
            x0 = x - ancho
        else:
            x0 = x
        return (x0, y, x0 + max(6, ancho), y + max(6, alto))

    def _medir_texto(self, w):
        """(ancho, alto) del texto que este widget dibujaria."""
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
        """Id del widget bajo el punto, o None.

        Se recorre al REVES porque el orden de la lista es el orden de pintado:
        el ultimo dibujado es el que el usuario ve arriba, y por lo tanto el que
        espera agarrar.
        """
        for w in reversed(self.raw.get("widgets") or []):
            caja = self.widget_bbox(w.get("id"))
            if caja and caja[0] <= x <= caja[2] and caja[1] <= y <= caja[3]:
                return w.get("id")
        return None

    def begin_drag(self, wid, x, y):
        """Empieza un arrastre. Guarda el offset dentro del widget para mover
        por delta: reposicionar la esquina en el cursor haria saltar al widget
        en cuanto se lo agarra desde cualquier lugar que no sea su esquina."""
        w = self.widget(wid)
        if w is None:
            return
        # El snapshot va aca y no en drag_to(): un arrastre dispara un cambio por
        # pixel de mouse, y con uno por cambio deshacer un arrastre pediria
        # cincuenta Ctrl+Z. El gesto entero es UN paso.
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
        # Clampeado al lienzo: un widget arrastrado afuera desaparece del panel
        # y no queda forma de volver a agarrarlo con el mouse.
        w["x"] = max(0, min(ancho - 1, int(x - dx)))
        w["y"] = max(0, min(alto - 1, int(y - dy)))
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def end_drag(self):
        self._drag = None

    # --- reglas de color ---
    #
    # En el JSON una regla es {"when": "> 90", "color": "#FF4D00"}: el comparador
    # y el numero viajan juntos en un string. La UI necesita las tres piezas por
    # separado -- un combo para el operador, un campo para el numero, otro para el
    # color -- asi que aca se parten al leer y se vuelven a armar al escribir. Es
    # el unico lugar del editor que traduce entre la forma del archivo y la forma
    # de los controles, y esta aca en vez de en la ventana para que tenga tests.
    _RULE_RE = re.compile(r"^\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

    def rule_operators(self) -> list[str]:
        """Los que el validador acepta, en orden de uso."""
        return [">", ">=", "<", "<="]

    def rules(self, wid) -> list[dict]:
        """[{op, value, color}] del widget, con el comparador ya partido."""
        w = self.widget(wid) or {}
        salida = []
        for r in w.get("rules") or []:
            m = self._RULE_RE.match(str(r.get("when", "")))
            salida.append({"op": m.group(1) if m else ">",
                           "value": m.group(2) if m else "",
                           "color": r.get("color", "#FFFFFF")})
        return salida

    def add_rule(self, wid) -> list[str]:
        """Agrega una regla que ya valida.

        El umbral por defecto sale del spec de la metrica -- el 85% de su maximo
        -- en vez de un 90 fijo: una regla ">= 90" sobre un voltaje de 1,05 V
        nunca se dispara y el usuario no entiende por que su regla no hace nada.
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
        """Cambia una pieza de una regla. No escribe si queda invalida.

        A diferencia del resto de los campos del editor -- donde un valor a medio
        tipear es legitimo y solo se reporta -- una regla mal armada rompe TODAS
        las reglas de ese widget, porque el validador rechaza el layout entero.
        Asi que aca se prueba primero y se descarta el cambio si no valida.
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
            reglas[i] = anterior          # se revierte: una regla rota apaga todas
            self.errors = schema.validate(self.raw)
            return errores
        reglas[i] = anterior              # para que el snapshot guarde el estado previo
        self._snapshot()
        reglas[i] = candidata
        self.dirty = True
        self.errors = []
        return []

    # --- fondo ---
    #
    # Los campos que admite cada tipo salen de schema.BACKGROUND_KEYS, no de una
    # lista escrita a mano aca: si la UI ofreciera un campo que el tipo no
    # admite, escribiria una clave que el validador rechaza y el usuario veria
    # un error que no cometio. `stops` se edita aparte porque es una lista.
    _DEFAULTS_FONDO = {
        "color": "#0B0F17", "angle": 90.0, "fit": "cover", "src": "fondos",
        "name": "scroll", "speed": 20.0, "period": 6.0, "fps": 10.0,
    }
    _STOPS_DEFAULT = [{"at": 0.0, "color": "#101725"},
                      {"at": 1.0, "color": "#141A26"}]

    def background_fields(self, tipo=None) -> list[str]:
        """Campos escalares que este tipo de fondo admite, en orden estable."""
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
        """Cambia el tipo y completa lo que ese tipo necesita.

        Se conservan las claves que el tipo nuevo tambien admite -- pasar de
        'gradient' a 'procedural' no puede perder el degradado que el usuario ya
        afino, que es justamente el punto de que procedural parta de ahi -- y se
        descartan las que no, porque quedarian como claves desconocidas.
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

    # --- paradas del degradado ---

    def stops(self) -> list:
        return (self.raw.get("background") or {}).get("stops") or []

    def add_stop(self) -> list[str]:
        """Agrega una parada en el medio del hueco mas grande.

        En el medio y no al final: una parada nueva encima de otra existente no
        se ve, y el usuario no entiende que paso.
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
        """Nunca deja menos de dos: un degradado de una parada no es un
        degradado y el validador lo rechaza."""
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
        """El frame tal como lo veria el panel. Con el layout invalido
        devuelve el ultimo valido, o un lienzo vacio si nunca hubo uno."""
        if not self.errors:
            try:
                layout = schema.build(self.raw)
                self._last_good = Renderer(layout).frame(self._sample)
            except Exception:
                pass                    # build/render fallo: queda el anterior
        if self._last_good is None:
            return Image.new("RGB", (320, 1480), (0, 0, 0))
        return self._last_good


ANIMADO = ("Animated background: the preview shows a single frame, so it looks "
           "frozen here. On the panel it animates. Raising the panel fps "
           "(Panel tab) makes it more visible.")


def _mismo_contenido(a, b) -> bool:
    """Dos rutas con el mismo contenido.

    Compara tamano y despues bytes; para carpetas alcanza con la lista de nombres y
    tamanos -- una secuencia de cuadros con los mismos nombres y tamanos es la misma
    secuencia, y leer 300 PNG para confirmarlo no paga.
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
    # Por trozos y no read_bytes(): un video de 900 MB comparado asi son 1,8 GB en
    # memoria de golpe, para contestar una pregunta de si/no. En este proyecto ya
    # hubo un episodio de consumo de RAM y no vale la pena repetirlo por comodidad.
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ta, tb = fa.read(1 << 20), fb.read(1 << 20)
            if ta != tb:
                return False
            if not ta:
                return True


def pista_fondo(tipo) -> str:
    """Aviso por tipo de fondo, o "" si no hace falta ninguno.

    Funcion de modulo y no metodo de la ventana: es texto puro, decidido por el
    tipo y por si ffmpeg esta instalado, y asi se prueba sin abrir Tkinter.

    El de los animados importa: la vista previa es UN cuadro, asi que un fondo
    que se mueve se ve quieto ahi y eso parece un bug.
    """
    if tipo == "procedural":
        return ANIMADO
    if tipo == "sequence":
        return (ANIMADO + " src is a folder of images, relative to "
                "vmaxpanel/assets.")
    if tipo == "video":
        # La ruta se consulta en cada llamada -- no se cachea al importar --
        # porque el usuario puede instalar ffmpeg con el editor abierto, y la
        # pista tiene que dejar de pedirlo cuando reabra la pestaña.
        from .render.video import COMO_INSTALAR, buscar_ffmpeg
        if buscar_ffmpeg() is None:
            return ANIMADO + " " + COMO_INSTALAR
        return (ANIMADO + " src is a video relative to vmaxpanel/assets: mp4, "
                "webm, mkv, gif, whatever ffmpeg can open.")
    return ""


def _coerce_fondo(clave, valor):
    """Como _coerce, pero para las claves del fondo.

    `fps` en el fondo es la cadencia de una secuencia y admite decimales; en el
    panel es entero. Misma clave, tipo distinto segun donde este: de ahi que el
    fondo tenga su propia conversion en vez de compartir la tabla.
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
    """Convierte lo que vino de un control de texto al tipo que el schema
    espera para esa clave. Lo que no es numerico se deja como texto: `text`,
    `format` y los colores tienen que quedar str, incluso si parecen numeros.
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
                return s                # se guarda tal cual y el validador avisa
    if key in _FLOAT_FIELDS:
        if s == "":
            return None                 # extremo abierto (min/max sin fijar)
        try:
            return float(s)
        except ValueError:
            return s
    return s


# --------------------------------------------------------------------------
# La ventana. Tkinter se importa adentro para que EditorState se pueda usar
# (y testear) en una maquina sin Tk.
# --------------------------------------------------------------------------

PREVIEW_SCALE = 0.36     # escala inicial, antes de que la ventana tenga tamano


class EditorWindow:
    def __init__(self, state: EditorState):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.state = state
        self.root = tk.Tk()
        self.root.title(f"VMax Panel — {state.path.name}")
        # El mismo icono que la bandeja, para que la ventana no salga con el
        # generico de Python en la barra de tareas. Si falta, no pasa nada.
        try:
            icono = Path(__file__).resolve().parent / "assets" / "vmaxpanel.ico"
            if icono.exists():
                self.root.iconbitmap(default=str(icono))
        except Exception:
            pass
        self._preview_img = None
        self._escala = PREVIEW_SCALE
        self._fields = {}
        self._pickers = {}
        self._metric_por_etiqueta = {}
        self._rule_rows = []
        self._rules_frame = None
        # (tipo, clave) de cada campo con algo tipeado y sin confirmar. Ver
        # _aplicar_pendientes().
        self._pendientes = set()
        self._build()
        # El tamano inicial se pide explicito: sin esto Tkinter le da el minimo que
        # necesitan los controles, la lista de widgets y las propiedades se comen el
        # ancho, y la vista previa de un panel 320x1480 queda en una tirita de ~60 px.
        # La escala responsive ya arreglaba el caso de maximizar; esto arregla el de
        # abrir, que es el 100% de las veces.
        self.root.geometry(self._geometria_inicial(
            self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        self._refresh(select_first=True)

    # Lo que necesita cada columna: la izquierda (lista + propiedades + mover) y la
    # vista previa de un panel vertical con algo de aire. Medidos sobre la ventana real.
    ANCHO_CONTROLES = 700
    ANCHO_PREVIEW = 420

    @staticmethod
    def _geometria_inicial(ancho_pantalla, alto_pantalla) -> str:
        """El "WxH" con el que conviene abrir, acotado a la pantalla.

        Acotado y no fijo: en una notebook 1366x768 pedir 1200x950 deja la barra de
        acciones abajo del borde, o sea sin forma de guardar. El 85% deja lugar para la
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
        # El pie se empaqueta ANTES que raiz aunque se llene despues: pack reparte
        # el espacio en orden, y raiz va con expand=True. Al revés, raiz se queda con
        # todo y el pie -- justo el que tiene Guardar -- puede quedar fuera de la
        # ventana.
        self._pie = ttk.Frame(self.root)
        self._pie.pack(side="bottom", fill="x")
        raiz.pack(fill="both", expand=True)

        # Pestanas: el fondo y el panel no son widgets, y meterlos en la misma
        # columna obligaria a elegir entre ver la lista o ver el fondo.
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
        ttk.Label(izq, text="Widgets").pack(anchor="w")
        self.lista = tk.Listbox(izq, width=24, height=28, exportselection=False)
        self.lista.pack(fill="y", expand=True)
        self.lista.bind("<<ListboxSelect>>", lambda e: self._on_select())

        botones = ttk.Frame(izq)
        botones.pack(fill="x", pady=(4, 0))
        for tipo in ("text", "label", "bar", "rect"):
            ttk.Button(botones, text=f"+{tipo}", width=6,
                       command=lambda t=tipo: self._add(t)).pack(side="left")
        ttk.Button(izq, text="Delete", command=self._remove).pack(fill="x")

        centro = ttk.Frame(tab_widgets, padding=(12, 0))
        centro.pack(side="left", fill="both", expand=True)
        self.props = ttk.Frame(centro)
        self.props.pack(fill="both", expand=True)

        flechas = ttk.Frame(centro)
        flechas.pack(fill="x", pady=6)
        ttk.Label(flechas, text="Move:").pack(side="left")
        for texto, (dx, dy) in (("←", (-1, 0)), ("→", (1, 0)),
                                ("↑", (0, -1)), ("↓", (0, 1)),
                                ("←10", (-10, 0)), ("→10", (10, 0)),
                                ("↑10", (0, -10)), ("↓10", (0, 10))):
            ttk.Button(flechas, text=texto, width=4,
                       command=lambda a=dx, b=dy: self._move(a, b)).pack(side="left")

        # La barra de acciones y la barra de estado van en el PIE, fuera del
        # Notebook. Estaban dentro de la pestana Widgets, y desde la pestana Fondo
        # no habia entonces ni boton de guardar ni un solo mensaje: el usuario
        # cambiaba el fondo, no encontraba donde aplicarlo, reiniciaba el motor --
        # que relee el archivo, donde el cambio nunca llego -- y el cambio se
        # perdia. Reportado tal cual: "no hay boton de aplicar ni guarda".
        self._acciones = ttk.Frame(self._pie, padding=(8, 0, 8, 8))
        self._acciones.pack(side="bottom", fill="x")
        ttk.Button(self._acciones, text="Save",
                   command=self._save).pack(side="left")
        ttk.Button(self._acciones, text="Discard changes",
                   command=self._discard).pack(side="left", padx=4)
        ttk.Button(self._acciones, text="Export…",
                   command=self._pedir_exportar).pack(side="left")
        ttk.Button(self._acciones, text="Import…",
                   command=self._pedir_importar).pack(side="left", padx=4)
        self.estado = ttk.Label(self._pie, text="", wraplength=900,
                                justify="left", padding=(8, 0))
        self.estado.pack(side="bottom", fill="x")

        self._build_fondo()
        self._build_fuentes()
        self._build_panel()

        self.der = ttk.Frame(raiz)
        self.der.pack(side="left", fill="both", expand=True)
        ttk.Label(self.der, text="Preview").pack(anchor="w")
        self.canvas = tk.Label(self.der, borderwidth=1, relief="solid",
                               anchor="n")
        self.canvas.pack(fill="both", expand=True)
        # El <Configure> se escucha en el CONTENEDOR, no en el Label: cambiar la
        # imagen cambia el tamano del Label y eso dispararia otro Configure,
        # o sea un bucle de redibujo.
        self.der.bind("<Configure>", self._on_resize)
        # Arrastrar sobre la vista previa: es la forma natural de posicionar, y
        # la lista de 47 nombres obliga a saber de memoria como se llama cada
        # cosa.
        self.canvas.bind("<Button-1>", self._on_preview_press)
        self.canvas.bind("<B1-Motion>", self._on_preview_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_preview_release)

        self.root.report_callback_exception = self._report_error
        self.root.bind("<Control-s>", lambda e: self._save())
        self.root.bind("<Control-z>", lambda e: self._undo())
        for tecla, (dx, dy) in (("<Left>", (-1, 0)), ("<Right>", (1, 0)),
                                ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.root.bind(tecla, lambda e, a=dx, b=dy: self._nudge(a, b))

    # --- arrastre sobre la vista previa ---

    def _offset_preview(self):
        """(x, y) de la esquina de la imagen dentro del Label.

        El Label esta anclado al norte y llena su hueco, asi que la imagen queda
        centrada horizontalmente: sin descontar ese margen, el clic cae varios
        pixeles corrido y agarra el widget de al lado.
        """
        ancho_label = self.canvas.winfo_width()
        ancho_img = self._preview_img.width() if self._preview_img else 0
        return max(0, (ancho_label - ancho_img) // 2), 0

    def _a_panel(self, px, py):
        """Coordenadas del Label -> coordenadas del panel (320x1480)."""
        ox, oy = self._offset_preview()
        k = self._escala or 1.0
        return int(round((px - ox) / k)), int(round((py - oy) / k))

    def _a_pantalla(self, x, y):
        ox, oy = self._offset_preview()
        k = self._escala or 1.0
        return int(round(x * k)) + ox, int(round(y * k)) + oy

    def _on_preview_press(self, evento):
        x, y = self._a_panel(evento.x, evento.y)
        wid = self.state.hit_test(x, y)
        if wid is None:
            # Un clic al vacio NO deselecciona: el panel de propiedades se
            # vaciaria y el usuario perderia lo que estaba editando.
            return
        ids = self.state.widget_ids()
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(ids.index(wid))
        self.lista.see(ids.index(wid))
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
        # Los campos x/y muestran el valor viejo hasta que se repintan.
        self._show_props()

    # --- pestana Fondo ---

    def _build_fondo(self):
        ttk = self.ttk
        cab = ttk.Frame(self.tab_fondo)
        cab.pack(fill="x")
        ttk.Label(cab, text="Type").pack(side="left")
        self._bg_type = self.tk.StringVar()
        combo = ttk.Combobox(cab, textvariable=self._bg_type, width=16,
                             state="readonly", values=self.state.background_types())
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_pick_bg_type())

        self._bg_hint = ttk.Label(self.tab_fondo, text="", wraplength=420,
                                  justify="left", foreground="#606060")
        self._bg_hint.pack(fill="x", pady=(4, 0))

        self._bg_campos = ttk.Frame(self.tab_fondo)
        self._bg_campos.pack(fill="x", pady=6)

        self._bg_stops = ttk.Frame(self.tab_fondo)
        self._bg_stops.pack(fill="both", expand=True)

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
            ttk.Label(self._bg_campos, text=clave).grid(row=fila, column=0, sticky="w")
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
            control.grid(row=fila, column=1, sticky="w", padx=4)
            if clave == "src":
                # Al lado del campo, no en otra parte: es lo que la mayoria va a
                # usar en vez de tipear una ruta, y tiene que estar donde se ve el
                # valor que reemplaza.
                self._btn_asset = ttk.Button(self._bg_campos, text="Choose…",
                                             width=9, command=self._pedir_asset)
                self._btn_asset.grid(row=fila, column=2, padx=(2, 0))

        self._show_stops()
        self._bg_hint.config(text=pista_fondo(tipo))

    # --- elegir el archivo del fondo ---

    def _pedir_asset(self):
        """Diálogo para elegir el video, la imagen o la carpeta del fondo."""
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
                filetypes=filtros + [("Todos", "*.*")])
        if elegido:
            self._usar_asset(Path(elegido))

    def _usar_asset(self, origen, assets_dir=None):
        """Deja `origen` disponible como asset y lo pone en `src`. -> nombre | None.

        **Copia el archivo adentro de vmaxpanel/assets si esta afuera**, y eso es
        todo el punto: `safe_asset_path` rechaza cualquier ruta que se escape de ese
        directorio -- con razon, el proceso corre elevado --, asi que elegir un video
        del Escritorio SOLO puede funcionar copiandolo. Sin esto el editor guardaria
        una ruta que el motor rechaza y el fondo quedaria en color plano sin que nada
        lo explique.
        """
        import shutil
        origen = Path(origen)
        destino_raiz = Path(assets_dir) if assets_dir else self._carpetas()[1]
        if not origen.exists():
            self.estado.config(text=f"{origen.name} does not exist", foreground="#A00000")
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
                               foreground="#A00000")
            return None

        self.state.set_background_field("src", nombre)
        if "src" in getattr(self, "_bg_fields", {}):
            self._bg_fields["src"].set(nombre)
        self._pendientes.discard(("bg", "src"))
        self._draw_preview()
        self._show_errors()
        self.estado.config(text=f"background: {nombre}", foreground="#006000")
        return nombre

    @staticmethod
    def _ya_esta_adentro(origen, raiz):
        """El nombre relativo si `origen` ya vive bajo `raiz`, o None.

        Con / y no con os.sep: va a un JSON que se comparte entre maquinas, y
        safe_asset_path normaliza las dos formas pero el archivo se lee mejor asi.
        """
        try:
            return origen.resolve().relative_to(raiz.resolve()).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _copiar_asset(origen, raiz, shutil):
        """Copia (archivo o carpeta) y devuelve el nombre que quedo.

        Si el nombre ya existe con OTRO contenido se renombra a `-2`: pisar el asset
        de otro perfil es destruir trabajo por un nombre repetido. Si existe con el
        MISMO contenido se reusa, para que tocar el boton dos veces no deje dos
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

    # --- paradas del degradado ---

    def _show_stops(self):
        ttk = self.ttk
        for hijo in self._bg_stops.winfo_children():
            hijo.destroy()
        self._stop_rows = []
        if not self.state.has_stops():
            return
        ttk.Label(self._bg_stops, text="Gradient stops").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(6, 2))
        for i, parada in enumerate(self.state.stops()):
            fila = i + 1
            ttk.Label(self._bg_stops, text=f"{i}").grid(row=fila, column=0)
            at = self.tk.StringVar(value=str(parada.get("at", 0)))
            color = self.tk.StringVar(value=str(parada.get("color", "#000000")))
            e1 = ttk.Entry(self._bg_stops, textvariable=at, width=8)
            e2 = ttk.Entry(self._bg_stops, textvariable=color, width=12)
            e1.grid(row=fila, column=1, padx=2)
            e2.grid(row=fila, column=2, padx=2)
            for control, clave in ((e1, "at"), (e2, "color")):
                control.bind("<FocusOut>",
                             lambda e, j=i, k=clave: self._apply_stop(j, k))
                control.bind("<Return>",
                             lambda e, j=i, k=clave: self._apply_stop(j, k))
            ttk.Button(self._bg_stops, text="−", width=3,
                       command=lambda j=i: self._remove_stop(j)).grid(row=fila, column=3)
            self._stop_rows.append({"at": at, "color": color})
        ttk.Button(self._bg_stops, text="+ stop",
                   command=self._add_stop).grid(row=len(self._stop_rows) + 1,
                                                column=0, columnspan=3,
                                                sticky="w", pady=4)

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
            self.estado.config(text=" / ".join(errores), foreground="#B00000")
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
                  justify="left", foreground="#606060").pack(anchor="w",
                                                             pady=(0, 8))
        self._font_grid = ttk.Frame(self.tab_fuentes)
        self._font_grid.pack(fill="both", expand=True)
        agregar = ttk.Frame(self.tab_fuentes)
        agregar.pack(fill="x", pady=6)
        self._font_nuevo = self.tk.StringVar()
        ttk.Entry(agregar, textvariable=self._font_nuevo, width=16).pack(side="left")
        ttk.Button(agregar, text="+ alias",
                   command=self._add_font).pack(side="left", padx=4)
        self._font_rows = {}
        self._familias = None

    def _show_fonts(self):
        ttk = self.ttk
        for hijo in self._font_grid.winfo_children():
            hijo.destroy()
        self._font_rows = {}
        if self._familias is None:
            # Una sola vez: indexar las fuentes del sistema recorre directorios.
            self._familias = self.state.font_families()
        for fila, alias in enumerate(self.state.fonts()):
            spec = self.state.raw["fonts"][alias]
            ttk.Label(self._font_grid, text=alias, width=12).grid(row=fila, column=0,
                                                                 sticky="w")
            familia = ttk.Combobox(self._font_grid, width=26, state="readonly",
                                   values=self._familias)
            familia.set(str(spec.get("family", "")))
            familia.grid(row=fila, column=1, padx=2)
            familia.bind("<<ComboboxSelected>>",
                         lambda e, a=alias: self._apply_font(a, "family"))

            size = self.tk.StringVar(value=str(spec.get("size", "")))
            entrada = ttk.Entry(self._font_grid, textvariable=size, width=6)
            entrada.grid(row=fila, column=2, padx=2)
            for evento in ("<FocusOut>", "<Return>"):
                entrada.bind(evento, lambda e, a=alias: self._apply_font(a, "size"))

            bold = self.tk.BooleanVar(value=bool(spec.get("bold")))
            ttk.Checkbutton(self._font_grid, text="bold", variable=bold,
                            command=lambda a=alias: self._apply_font(a, "bold")
                            ).grid(row=fila, column=3, padx=4)

            usuarios = len(self.state.font_users(alias))
            ttk.Label(self._font_grid, text=f"{usuarios} widgets",
                      foreground="#606060").grid(row=fila, column=4, padx=4)
            ttk.Button(self._font_grid, text="−", width=3,
                       command=lambda a=alias: self._remove_font(a)
                       ).grid(row=fila, column=5)
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
            self.estado.config(text=" / ".join(errores), foreground="#B00000")
        else:
            self._show_errors()

    def _remove_font(self, alias):
        errores = self.state.remove_font(alias)
        self._show_fonts()
        self._draw_preview()
        if errores:
            self.estado.config(text=" / ".join(errores), foreground="#B00000")
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
                  justify="left", foreground="#606060").pack(anchor="w", pady=(0, 8))
        campos = ttk.Frame(self.tab_panel)
        campos.pack(fill="x")
        for fila, clave in enumerate(self.state.panel_fields()):
            ttk.Label(campos, text=clave).grid(row=fila, column=0, sticky="w")
            var = self.tk.StringVar()
            entrada = ttk.Entry(campos, textvariable=var, width=12)
            entrada.grid(row=fila, column=1, sticky="w", padx=4)
            entrada.bind("<FocusOut>", lambda e, k=clave: self._apply_panel(k))
            entrada.bind("<Return>", lambda e, k=clave: self._apply_panel(k))
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

    def _selected(self):
        sel = self.lista.curselection()
        return self.lista.get(sel[0]) if sel else None

    def _on_select(self):
        """Cambio de seleccion en la lista.

        Faltaba entero: el bind existia y este metodo no, asi que cada clic
        levantaba una AttributeError que Tkinter imprime a stderr y se come.
        Bajo pythonw -- que es como lo abre la bandeja -- eso no va a ninguna
        parte: el panel de propiedades se quedaba mostrando el primer widget
        para siempre, sin ningun error a la vista.
        """
        self._show_props()
        self._show_errors()

    def _report_error(self, exc_type, exc, tb):
        """Excepcion de un callback de Tkinter, a la vista en vez de perdida.

        El default imprime a stderr y sigue, que bajo pythonw es un fallo
        invisible. Se muestra en la barra de estado y se re-emite al log.
        """
        texto = f"internal error: {exc_type.__name__}: {exc}"
        try:
            self.estado.config(text=texto, foreground="#B00000")
        except Exception:
            pass
        print(texto, file=sys.stderr)
        if tb is not None:
            traceback.print_exception(exc_type, exc, tb, file=sys.stderr)

    def _refresh(self, select_first=False, keep=None):
        keep = keep or self._selected()
        self.lista.delete(0, "end")
        for wid in self.state.widget_ids():
            self.lista.insert("end", wid)
        ids = self.state.widget_ids()
        objetivo = keep if keep in ids else (ids[0] if (select_first and ids) else None)
        if objetivo is not None:
            self.lista.selection_set(ids.index(objetivo))
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
                               foreground="#B00000")
        else:
            self.estado.config(text=f"{marca} no errors", foreground="#006000")

    def _escala_disponible(self) -> float:
        """La escala mas grande a la que el frame entero entra en su hueco.

        Se mide el contenedor y no la ventana: asi la vista previa aprovecha lo
        que sobra cuando el usuario maximiza, que es todo el punto de un editor
        -- juzgar un layout en miniatura no sirve.

        Tope en 1.0: mas alla es upscaling borroso, y 1480 px de alto no entran
        en una pantalla de 1080 de todos modos.
        """
        alto = self.der.winfo_height() - 28          # el rotulo "Vista previa"
        ancho = self.der.winfo_width() - 6           # el borde del Label
        d = self.state.raw.get("designed_for") or {}
        pw = float(d.get("width") or 320) or 320
        ph = float(d.get("height") or 1480) or 1480
        if alto <= 1 or ancho <= 1:
            return PREVIEW_SCALE                    # todavia sin geometria real
        return max(0.05, min(1.0, ancho / pw, alto / ph))

    def _on_resize(self, _evento=None):
        """Redibuja solo si la escala cambio de verdad.

        Un resize dispara muchos <Configure> seguidos y cada redibujo implica
        reescalar una imagen de 320x1480 y convertirla a PhotoImage. El umbral
        del 2% corta el ruido sin que se note el salto.
        """
        nueva = self._escala_disponible()
        if abs(nueva - self._escala) / max(nueva, self._escala) > 0.02:
            self._escala = nueva
            self._draw_preview()

    def _draw_preview(self):
        # El titulo se actualiza aca y no en cada mutacion: _draw_preview() es el
        # camino comun de TODAS -- mover, editar un campo, cambiar el fondo, deshacer
        # -- asi que es el unico lugar donde no hay que acordarse de agregarlo.
        self._marcar_titulo()
        img = self.state.preview()
        self._escala = self._escala_disponible()
        dims = (max(1, int(img.width * self._escala)),
                max(1, int(img.height * self._escala)))
        chico = img.resize(dims, Image.LANCZOS)
        # PhotoImage sin referencia viva se recolecta y el Label queda en
        # blanco: el clasico de Tkinter con imagenes.
        self._preview_img = _to_photoimage(chico, self.tk)
        self.canvas.config(image=self._preview_img)

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
            self.ttk.Label(self.props, text=clave).grid(row=fila, column=0,
                                                        sticky="w")
            if clave == "metric":
                self._metric_picker(fila, valor)
                continue
            if clave == "font":
                self._font_picker(fila, valor)
                continue
            var = self.tk.StringVar(value="" if valor is None else str(valor))
            entrada = self.ttk.Entry(self.props, textvariable=var, width=32)
            # sticky="w", sin weight en la columna: con la ventana maximizada,
            # un campo que se expande deja una caja de 1500 px para escribir
            # "18".
            entrada.grid(row=fila, column=1, sticky="w", padx=4)
            entrada.bind("<FocusOut>", lambda e, k=clave: self._apply(k))
            entrada.bind("<Return>", lambda e, k=clave: self._apply(k))
            self._pendiente_al_tipear(var, "widget", clave)
            self._fields[clave] = var
        self._show_rules(len(w) + 1)

    # --- reglas de color ---

    def _show_rules(self, fila_base):
        """El editor de reglas, debajo de las propiedades.

        Solo para los widgets de texto: son los unicos que tienen reglas en este
        motor, y mostrar la seccion en una barra prometeria algo que no existe.
        """
        ttk = self.ttk
        # No hay que destruir nada aca: _show_props() ya borro todos los hijos de
        # self.props, y el marco de reglas es uno de ellos. La version anterior
        # guardaba una referencia y le pedia los hijos a un widget ya destruido,
        # que es "bad window path name".
        self._rule_rows = []
        wid = self._selected()
        w = self.state.widget(wid) if wid else None
        if w is None or w.get("type") != "text":
            return

        marco = ttk.Frame(self.props)
        marco.grid(row=fila_base, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._rules_frame = marco
        ttk.Label(marco, text="COLOUR BY VALUE", foreground="#606060").grid(
            row=0, column=0, columnspan=4, sticky="w")
        for i, regla in enumerate(self.state.rules(wid)):
            op = ttk.Combobox(marco, width=4, state="readonly",
                              values=self.state.rule_operators())
            op.set(regla["op"])
            op.grid(row=i + 1, column=0, padx=(0, 2), pady=1)
            op.bind("<<ComboboxSelected>>", lambda e, j=i: self._apply_rule(j, "op"))

            valor = self.tk.StringVar(value=regla["value"])
            color = self.tk.StringVar(value=regla["color"])
            e_valor = ttk.Entry(marco, textvariable=valor, width=8)
            e_color = ttk.Entry(marco, textvariable=color, width=10)
            e_valor.grid(row=i + 1, column=1, padx=2)
            e_color.grid(row=i + 1, column=2, padx=2)
            for control, campo in ((e_valor, "value"), (e_color, "color")):
                for evento in ("<FocusOut>", "<Return>"):
                    control.bind(evento,
                                 lambda e, j=i, c=campo: self._apply_rule(j, c))
            ttk.Button(marco, text="−", width=3,
                       command=lambda j=i: self._remove_rule(j)).grid(row=i + 1,
                                                                     column=3)
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
        # Se repinta siempre: una regla rechazada se revierte en el estado, asi
        # que el control tiene que volver a mostrar el valor que quedo y no el
        # que el usuario tipeo.
        self._show_props()
        self._draw_preview()
        if errores:
            self.estado.config(text=" / ".join(errores), foreground="#B00000")
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
        """Combo de metricas con etiqueta amigable, agrupado por dispositivo.

        Era un campo de texto libre: para poner el espacio libre de la D habia
        que saber de memoria que el id es `vol.D.free`. Los encabezados de grupo
        entran como items no seleccionables porque ttk.Combobox no tiene grupos
        de verdad; _on_pick_metric() los ignora.
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
        combo.grid(row=fila, column=1, sticky="w", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_pick_metric())
        self._pickers["metric"] = combo

    def _on_pick_metric(self):
        wid = self._selected()
        combo = self._pickers.get("metric")
        if wid is None or combo is None:
            return
        mid = self._metric_por_etiqueta.get(combo.get())
        if mid is None:
            # Un encabezado de grupo, o algo que no esta en el catalogo: se
            # repone lo que el widget ya tenia en vez de escribir basura.
            actual = (self.state.widget(wid) or {}).get("metric")
            actuales = [t for t, m in self._metric_por_etiqueta.items() if m == actual]
            if actuales:
                combo.set(actuales[0])
            return
        self.state.set_field(wid, "metric", mid)
        self._draw_preview()
        self._show_errors()

    def _font_picker(self, fila, actual):
        """Los alias de fuente son un conjunto cerrado del propio layout: un
        combo evita el error de tipear un alias que no existe."""
        combo = self.ttk.Combobox(self.props, values=self.state.fonts(),
                                  width=20, state="readonly")
        combo.set(actual or "")
        combo.grid(row=fila, column=1, sticky="w", padx=4)

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
        """Flechas del teclado: mueven el widget, salvo que estes escribiendo.

        El bind es de ventana, asi que sin este filtro la flecha llega a los
        dos lados: corriges un digito en el campo `x` y de paso el widget se
        desplaza un pixel. Es corrupcion del layout sin que se note.
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
            self.estado.config(text="guardado; el panel lo levanta solo",
                               foreground="#006000")

    def _pendiente_al_tipear(self, var, tipo, clave):
        """Marca (tipo, clave) como sin confirmar en cuanto cambia el texto.

        Se sigue la VARIABLE y no el teclado: un trace de escritura cubre tipear,
        pegar con el mouse, arrastrar texto y el autocompletado; `<KeyRelease>` solo
        cubre teclas -- y encima no se puede simular sin keysym, asi que tampoco se
        podia probar.

        El trace se agrega DESPUES de crear la variable con su valor inicial, asi
        que reconstruir un panel no marca nada: solo lo marca un cambio posterior,
        que es siempre del usuario.

        Es lo que permite confirmar al guardar SOLO lo que el usuario toco. Las dos
        alternativas son peores: releer todos los controles resucita valores viejos
        de las pestanas que no estan a la vista -- deshacer un cambio de fondo y
        guardar lo volveria a aplicar --, y depender del foco del sistema falla justo
        cuando la ventana no tiene el foco.
        """
        var.trace_add("write",
                      lambda *_, t=tipo, k=clave: self._pendientes.add((t, k)))

    def _aplicar_pendientes(self):
        """Confirma lo tipeado y no aplicado, antes de guardar.

        Los Entry aplican con <Return> o <FocusOut>. Un clic directo en Guardar no
        siempre dispara ninguno de los dos -- depende de si el boton toma el foco --
        y entonces se guardaba el valor VIEJO del campo recien escrito.
        Silenciosamente, que es lo peor: el archivo queda valido, el panel lo
        recarga y no cambia nada. Es el bug que reporto el usuario: cambiar el fondo,
        no encontrar donde aplicarlo, y que reiniciar el motor "ignore" el cambio.
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
                # Un control que ya no existe (cambio la seleccion, se rearmo la
                # pestana) no puede impedir guardar el resto.
                pass
        self._pendientes.clear()

    def _marcar_titulo(self):
        """El titulo dice si hay cambios sin guardar.

        Sin esa senal, reiniciar el motor desde la bandeja parece ignorar la edicion
        -- y lo que pasa es que la edicion nunca llego al disco, porque el motor
        relee el archivo. Es exactamente el sintoma que reporto el usuario.
        """
        base = f"Layout editor — {self.state.path.name}"
        self.root.title(base + (" • unsaved changes" if self.state.dirty
                                else ""))

    def _undo(self):
        """Ctrl+Z. Repinta TODO: deshacer puede haber cambiado el fondo, las
        fuentes o la lista de widgets, no solo el campo que se estaba editando.
        Sin repintar, los controles siguen mostrando el valor deshecho."""
        if not self.state.undo():
            self.estado.config(text="no hay nada que deshacer",
                               foreground="#606060")
            return
        self._refresh()

    def _discard(self):
        # Lo tipeado y no confirmado tambien se descarta: es justo lo que el boton
        # promete, y dejarlo pendiente lo haria reaparecer en el proximo guardado.
        self._pendientes.clear()
        self.state.reload()
        self._refresh(select_first=True)

    # --- exportar / importar ---
    #
    # La logica vive en bundle.py; aca esta el pegamento y los dos mensajes que
    # importan: por que no se exporto, y donde quedo lo que se importo.

    def _carpetas(self):
        """(perfiles, assets) del proyecto.

        Se derivan del paquete y no del perfil abierto: el perfil puede estar en
        cualquier parte -- los tests lo abren desde un tmp_path -- pero los assets
        de un bundle siempre van a vmaxpanel/assets, que es el unico lugar donde el
        motor los busca.
        """
        from .cli import assets_dir, profiles_dir
        return profiles_dir(), assets_dir()

    def _pedir_exportar(self):
        from tkinter import filedialog
        sugerido = f"{self.state.path.stem}{bundle.EXT}"
        destino = filedialog.asksaveasfilename(
            parent=self.root, title="Exportar perfil",
            initialfile=sugerido, defaultextension=bundle.EXT,
            filetypes=[("Perfil de VMax Panel", f"*{bundle.EXT}")])
        if destino:
            self._exportar_a(Path(destino))

    def _exportar_a(self, destino):
        destino = Path(destino)
        if self.state.dirty:
            # Exportar lee el ARCHIVO, no lo que hay en pantalla. Con cambios sin
            # guardar el bundle llevaria la version vieja, y ese error no se nota
            # hasta que otra persona lo abre.
            self.estado.config(text="there are unsaved changes: save first, then "
                                    "export", foreground="#803000")
            return
        if destino.exists():
            # asksaveasfilename ya pregunta, pero este metodo tambien se llama
            # directo: pisar un bundle que el usuario quizas ya compartio no puede
            # depender de que el dialogo haya preguntado.
            self.estado.config(text=f"{destino.name} ya existe: elegí otro nombre",
                               foreground="#803000")
            return
        try:
            info = bundle.export_profile(self.state.path, destino, self._carpetas()[1])
        except bundle.BundleError as e:
            self.estado.config(text=str(e), foreground="#A00000")
            return
        assets = ", ".join(info["assets"]) or "sin assets"
        self.estado.config(
            text=f"exportado a {destino.name} ({assets}). Las fuentes no viajan: "
                 f"se piden por familia.", foreground="#006000")

    def _pedir_importar(self):
        from tkinter import filedialog
        origen = filedialog.askopenfilename(
            parent=self.root, title="Importar perfil",
            filetypes=[("Perfil de VMax Panel", f"*{bundle.EXT}"),
                       ("Todos", "*.*")])
        if origen:
            self._importar_de(Path(origen))

    def _importar_de(self, origen, profiles_dir=None, assets_dir=None):
        """Importa y pasa a editar el perfil importado.

        Importar y no abrirlo dejaria al usuario adivinando si funciono.
        """
        perfiles, assets = self._carpetas()
        try:
            info = bundle.import_bundle(origen, profiles_dir or perfiles,
                                        assets_dir or assets, si_existe="renombrar")
        except bundle.BundleError as e:
            self.estado.config(text=str(e), foreground="#A00000")
            return
        self.state = EditorState(info["profile"])
        self.state.reload()
        self._refresh(select_first=True)
        faltan = info["fuentes_faltantes"]
        aviso = (f" Ojo: no tenés {', '.join(faltan)}, se ve distinto."
                 if faltan else "")
        self.estado.config(
            text=f"importado en {info['profile'].name}, y abierto.{aviso}",
            foreground="#803000" if faltan else "#006000")

    def run(self):
        self.root.mainloop()


def _to_photoimage(img, tk):
    """PIL -> PhotoImage.

    Se prefiere `ImageTk` de Pillow, pero no se depende de el: viene en el
    paquete y aun asi puede faltar segun como este compilado el binding de Tk.
    El respaldo es PNG en base64, que Tk 8.6 lee nativo -- PPM tambien seria
    nativo como archivo, pero PhotoImage(data=...) no lo reconoce.
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
        print(f"el editor necesita Tkinter: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
