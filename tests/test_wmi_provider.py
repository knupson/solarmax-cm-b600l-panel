"""WmiProvider: espacio en disco por volumen, uptime, procesos.

Se testea con un ejecutor de CIM falso, sin tocar WMI: lo que importa es el
mapeo a ids de metrica, el catalogo de nombres amigables y la cache -- no
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
    """Si un id que el provider sirve no valida, Registry lo rechaza en el
    constructor y se cae el arranque entero."""
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
    """El id es tecnico (vol.D.free) y la etiqueta la ve el usuario en el
    editor: tiene que decir de que disco habla, con el nombre que le puso."""
    cat = WmiProvider(cim=FakeCim()).catalog()
    assert "JUEGOS" in cat["vol.D.free"].label
    assert "D:" in cat["vol.D.free"].label
    assert "free" in cat["vol.D.free"].label.lower()
    # un volumen sin etiqueta no puede quedar con un guion suelto
    assert cat["vol.C.free"].label.startswith("C:")
    assert "Google Drive" in cat["vol.G.load"].label


def test_the_catalog_groups_by_device():
    """El editor agrupa por dispositivo, asi que el catalogo tiene que decir
    a que dispositivo pertenece cada metrica."""
    p = WmiProvider(cim=FakeCim())
    grupos = p.groups()
    assert grupos["vol.D.free"] == "Disk D: (JUEGOS)"
    assert grupos["vol.C.free"] == "Disk C:"
    assert grupos["sys.uptime"] == "Sistema"


def test_wmi_is_not_queried_on_every_read():
    """Consultar los cuatro volumenes cuesta ~300 ms. A 1 fps eso es un tercio
    del presupuesto de cuadro para un dato que cambia cada varios minutos."""
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
