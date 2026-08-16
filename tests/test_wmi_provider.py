"""WmiProvider: espacio en disco por volumen, uptime, procesos.

Tested with a fake CIM runner, without touching WMI: what matters is the mapping
to metric ids, the catalogue of friendly names and the cache -- not
PowerShell.
"""
import threading
import time

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
    """Querying the volumes costs ~550 ms measured. At 1 fps that is over half the
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


class CimQueSeCuelga:
    """First call answers at once; every later one blocks until it is released.

    It is the shape of the real failure: the query is fine until the disk is
    saturated, and then it takes seconds.
    """

    def __init__(self):
        self.llamadas = 0
        self.entro = threading.Event()
        self.soltar = threading.Event()

    def __call__(self):
        self.llamadas += 1
        if self.llamadas > 1:
            self.entro.set()
            if not self.soltar.wait(10):
                raise TimeoutError("nadie solto la consulta de fondo")
        return {"volumenes": VOLUMENES, "uptime": 32040.0, "procesos": 193}


def test_a_stale_cache_is_refreshed_off_the_calling_thread():
    """The engine calls read() from inside _render_once(). A query that blocks
    there stops frames from going out, and the panel resets itself after ~2-3 s
    without data -- which is what made it restart over and over while the machine
    was extracting a big archive. read() must come back at once and let the
    refresh happen somewhere else.
    """
    cim = CimQueSeCuelga()
    p = WmiProvider(cim=cim, ttl=0.0)        # everything is stale immediately
    assert p.read()["vol.C.free"] == 270.0   # the first one IS synchronous: start-up

    t0 = time.perf_counter()
    m = p.read()
    tardo = time.perf_counter() - t0

    assert cim.entro.wait(5), "el refresco de fondo nunca arranco"
    assert tardo < 0.5, (f"read() bloqueo {tardo:.2f} s: la consulta sigue "
                         f"corriendo en el hilo del render")
    assert m["vol.C.free"] == 270.0          # meanwhile it serves the last good one
    cim.soltar.set()


def test_a_failing_refresh_keeps_the_last_good_reading():
    """One query that fails must not blank every disk on the panel: the reading it
    replaces is seconds old, not wrong."""
    estado = {"falla": False}

    def cim():
        if estado["falla"]:
            raise OSError("WMI no responde")
        return {"volumenes": VOLUMENES, "uptime": 32040.0, "procesos": 193}

    p = WmiProvider(cim=cim, ttl=0.0)
    assert p.read()["vol.C.free"] == 270.0
    estado["falla"] = True
    for _ in range(40):                      # let a background attempt run and fail
        p.read()
        if p.unavailable_reason:
            break
        time.sleep(0.05)
    assert p.unavailable_reason, "el fallo tiene que quedar dicho en algun lado"
    assert p.read()["vol.C.free"] == 270.0


def test_a_refresh_that_never_comes_back_stops_serving_the_old_reading():
    """Stale for a moment is the whole point of the background refresh. Stale
    forever is the panel showing a number that stopped being true with nobody
    finding out -- this project already has that scar (the RAM speed baked into a
    profile). Past the ceiling the metrics go unavailable and the panel draws
    dashes, which is the honest answer."""
    cim = CimQueSeCuelga()
    p = WmiProvider(cim=cim, ttl=0.0, max_stale=0.2)
    assert p.read()["vol.C.free"] == 270.0
    time.sleep(0.35)
    assert p.read() == {}
    assert p.unavailable_reason
    cim.soltar.set()
