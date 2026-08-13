"""WmiProvider: espacio en disco por volumen, uptime, procesos.

Tested with a fake CIM runner, without touching WMI: what matters is the mapping
to metric ids, the catalogue of friendly names and the cache -- not
PowerShell.
"""
import pytest

from vmaxpanel.metrics import is_metric
from vmaxpanel.providers.wmi_provider import WmiProvider

VOLUMENES = [
    {"letra": "C", "etiqueta": "", "libre": 270.0, "total": 476.0},
    {"letra": "D", "etiqueta": "JUEGOS", "libre": 453.6, "total": 953.9},
    {"letra": "G", "etiqueta": "Google Drive", "libre": 256.5, "total": 476.0},
]


class FakeCim:
    """Devuelve datos fijos y cuenta cuantas veces se lo consultan."""

    def __init__(self, volumenes=None, boot_hace=8.9, procesos=193):
        self.volumenes = VOLUMENES if volumenes is None else volumenes
        self.boot_hace, self.procesos = boot_hace, procesos
        self.llamadas = 0

    def __call__(self):
        self.llamadas += 1
        return {"volumenes": self.volumenes, "uptime": self.boot_hace * 3600,
                "procesos": self.procesos}


def test_serves_one_metric_set_per_volume():
    p = WmiProvider(cim=FakeCim())
    assert p.probe() is True
    servidas = p.metrics()
    for letra in ("C", "D", "G"):
        for medida in ("free", "used", "total", "load"):
            assert f"vol.{letra}.{medida}" in servidas
    assert "sys.uptime" in servidas and "sys.procs" in servidas


def test_every_served_id_is_a_valid_metric():
    """If an id the provider serves does not validate, Registry rejects it in its
    constructor and the whole start-up falls over."""
    for mid in WmiProvider(cim=FakeCim()).metrics():
        assert is_metric(mid), mid


def test_values_are_computed_per_volume():
    m = WmiProvider(cim=FakeCim()).read()
    assert m["vol.C.free"] == 270.0
    assert m["vol.C.total"] == 476.0
    assert m["vol.C.used"] == pytest.approx(206.0)
    assert m["vol.C.load"] == pytest.approx(43.28, abs=0.1)
    assert m["vol.D.load"] == pytest.approx(52.45, abs=0.1)


def test_the_catalog_carries_the_friendly_device_name():
    """The id is technical (vol.D.free) and the label is what the user sees in the
    editor: it has to say which disk it is about, by the name they gave it."""
    cat = WmiProvider(cim=FakeCim()).catalog()
    assert "JUEGOS" in cat["vol.D.free"].label
    assert "D:" in cat["vol.D.free"].label
    assert "free" in cat["vol.D.free"].label.lower()
    # a volume with no label must not be left with a stray dash
    assert cat["vol.C.free"].label.startswith("C:")
    assert "Google Drive" in cat["vol.G.load"].label


def test_the_catalog_groups_by_device():
    """The editor groups by device, so the catalogue has to say which device each
    metric belongs to."""
    p = WmiProvider(cim=FakeCim())
    grupos = p.groups()
    assert grupos["vol.D.free"] == "Disk D: (JUEGOS)"
    assert grupos["vol.C.free"] == "Disk C:"
    assert grupos["sys.uptime"] == "Sistema"


def test_wmi_is_not_queried_on_every_read():
    """Querying the four volumes costs ~300 ms. At 1 fps that is a third of the
    frame budget for a value that changes every few minutes."""
    cim = FakeCim()
    p = WmiProvider(cim=cim, ttl=30.0)
    for _ in range(5):
        p.read()
    assert cim.llamadas == 1


def test_a_failing_query_does_not_break_the_read():
    def explota():
        raise OSError("WMI no responde")

    p = WmiProvider(cim=explota)
    assert p.probe() is False
    assert p.unavailable_reason
    assert p.read() == {}
