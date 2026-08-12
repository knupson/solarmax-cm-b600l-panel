"""Modelo tipado de un layout. Puramente declarativo: nada aca se ejecuta."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Size:
    width: int
    height: int


@dataclass
class PanelCfg:
    rotate: int = 0
    brightness: int = 100
    fps: float = 1.0
    jpeg_quality: int = 82


@dataclass(frozen=True)
class Font:
    family: str
    size: int
    bold: bool = False
    # Familias a probar si `family` no esta instalada, en orden. Existe porque un
    # perfil compartido nombra fuentes que la otra maquina puede no tener: Apex pide
    # Franklin Gothic Medium Cond, que viene con OFFICE y no con Windows. Sin la
    # cadena, alla se ve otra tipografia y lo unico que la app podia hacer era
    # avisar; con la cadena el perfil declara con que reemplazarla.
    fallbacks: tuple = ()


@dataclass(frozen=True)
class Rule:
    op: str          # ">" | ">=" | "<" | "<="
    value: float
    color: str

    def matches(self, v) -> bool:
        if not isinstance(v, (int, float)):
            return False
        if self.op == ">":
            return v > self.value
        if self.op == ">=":
            return v >= self.value
        if self.op == "<":
            return v < self.value
        return v <= self.value


@dataclass
class Background:
    type: str = "solid"
    color: str = "#000000"
    stops: list = field(default_factory=list)
    angle: float = 90.0
    src: str | None = None
    fit: str = "cover"
    # --- animados (fase 2) ---
    # `name` elige el generador de 'procedural'. Los dos reusan `stops`, asi que
    # un fondo animado se configura con el mismo vocabulario que el gradiente
    # que el usuario ya conoce.
    name: str = "scroll"
    speed: float = 20.0        # px/s, para scroll
    period: float = 6.0        # s de un ciclo completo, para pulse
    fps: float = 10.0          # cuadros por segundo de una 'sequence'


@dataclass
class Widget:
    id: str
    type: str
    x: int
    y: int


@dataclass
class TextWidget(Widget):
    metric: str = ""
    font: str = ""
    color: str = "#FFFFFF"
    format: str = "{}"
    align: str = "left"
    humanize: str = "none"
    rules: list[Rule] = field(default_factory=list)


@dataclass
class LabelWidget(Widget):
    text: str = ""
    font: str = ""
    color: str = "#FFFFFF"
    align: str = "left"


@dataclass
class BarWidget(Widget):
    metric: str = ""
    w: int = 0
    h: int = 0
    radius: int = 0
    fill: str = "#3987E5"
    track: str = "#242834"
    min: float | None = None
    max: float | None = None


@dataclass
class ArcWidget(Widget):
    metric: str = ""
    r: int = 0
    thickness: int = 8
    start_angle: float = 135.0
    sweep: float = 270.0
    fill: str = "#3987E5"
    track: str = "#242834"
    min: float | None = None
    max: float | None = None


@dataclass
class GraphWidget(Widget):
    metric: str = ""
    w: int = 0
    h: int = 0
    color: str = "#3987E5"
    track: str = "#242834"
    samples: int = 120
    min: float | None = None
    max: float | None = None


@dataclass
class ImageWidget(Widget):
    src: str = ""
    w: int = 0
    h: int = 0


@dataclass
class RectWidget(Widget):
    """Rectangulo estatico: divisores, marcos y bloques de color.

    No tiene `metric` ni `rules` a proposito -- es decoracion, no una
    lectura. `w`/`h` son el tamano real en pixeles (h=1 es una linea de
    1 px), a diferencia de bar/graph, que usan la caja inclusive de Pillow.

    `fill` y `stroke` son opcionales por separado, pero al menos uno tiene
    que estar: el validador rechaza un rect sin ninguno de los dos porque
    no dibujaria nada y no habria forma de notarlo mirando el panel.
    """
    w: int = 0
    h: int = 0
    radius: int = 0
    fill: str | None = None
    stroke: str | None = None
    stroke_width: int = 1


@dataclass
class Layout:
    version: int
    name: str
    designed_for: Size
    panel: PanelCfg
    fonts: dict[str, Font]
    background: Background
    widgets: list[Widget]

    def font_for(self, alias: str) -> Font:
        return self.fonts[alias]
