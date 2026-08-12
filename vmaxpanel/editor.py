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
import sys
import traceback
from pathlib import Path

from PIL import Image

from .layout import loader, schema
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
    "label": {"text": "NUEVO", "x": 24, "y": 24, "font": None, "color": "#FFFFFF"},
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
        self.reload()

    # --- carga y guardado ---

    def reload(self):
        try:
            self.raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            self.raw = {}
            self.errors = [f"no se pudo leer el perfil: {e}"]
            self.dirty = False
            return
        self.errors = schema.validate(self.raw)
        self.dirty = False

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

    # --- edicion ---

    def set_field(self, wid, key, value) -> list[str]:
        w = self.widget(wid)
        if w is None:
            return [f"no existe el widget {wid!r}"]
        w[key] = _coerce(key, value)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def add_widget(self, tipo, wid) -> list[str]:
        if tipo not in _TEMPLATES:
            return [f"tipo desconocido {tipo!r}"]
        if wid in self.widget_ids():
            return [f"ya hay un widget con id {wid!r}"]
        nuevo = {"id": wid, "type": tipo, **_TEMPLATES[tipo]}
        if nuevo.get("font", "sin-alias") is None:
            aliases = self.fonts()
            if not aliases:
                return ["el layout no tiene ninguna fuente definida"]
            nuevo["font"] = aliases[0]
        self.raw.setdefault("widgets", []).append(nuevo)
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def remove_widget(self, wid) -> list[str]:
        widgets = self.raw.get("widgets", [])
        self.raw["widgets"] = [w for w in widgets if w.get("id") != wid]
        self.dirty = True
        self.errors = schema.validate(self.raw)
        return list(self.errors)

    def move_widget(self, wid, dx, dy) -> list[str]:
        w = self.widget(wid)
        if w is None:
            return [f"no existe el widget {wid!r}"]
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
        self._build()
        self._refresh(select_first=True)

    # --- construccion ---

    def _build(self):
        tk, ttk = self.tk, self.ttk
        raiz = ttk.Frame(self.root, padding=8)
        raiz.pack(fill="both", expand=True)

        izq = ttk.Frame(raiz)
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
        ttk.Button(izq, text="Borrar", command=self._remove).pack(fill="x")

        centro = ttk.Frame(raiz, padding=(12, 0))
        centro.pack(side="left", fill="both", expand=True)
        self.props = ttk.Frame(centro)
        self.props.pack(fill="both", expand=True)

        flechas = ttk.Frame(centro)
        flechas.pack(fill="x", pady=6)
        ttk.Label(flechas, text="Mover:").pack(side="left")
        for texto, (dx, dy) in (("←", (-1, 0)), ("→", (1, 0)),
                                ("↑", (0, -1)), ("↓", (0, 1)),
                                ("←10", (-10, 0)), ("→10", (10, 0)),
                                ("↑10", (0, -10)), ("↓10", (0, 10))):
            ttk.Button(flechas, text=texto, width=4,
                       command=lambda a=dx, b=dy: self._move(a, b)).pack(side="left")

        acciones = ttk.Frame(centro)
        acciones.pack(fill="x")
        ttk.Button(acciones, text="Guardar", command=self._save).pack(side="left")
        ttk.Button(acciones, text="Descartar cambios",
                   command=self._discard).pack(side="left", padx=4)
        self.estado = ttk.Label(centro, text="", wraplength=380, justify="left")
        self.estado.pack(fill="x", pady=(6, 0))

        self.der = ttk.Frame(raiz)
        self.der.pack(side="left", fill="both", expand=True)
        ttk.Label(self.der, text="Vista previa").pack(anchor="w")
        self.canvas = tk.Label(self.der, borderwidth=1, relief="solid",
                               anchor="n")
        self.canvas.pack(fill="both", expand=True)
        # El <Configure> se escucha en el CONTENEDOR, no en el Label: cambiar la
        # imagen cambia el tamano del Label y eso dispararia otro Configure,
        # o sea un bucle de redibujo.
        self.der.bind("<Configure>", self._on_resize)

        self.root.report_callback_exception = self._report_error
        self.root.bind("<Control-s>", lambda e: self._save())
        for tecla, (dx, dy) in (("<Left>", (-1, 0)), ("<Right>", (1, 0)),
                                ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.root.bind(tecla, lambda e, a=dx, b=dy: self._nudge(a, b))

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
        texto = f"error interno: {exc_type.__name__}: {exc}"
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
        self._draw_preview()
        self._show_errors()

    def _show_errors(self):
        marca = "•" if self.state.dirty else ""
        if self.state.errors:
            self.estado.config(text=f"{marca} " + " / ".join(self.state.errors[:3]),
                               foreground="#B00000")
        else:
            self.estado.config(text=f"{marca} sin errores", foreground="#006000")

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
            self._fields[clave] = var

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
        errores = self.state.save()
        self._show_errors()
        if not errores:
            self.estado.config(text="guardado; el panel lo levanta solo",
                               foreground="#006000")

    def _discard(self):
        self.state.reload()
        self._refresh(select_first=True)

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
