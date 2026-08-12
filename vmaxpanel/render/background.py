"""Fondos: solid, gradient, image (estaticos) y procedural, sequence (animados).

`video` sigue sin implementar y degrada a color plano con un aviso, en vez de
fallar: un perfil compartido que lo use tiene que seguir abriendo.

Los estaticos se cachean porque no cambian entre frames mientras el layout sea
el mismo; el loop de render solo copia el cache y le dibuja los widgets encima.
Los animados calculan cada cuadro en funcion del reloj, que se INYECTA: un fondo
que dependa de time.monotonic() directo no se puede testear de forma
determinista.

Quien construye un BackgroundSource es quien tiene que descartarlo y crear uno
nuevo si el layout (o el tamano) cambia: esta clase no se entera de esos cambios
sola, no hay invalidacion automatica.

Costos medidos en el spike de fase 2 (perfil real, 320x1480, presupuesto de
16,7 ms por cuadro a 60 fps): gradiente reconstruido 7,7 ms, secuencia 2,8 ms,
scroll procedural 0,5 ms.
"""
import math
import time
from pathlib import Path

from PIL import Image, ImageEnhance

from ..layout.schema import safe_asset_path

FALLBACK = (10, 12, 16)
SIN_IMPLEMENTAR = {"video"}
ANIMADOS = {"procedural", "sequence"}
PROCEDURALES = ("scroll", "pulse")
EXT_CUADROS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")


