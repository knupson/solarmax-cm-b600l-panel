"""Ids canonicos de metrica, desacoplados de su origen.

Un id que ningun provider disponible sirve se lee como UNAVAILABLE, estado
distinto de None: None es "el provider existe pero esta muestra no trajo valor".
"""
import re
import unicodedata
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
    _n("mem.load", "Uso de RAM", "%", 0.0, 100.0),
    _n("mem.used", "RAM usada", "GiB", 0.0, 256.0),
    _n("mem.total", "RAM total", "GiB", 0.0, 256.0),
    # MT/s (megatransfers), no MHz: es la unidad que reporta SMBIOS y la que
    # usa el kit para venderse. DDR5-5600 son 5600 MT/s a 2800 MHz de reloj.
    _n("mem.speed", "Velocidad de RAM", "MT/s", 0.0, 12000.0),
    _n("net.down", "Bajada", "B/s", 0.0, None),
    _n("net.up", "Subida", "B/s", 0.0, None),
    _t("clock.time", "Hora"),
    _t("clock.time_hms", "Hora con segundos"),
    _t("clock.date", "Fecha"),
    _n("sys.uptime", "Encendida hace", "s", 0.0, None),
    _n("sys.procs", "Procesos", "", 0.0, None),
]}

_DISK_RE = re.compile(r"^disk\.temp\.(\d+)$")

DISK_TEMP_SPEC = _n("disk.temp.N", "Temperatura de disco", "°C", 0.0, 100.0)


def slug(nombre) -> str:
    """"Wi-Fi 2" -> "wi-fi-2": nombre de dispositivo apto para un id.

    Los ids de metrica se escriben a mano en el perfil y viajan por JSON, asi
    que no pueden llevar espacios ni mayusculas ni acentos. El nombre lindo no
    se pierde: lo publica el provider en su catalogo, que es lo que el editor
    muestra.
    """
    if not isinstance(nombre, str):
        return ""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9-]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s)


# --- familias de metricas por dispositivo ---
#
# Un id de metrica se valida contra estos patrones cuando no esta en METRICS.
# Hace falta porque las instancias se descubren en tiempo de ejecucion -- que
# volumenes hay, cuantos nucleos, que adaptadores -- y el validador de layouts
# corre sin consultar hardware: tiene que poder decir que `vol.D.free` es un id
# legitimo aunque en ESTA maquina no exista la D.
#
# La etiqueta de aca nombra la instancia por su id ("D: -- libre"). El provider
# la refina con el nombre real del dispositivo ("D: JUEGOS -- libre") en su
# catalogo, que es de donde la saca el editor.
_MEDIDAS_VOL = {
    "free": ("libre", "GiB", 0.0, None),
    "used": ("usado", "GiB", 0.0, None),
    "total": ("total", "GiB", 0.0, None),
    "load": ("uso", "%", 0.0, 100.0),
}
_MEDIDAS_CORE = {
    "temp": ("temperatura", "°C", 0.0, 110.0),
    "clock": ("frecuencia", "MHz", 0.0, 6000.0),
    "load": ("carga", "%", 0.0, 100.0),
}
_MEDIDAS_NET = {
    "down": ("bajada", "B/s", 0.0, None),
    "up": ("subida", "B/s", 0.0, None),
}

_FAMILIAS = [
    (re.compile(r"^vol\.([A-Z])\.(free|used|total|load)$"),
     lambda m: (f"{m.group(1)}: — {_MEDIDAS_VOL[m.group(2)][0]}",
                *_MEDIDAS_VOL[m.group(2)][1:])),
    (re.compile(r"^core\.(\d+)\.(temp|clock|load)$"),
     lambda m: (f"Núcleo {m.group(1)} — {_MEDIDAS_CORE[m.group(2)][0]}",
                *_MEDIDAS_CORE[m.group(2)][1:])),
    (re.compile(r"^fan\.(\d+)\.rpm$"),
     lambda m: (f"Fan {m.group(1)}", "RPM", 0.0, 3000.0)),
    (re.compile(r"^mb\.temp\.(\d+)$"),
     lambda m: (f"Placa — temperatura {m.group(1)}", "°C", 0.0, 100.0)),
    (re.compile(r"^net\.([a-z0-9][a-z0-9-]*)\.(down|up)$"),
     lambda m: (f"{m.group(1)} — {_MEDIDAS_NET[m.group(2)][0]}",
                *_MEDIDAS_NET[m.group(2)][1:])),
]


# Nombre del grupo por prefijo de id. El prefijo es tecnico ("net", "mem") y
# el grupo lo lee el usuario en el selector del editor, asi que no pueden ser
# lo mismo. Un dispositivo concreto (un volumen, un fan con nombre) lo refina
# el provider con groups().
_GRUPOS = {
    "cpu": "CPU", "gpu": "GPU", "mem": "Memoria RAM", "net": "Red",
    "clock": "Reloj", "disk": "Discos", "vol": "Discos", "sys": "Sistema",
    "core": "Núcleos de CPU", "fan": "Ventiladores", "mb": "Placa madre",
}


def group_for(mid) -> str:
    """Grupo al que pertenece una metrica, en nombre amigable."""
    if not isinstance(mid, str) or not mid:
        return "Otras"
    prefijo = mid.split(".", 1)[0]
    return _GRUPOS.get(prefijo, prefijo.upper())


def _familia(mid: str):
    """(label, unit, lo, hi) si `mid` pertenece a una familia, o None."""
    for patron, armar in _FAMILIAS:
        m = patron.match(mid)
        if m:
            return armar(m)
    return None


def disk_metric(n: int) -> str:
    """Id de la temperatura del disco n.

    Levanta con un indice negativo en vez de devolver "disk.temp.-1", que
    is_metric() rechaza: un generador que produce ids que el validador no
    acepta es una trampa para el que lo use.
    """
    n = int(n)
    if n < 0:
        raise ValueError(f"indice de disco invalido: {n}")
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
    return (mid in METRICS or bool(_DISK_RE.match(mid))
            or _familia(mid) is not None)


def spec_for(mid) -> MetricSpec | None:
    if not isinstance(mid, str):
        return None
    if mid in METRICS:
        return METRICS[mid]
    m = _DISK_RE.match(mid)
    if m:
        # La etiqueta LLEVA el indice. Sin eso los tres discos comparten
        # "Temperatura de disco" y el selector del editor no los puede
        # distinguir: elegir uno escribe otro.
        return MetricSpec(mid, f"Disco {m.group(1)} — temperatura",
                          DISK_TEMP_SPEC.unit, "number",
                          DISK_TEMP_SPEC.min, DISK_TEMP_SPEC.max)
    fam = _familia(mid)
    if fam is not None:
        label, unidad, lo, hi = fam
        return MetricSpec(mid, label, unidad, "number", lo, hi)
    return None
