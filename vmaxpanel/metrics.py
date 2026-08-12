"""Ids canonicos de metrica, desacoplados de su origen.

Un id que ningun provider disponible sirve se lee como UNAVAILABLE, estado
distinto de None: None es "el provider existe pero esta muestra no trajo valor".
"""
import re
from dataclasses import dataclass


class _Unavailable:
    """Sentinel: ningun provider de esta maquina sirve esta metrica."""

    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __bool__(self):
        return False

    def __repr__(self):
        return "UNAVAILABLE"


UNAVAILABLE = _Unavailable()


@dataclass(frozen=True)
class MetricSpec:
    id: str
    label: str
    unit: str
    kind: str            # "number" | "text"
    min: float | None = None
    max: float | None = None


def _n(mid, label, unit, lo=None, hi=None):
    return MetricSpec(mid, label, unit, "number", lo, hi)


def _t(mid, label):
    return MetricSpec(mid, label, "", "text")


METRICS: dict[str, MetricSpec] = {m.id: m for m in [
    _t("cpu.name", "Modelo de CPU"),
    _n("cpu.load", "Carga de CPU", "%", 0.0, 100.0),
    _n("cpu.temp", "Temperatura de CPU", "°C", 0.0, 110.0),
    _n("cpu.clock", "Clock de CPU", "MHz", 0.0, 6000.0),
    _n("cpu.vcore", "VCore", "V", 0.0, 2.0),
    _n("cpu.vrm_temp", "Temperatura de VRM", "°C", 0.0, 120.0),
    _n("cpu.power", "Consumo de CPU", "W", 0.0, 300.0),
    _n("cpu.fan", "Fan de CPU", "RPM", 0.0, 3000.0),
    _t("gpu.name", "Modelo de GPU"),
    _n("gpu.load", "Carga de GPU", "%", 0.0, 100.0),
    _n("gpu.temp", "Temperatura de GPU", "°C", 0.0, 110.0),
    _n("gpu.hotspot", "Hot spot de GPU", "°C", 0.0, 130.0),
    _n("gpu.clock", "Clock de GPU", "MHz", 0.0, 4000.0),
    _n("gpu.power", "Consumo de GPU", "W", 0.0, 600.0),
    _n("gpu.vram", "VRAM usada", "%", 0.0, 100.0),
    _n("gpu.fan", "Fan de GPU", "RPM", 0.0, 4000.0),
    _n("mem.load", "RAM usada", "%", 0.0, 100.0),
    _n("mem.used", "RAM usada", "GiB", 0.0, 256.0),
    _n("mem.total", "RAM total", "GiB", 0.0, 256.0),
    # MT/s (megatransfers), no MHz: es la unidad que reporta SMBIOS y la que
    # usa el kit para venderse. DDR5-5600 son 5600 MT/s a 2800 MHz de reloj.
    _n("mem.speed", "Velocidad de RAM", "MT/s", 0.0, 12000.0),
    _n("net.down", "Bajada", "B/s", 0.0, None),
    _n("net.up", "Subida", "B/s", 0.0, None),
    _t("clock.time", "Hora"),
    _t("clock.date", "Fecha"),
]}

_DISK_RE = re.compile(r"^disk\.temp\.(\d+)$")

DISK_TEMP_SPEC = _n("disk.temp.N", "Temperatura de disco", "°C", 0.0, 100.0)


def disk_metric(n: int) -> str:
    return f"disk.temp.{n}"


def is_metric(mid) -> bool:
    """False para cualquier cosa que no sea un id conocido, incluido lo que
    no es texto.

    El argumento viene del JSON del usuario a traves de schema.validate(),
    asi que puede ser un entero o una lista. Sin el chequeo de tipo,
    _DISK_RE.match tiraba TypeError y hacia reventar al propio validador:
    loads() solo convierte JSONDecodeError y LayoutError, y
    ProfileStore/Engine capturan solo LayoutError, asi que el error se
    escapaba hasta matar el arranque o el loop de render en vez de quedar
    reportado como "metrica desconocida".
    """
    if not isinstance(mid, str):
        return False
    return mid in METRICS or bool(_DISK_RE.match(mid))


def spec_for(mid) -> MetricSpec | None:
    if not isinstance(mid, str):
        return None
    if mid in METRICS:
        return METRICS[mid]
    if _DISK_RE.match(mid):
        return MetricSpec(mid, DISK_TEMP_SPEC.label, DISK_TEMP_SPEC.unit,
                          "number", DISK_TEMP_SPEC.min, DISK_TEMP_SPEC.max)
    return None
