"""Ids canonicos de metrica, desacoplados de su origen.

An id that no available provider serves reads as UNAVAILABLE, a state distinct
from None: None means "the provider exists but this sample brought no value".
"""
import re
import unicodedata
from dataclasses import dataclass


class _Unavailable:
    """Sentinel: no provider on this machine serves this metric."""

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
    _t("cpu.name", "CPU model"),
    _t("cpu.name_short", "CPU model (short)"),
    _n("cpu.load", "CPU load", "%", 0.0, 100.0),
    _n("cpu.temp", "CPU temperature", "°C", 0.0, 110.0),
    _n("cpu.clock", "CPU clock", "MHz", 0.0, 6000.0),
    _n("cpu.vcore", "VCore", "V", 0.0, 2.0),
    _n("cpu.vrm_temp", "VRM temperature", "°C", 0.0, 120.0),
    _n("cpu.power", "CPU power", "W", 0.0, 300.0),
    _n("cpu.fan", "CPU fan", "RPM", 0.0, 3000.0),
    _t("mb.name", "Motherboard model"),
    _t("gpu.name", "GPU model"),
    _n("gpu.load", "GPU load", "%", 0.0, 100.0),
    _n("gpu.temp", "GPU temperature", "°C", 0.0, 110.0),
    _n("gpu.hotspot", "GPU hot spot", "°C", 0.0, 130.0),
    _n("gpu.clock", "GPU clock", "MHz", 0.0, 4000.0),
    _n("gpu.power", "GPU power", "W", 0.0, 600.0),
    _n("gpu.vram", "VRAM used", "%", 0.0, 100.0),
    _n("gpu.fan", "GPU fan", "RPM", 0.0, 4000.0),
    _n("mem.load", "RAM usage", "%", 0.0, 100.0),
    _n("mem.used", "RAM used", "GiB", 0.0, 256.0),
    _n("mem.total", "RAM total", "GiB", 0.0, 256.0),
    # MT/s (megatransfers), not MHz: it is the unit SMBIOS reports and the one the
    # kit is sold under. DDR5-5600 is 5600 MT/s at a 2800 MHz clock.
    _n("mem.speed", "RAM speed", "MT/s", 0.0, 12000.0),
    _n("net.down", "Download", "B/s", 0.0, None),
    _n("net.up", "Upload", "B/s", 0.0, None),
    _t("clock.time", "Time"),
    _t("clock.time_hms", "Time with seconds"),
    _t("clock.date", "Date"),
    _n("sys.uptime", "Uptime", "s", 0.0, None),
    _n("sys.procs", "Processes", "", 0.0, None),
]}

_DISK_RE = re.compile(r"^disk\.temp\.(\d+)$")

DISK_TEMP_SPEC = _n("disk.temp.N", "Disk temperature", "°C", 0.0, 100.0)


def slug(nombre) -> str:
    """"Wi-Fi 2" -> "wi-fi-2": a device name fit for an id.

    Metric ids are written by hand in the profile and travel through JSON, so they
    cannot carry spaces, capitals or accents. The pretty name is not lost: the
    provider publishes it in its catalogue, which is what the editor
    muestra.
    """
    if not isinstance(nombre, str):
        return ""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9-]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s)


# --- familias de metricas por dispositivo ---
#
# A metric id is validated against these patterns when it is not in METRICS. This
# is needed because instances are discovered at run time -- which volumes exist,
# how many cores, which adapters -- and the layout validator runs without querying
# hardware: it has to be able to say that `vol.D.free` is a legitimate id even
# though THIS machine has no D drive.
#
# The label here names the instance by its id ("D: -- free"). The provider refines
# it with the device's real name ("D: GAMES -- free") in its catalogue, which is
# where the editor takes it from.
_MEDIDAS_VOL = {
    "free": ("free", "GiB", 0.0, None),
    "used": ("used", "GiB", 0.0, None),
    "total": ("total", "GiB", 0.0, None),
    "load": ("usage", "%", 0.0, 100.0),
}
_MEDIDAS_CORE = {
    "temp": ("temperature", "°C", 0.0, 110.0),
    "clock": ("frequency", "MHz", 0.0, 6000.0),
    "load": ("load", "%", 0.0, 100.0),
}
_MEDIDAS_NET = {
    "down": ("download", "B/s", 0.0, None),
    "up": ("upload", "B/s", 0.0, None),
}