def parse_hex(color, default=FALLBACK):
    """Convierte "#RRGGBB" a (r, g, b). Devuelve `default`, en silencio, ante
    cualquier cosa que no matchee.

    validate() en layout/schema.py exige #RRGGBB para 'solid' y para cada
    stop de 'gradient' via _check_color, pero NO para bg.color en
    'image'/'sequence'/'video' (BACKGROUND_KEYS lo permite como clave sin
    validarlo): un color roto ahi si puede llegar hasta aca desde un layout
    compartido. El default silencioso es lo que evita que ese hueco de
    validacion tire una excepcion en vez de, como mucho, pintar el letterbox
    con un color que no es el pedido.
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
        self._tira = None          # el gradiente y su espejo, para el scroll
        self._cuadros = None       # rutas de una sequence, leidas una sola vez
        # monotonic y no time(): un ajuste de hora del sistema -- o el cambio de
        # horario -- no puede hacer saltar la animacion hacia atras.
        self._clock = clock or time.monotonic

    @property
    def animated(self) -> bool:
        return self.bg.type in ANIMADOS

    def frame(self) -> Image.Image:
        """Devuelve una copia del fondo. Copia, no el original: quien recibe el
        frame le dibuja widgets encima, y si eso mutara el cache el siguiente
        frame arrancaria con la basura del anterior."""
        if self.animated:
            return self._animado(self._clock())
        if self._cache is None:
            self._cache = self._build()
        return self._cache.copy()

    # --- animados ---

    def _animado(self, t) -> Image.Image:
        if self.bg.type == "sequence":
            return self._sequence(t)
        if self.bg.name == "scroll":
            return self._scroll(t)
        if self.bg.name == "pulse":
            return self._pulse(t)
        self._avisar(f"generador procedural {self.bg.name!r} desconocido; "
                     f"se usa un color plano")
        return self._solid()

    def _avisar(self, texto):
        """Aviso sin duplicar.

        Un fondo animado recalcula hasta 60 veces por segundo: si cada vuelta
        agregara su aviso, warnings() creceria sin limite y la bandeja mostraria
        el mismo texto mil veces.
        """
        if texto not in self.warnings:
            self.warnings.append(texto)

    def _tira_doble(self) -> Image.Image:
        """El gradiente y su espejo apilados: 2x el alto del panel.

        Es lo que hace que el scroll cierre sin salto. Con una sola copia, al
        dar la vuelta el ultimo color choca con el primero y se ve un tiron en
        cada ciclo; con el espejo el recorrido es continuo en los dos sentidos.
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
        # La ventana quedo partida entre el final de la tira y su principio.
        out = Image.new("RGB", self.size)
        primera = alto * 2 - desp
        out.paste(tira.crop((0, desp, ancho, alto * 2)), (0, 0))
        out.paste(tira.crop((0, 0, ancho, alto - primera)), (0, primera))
        return out

    def _pulse(self, t) -> Image.Image:
        """El gradiente con el brillo respirando.

        El factor no baja de 0.55: un fondo que se va a negro deja el texto
        flotando en el vacio, y el punto de un fondo es acompanar, no competir.
        """
        if self._cache is None:
            self._cache = self._gradient()
        periodo = self.bg.period if self.bg.period and self.bg.period > 0 else 6.0
        fase = (t % periodo) / periodo
        k = 0.775 + 0.225 * math.cos(2 * math.pi * fase)
        return ImageEnhance.Brightness(self._cache).enhance(k)

    def _lista_cuadros(self):
        """Rutas de los cuadros, ordenadas y leidas una sola vez.

        Una sola vez porque el conjunto de ids/cuadros no puede cambiar entre
        muestras: es la misma razon por la que los adaptadores de red y el
        indice de los discos se fijan al arrancar.
        """
        if self._cuadros is not None:
            return self._cuadros
        self._cuadros = []
        seguro = safe_asset_path(self.bg.src) if self.bg.src else None
        if seguro is None:
            self._avisar(f"fondo 'sequence' con ruta invalida o fuera del "
                         f"directorio de assets: {self.bg.src!r}")
            return self._cuadros
        try:
            self._cuadros = sorted(p for p in (self.assets_dir / seguro).iterdir()
                                   if p.suffix.lower() in EXT_CUADROS)
        except Exception as e:
            self._avisar(f"no se pudo leer la secuencia {self.bg.src!r}: {e}")
            return self._cuadros
        if not self._cuadros:
            self._avisar(f"la secuencia {self.bg.src!r} no tiene ningun cuadro")
        return self._cuadros

    def _sequence(self, t) -> Image.Image:
        """Cuadro `int(t * fps) % n`, decodificado en el momento.

        Los cuadros decodificados NO se cachean a proposito: a 320x1480 cada uno
        ocupa 1,4 MB en RAM, asi que una secuencia de 60 se comeria 85 MB para
        ahorrar los 2,8 ms que cuesta decodificar y escalar (medido en el
        spike). El archivo ya lo cachea el sistema operativo.
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
            self._avisar(f"no se pudo abrir el cuadro {cuadros[idx].name!r}: {e}")
            return self._solid()

    # --- estaticos ---

    def _build(self) -> Image.Image:
        # Se llama una sola vez (frame() cachea el resultado), asi que los
        # warnings que agregan las ramas de aca abajo no se duplican aunque
        # frame() se llame muchas veces.
        t = self.bg.type
        if t in SIN_IMPLEMENTAR:
            self.warnings.append(
                f"fondo de tipo {t!r} todavia no esta implementado; "
                f"se usa un color plano")
            return self._solid()
        if t == "gradient":
            return self._gradient()
        if t == "image":
            return self._image()
        return self._solid()

    def _solid(self):
        return Image.new("RGB", self.size, parse_hex(self.bg.color))

    def _gradient(self):
        """Degradado lineal entre paradas ordenadas por 'at'.

        Angulo: solo distingue vertical de horizontal, no un angulo
        arbitrario. `angle % 180` en [45, 135) = vertical; el resto =
        horizontal. No hay diagonales rotadas -- fase 1 no las necesita.

        La tira muestreada tiene 1px en el eje perpendicular al degradado,
        pero YA tiene resolucion completa en el eje del degradado (`n` es el
        ancho/alto real, no 1). El resize final solo estira ese eje
        perpendicular; como cada fila/columna es de un solo color, no hay
        banding ni perdida de paradas intermedias por la interpolacion.
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
            self.warnings.append("fondo de tipo 'image' sin src")
            return self._solid()
        # safe_asset_path() ya corrio en schema.build(), pero BackgroundSource
        # tambien se instancia directo con un Background armado a mano (como
        # en los tests). Revalidar es la misma defensa en profundidad que ya
        # usa widgets._draw_image con w.src.
        safe_src = safe_asset_path(self.bg.src)
        if safe_src is None:
            self.warnings.append(
                f"fondo de tipo 'image' con ruta invalida: {self.bg.src!r}")
            return self._solid()
        path = self.assets_dir / safe_src
        try:
            src = Image.open(path).convert("RGB")
        except Exception as e:
            self.warnings.append(f"no se pudo abrir el fondo {self.bg.src!r}: {e}")
            return self._solid()
        return self._fit(src)

    def _fit(self, src):
        tw, th = self.size
        if self.bg.fit == "stretch":
            return src.resize(self.size, Image.LANCZOS)
        sw, sh = src.size
        k = max(tw / sw, th / sh) if self.bg.fit == "cover" else min(tw / sw, th / sh)
        # round(), no int(): el eje que manda deberia dar sw*k == tw (o
        # sh*k == th) exacto, pero en punto flotante puede quedar en
        # 199.99999999999997. int() trunca a 199 y "cover" deja un borde de
        # 1px sin cubrir; round() lo corrige sin tocar los casos ya exactos.
        scaled = src.resize((max(1, round(sw * k)), max(1, round(sh * k))), Image.LANCZOS)
        out = Image.new("RGB", self.size, parse_hex(self.bg.color, (0, 0, 0)))
        out.paste(scaled, ((tw - scaled.width) // 2, (th - scaled.height) // 2))
        return out
