"""Fondos. Fase 1: solid, gradient e image.

sequence/video/procedural son fase 2 y degradan a solid con un aviso, en vez de
fallar: un perfil compartido que los use tiene que seguir abriendo.

El fondo se cachea porque no cambia entre frames mientras el layout sea el
mismo; el loop de render solo copia el cache y le dibuja los widgets encima.
Quien construye un BackgroundSource es quien tiene que descartarlo y crear uno
nuevo si el layout (o el tamano) cambia: esta clase no se entera de esos
cambios sola, no hay invalidacion automatica.
"""
from pathlib import Path

from PIL import Image

from ..layout.schema import safe_asset_path

FALLBACK = (10, 12, 16)
PHASE2 = {"sequence", "video", "procedural"}


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
    animated = False        # fase 2 lo pone en True para sequence/video/procedural

    def __init__(self, bg, size, assets_dir="."):
        self.bg = bg
        self.size = (size.width, size.height)
        self.assets_dir = Path(assets_dir)
        self.warnings: list[str] = []
        self._cache = None

    def frame(self) -> Image.Image:
        """Devuelve una copia del fondo. Copia, no el original: quien recibe
        el frame le dibuja widgets encima, y si eso mutara el cache el
        siguiente frame arrancaria con la basura del anterior."""
        if self._cache is None:
            self._cache = self._build()
        return self._cache.copy()

    def _build(self) -> Image.Image:
        # Se llama una sola vez (frame() cachea el resultado), asi que los
        # warnings que agregan las ramas de aca abajo no se duplican aunque
        # frame() se llame muchas veces.
        t = self.bg.type
        if t in PHASE2:
            self.warnings.append(
                f"fondo de tipo {t!r} todavia no esta implementado (fase 2); "
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
