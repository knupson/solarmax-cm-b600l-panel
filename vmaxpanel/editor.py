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
from pathlib import Path

from PIL import Image

from .layout import loader, schema
from .metrics import METRICS
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


def demo_sample() -> dict:
    """Una muestra plausible para TODAS las metricas conocidas.

    El preview no puede estar lleno de "--": las metricas que esta maquina no
    sirve (por falta de GSA1, de WinRing0, de lo que sea) igual tienen que
    dibujarse con algo para poder juzgar el layout. Los numeros salen del
    medio del rango declarado en el spec, asi que una barra se ve a media
    asta en vez de vacia o saturada.
    """
    out = {}
    for mid, spec in METRICS.items():
        if spec.kind == "text":
            out[mid] = {"cpu.name": "INTEL CORE i5-12400F",
                        "gpu.name": "AMD RADEON RX 6800 XT",
                        "clock.time": "14:32",
                        "clock.date": "LUN 11 AGO"}.get(mid, mid.split(".")[-1].upper())
            continue
        lo = spec.min if spec.min is not None else 0.0
        hi = spec.max if spec.max is not None else lo + 100.0
        out[mid] = round(lo + (hi - lo) * 0.42, 2)
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
        loader.save(schema.build(self.raw), self.path)
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

PREVIEW_SCALE = 0.36            # 320x1480 -> 115x533, entra en una pantalla


class EditorWindow:
    def __init__(self, state: EditorState):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.state = state
        self.root = tk.Tk()
        self.root.title(f"VMax Panel — {state.path.name}")
        self._preview_img = None
        self._fields = {}
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

        der = ttk.Frame(raiz)
        der.pack(side="left", fill="y")
        ttk.Label(der, text="Vista previa").pack(anchor="w")
        self.canvas = tk.Label(der, borderwidth=1, relief="solid")
        self.canvas.pack()

        self.root.bind("<Control-s>", lambda e: self._save())
        for tecla, (dx, dy) in (("<Left>", (-1, 0)), ("<Right>", (1, 0)),
                                ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.root.bind(tecla, lambda e, a=dx, b=dy: self._move(a, b))

    # --- refresco ---

    def _selected(self):
        sel = self.lista.curselection()
        return self.lista.get(sel[0]) if sel else None

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

    def _draw_preview(self):
        img = self.state.preview()
        chico = img.resize((int(img.width * PREVIEW_SCALE),
                            int(img.height * PREVIEW_SCALE)), Image.LANCZOS)
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
        for fila, (clave, valor) in enumerate(w.items()):
            if clave in ("id", "type", "rules"):
                continue
            self.ttk.Label(self.props, text=clave).grid(row=fila, column=0,
                                                        sticky="w")
            var = self.tk.StringVar(value="" if valor is None else str(valor))
            entrada = self.ttk.Entry(self.props, textvariable=var, width=28)
            entrada.grid(row=fila, column=1, sticky="we", padx=4)
            entrada.bind("<FocusOut>", lambda e, k=clave: self._apply(k))
            entrada.bind("<Return>", lambda e, k=clave: self._apply(k))
            self._fields[clave] = var
        self.props.columnconfigure(1, weight=1)

    # --- acciones ---

    def _apply(self, clave):
        wid = self._selected()
        if wid is None:
            return
        self.state.set_field(wid, clave, self._fields[clave].get())
        self._draw_preview()
        self._show_errors()

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
