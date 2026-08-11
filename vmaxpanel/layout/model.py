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