_FAMILIAS = [
    (re.compile(r"^vol\.([A-Z])\.(free|used|total|load)$"),
     lambda m: (f"{m.group(1)}: — {_MEDIDAS_VOL[m.group(2)][0]}",
                *_MEDIDAS_VOL[m.group(2)][1:])),
    (re.compile(r"^core\.(\d+)\.(temp|clock|load)$"),
     lambda m: (f"Core {m.group(1)} — {_MEDIDAS_CORE[m.group(2)][0]}",
                *_MEDIDAS_CORE[m.group(2)][1:])),
    (re.compile(r"^fan\.(\d+)\.rpm$"),
     lambda m: (f"Fan {m.group(1)}", "RPM", 0.0, 3000.0)),
    (re.compile(r"^mb\.temp\.(\d+)$"),
     lambda m: (f"Motherboard — temperature {m.group(1)}", "°C", 0.0, 100.0)),
    (re.compile(r"^net\.([a-z0-9][a-z0-9-]*)\.(down|up)$"),
     lambda m: (f"{m.group(1)} — {_MEDIDAS_NET[m.group(2)][0]}",
                *_MEDIDAS_NET[m.group(2)][1:])),
]


# The group name per id prefix. The prefix is technical ("net", "mem") and the
# group is what the user reads in the editor's selector, so they cannot be the
# same thing. A concrete device (a volume, a named fan) is refined by the provider
# through groups().
_GRUPOS = {
    "cpu": "CPU", "gpu": "GPU", "mem": "RAM", "net": "Network",
    "clock": "Clock", "disk": "Disks", "vol": "Disks", "sys": "System",
    "core": "CPU cores", "fan": "Fans", "mb": "Motherboard",
}


def group_for(mid) -> str:
    """The group a metric belongs to, by friendly name."""
    if not isinstance(mid, str) or not mid:
        return "Other"
    prefijo = mid.split(".", 1)[0]
    return _GRUPOS.get(prefijo, prefijo.upper())


def _familia(mid: str):
    """(label, unit, lo, hi) if `mid` belongs to a family, or None."""
    for patron, armar in _FAMILIAS:
        m = patron.match(mid)
        if m:
            return armar(m)
    return None


def disk_metric(n: int) -> str:
    """The id for disk n's temperature.

    It raises on a negative index rather than returning "disk.temp.-1", which
    is_metric() rejects: a generator producing ids the validator refuses is a trap
    for whoever uses it.
    """
    n = int(n)
    if n < 0:
        raise ValueError(f"indice de disco invalido: {n}")
    return f"disk.temp.{n}"


# Marketing noise in Win32_Processor.Name, in the order it is stripped. All of it
# generic: no hand-written "12th Gen", because the profile gets shared and the
# rule has to work on anybody's machine.
_CPU_RUIDO = [
    re.compile(r"\((?:R|TM|tm|C)\)"),            # (R) (TM) (C)
    re.compile(r"[®™©]"),
    re.compile(r"^\s*\d+(?:th|st|nd|rd)\s+Gen\s+", re.I),   # "12th Gen "
    re.compile(r"\s*\bCPU\b\s*@.*$", re.I),      # " CPU @ 2.60GHz"
    re.compile(r"\s*@.*$"),                      # " @ 3.70GHz" without the word CPU
    re.compile(r"\s*\b\d+-Core\b", re.I),        # " 6-Core"
    re.compile(r"\s*\bProcessor\b", re.I),
    re.compile(r"\b(?:Intel|AMD|Genuine\s*Intel|AuthenticAMD)\b", re.I),
]


def short_cpu_name(name):
    """"12th Gen Intel(R) Core(TM) i5-12400F" -> "Core i5-12400F".

    It leaves family + model, which is what fits and what is useful on a 320 px
    panel. Symmetric across brands: Intel ends up "Core i5-12400F" and AMD ends up
    "Ryzen 5 5600X" -- in both cases the family word is kept because it is part of
    what the product is called.

    A name matching no pattern is returned as-is: better the long original than a
    hole in the panel. None and "" pass straight through so as not to invent a
    value where the provider brought no data.
    """
    if not isinstance(name, str) or not name.strip():
        return name
    s = name
    for patron in _CPU_RUIDO:
        s = patron.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;-")
    return s or name


def is_metric(mid) -> bool:
    """False for anything that is not a known id, including things that
    no es texto.

    The argument comes from the user's JSON through schema.validate(), so it can
    be an integer or a list. Without the type check, _DISK_RE.match raised
    TypeError and blew up the validator itself: loads() only converts
    JSONDecodeError and LayoutError, and ProfileStore/Engine only catch
    LayoutError, so the error escaped all the way to killing start-up or the
    render loop instead of being reported as "unknown metric".
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
        # The label CARRIES the index. Without it every disk shares "Disk
        # temperature" and the editor's selector cannot tell them apart: picking
        # one writes another.
        return MetricSpec(mid, f"Disk {m.group(1)} — temperature",
                          DISK_TEMP_SPEC.unit, "number",
                          DISK_TEMP_SPEC.min, DISK_TEMP_SPEC.max)
    fam = _familia(mid)
    if fam is not None:
        label, unidad, lo, hi = fam
        return MetricSpec(mid, label, unidad, "number", lo, hi)
    return None
