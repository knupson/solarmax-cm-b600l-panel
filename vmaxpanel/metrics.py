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
    _t("cpu.name_short", "Modelo de CPU (corto)"),
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


# Basura de marketing en Win32_Processor.Name, en el orden en que se saca.
# Todo generico: nada de "12th Gen" a mano, porque el perfil se comparte y la
# regla tiene que servir en la maquina de cualquiera.
_CPU_RUIDO = [
    re.compile(r"\((?:R|TM|tm|C)\)"),            # (R) (TM) (C)
    re.compile(r"[®™©]"),
    re.compile(r"^\s*\d+(?:th|st|nd|rd)\s+Gen\s+", re.I),   # "12th Gen "
    re.compile(r"\s*\bCPU\b\s*@.*$", re.I),      # " CPU @ 2.60GHz"
    re.compile(r"\s*@.*$"),                      # " @ 3.70GHz" sin la palabra CPU
    re.compile(r"\s*\b\d+-Core\b", re.I),        # " 6-Core"
    re.compile(r"\s*\bProcessor\b", re.I),
    re.compile(r"\b(?:Intel|AMD|Genuine\s*Intel|AuthenticAMD)\b", re.I),
]


def short_cpu_name(name):
    """"12th Gen Intel(R) Core(TM) i5-12400F" -> "Core i5-12400F".

    Deja familia + modelo, que es lo que cabe y lo que sirve en un panel de
    320 px. Simetrico entre marcas: Intel queda "Core i5-12400F" y AMD queda
    "Ryzen 5 5600X" -- en los dos casos la palabra de familia se conserva
    porque es parte de como se llama el producto.

    Un nombre que no matchea ningun patron se devuelve tal cual: mejor el
    original largo que un hueco en el panel. None y "" pasan derecho para no
    inventar un valor donde el provider no trajo dato.
    """
    if not isinstance(name, str) or not name.strip():
        return name
    s = name
    for patron in _CPU_RUIDO:
        s = patron.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;-")
    return s or name


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
