import io
import json
import threading
import time

import pytest

from vmaxpanel.metrics import UNAVAILABLE, is_metric
from vmaxpanel.providers.msr import MsrProvider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.providers.sidecar import SidecarClient
from vmaxpanel.providers.sidecar_providers import (CpuLhmProvider, Gsa1Provider,
                                                  LhmProvider, MoboProvider,
                                                  PdhProvider, SmbiosProvider)

SAMPLE = {
    "gsa1": {"cpu.temp": 42.0, "cpu.vrm_temp": 38.0, "cpu.vcore": 1.05},
    "pdh": {"cpu.clock": 4080, "cpu.name": "12th Gen Intel Core i5-12400F"},
    "lhm": {"gpu.name": "AMD Radeon RX 6800 XT", "gpu.load": 12.0,
            "gpu.temp": 51.0, "disk.temp.0": 34.0, "disk.temp.1": 40.0},
    "caps": {"gsa1": True, "pdh": True, "lhm": True},
}


class FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))
        self.terminated = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def poll(self):
        return None


def client_for(sample, caps_override=None):
    payload = dict(sample)
    if caps_override is not None:
        payload["caps"] = caps_override
    proc = FakeProc(["arrancando el sidecar", json.dumps(payload)])
    c = SidecarClient(script="ignored.ps1", spawn=lambda: proc)
    c.start()
    assert c.wait_ready(timeout=2.0)
    return c, proc


def test_client_ignores_non_json_lines_and_parses_the_sample():
    c, _ = client_for(SAMPLE)
    assert c.namespace("gsa1")["cpu.temp"] == 42.0
    assert c.caps() == {"gsa1": True, "pdh": True, "lhm": True}


def test_namespace_missing_returns_empty_dict():
    c, _ = client_for({"caps": {"gsa1": False}})
    assert c.namespace("lhm") == {}


def test_close_terminates_the_process():
    c, proc = client_for(SAMPLE)
    c.close()
    assert proc.terminated


def test_providers_serve_their_own_namespace_only():
    c, _ = client_for(SAMPLE)
    assert Gsa1Provider(c).metrics() == {"cpu.temp", "cpu.vrm_temp", "cpu.vcore"}
    assert PdhProvider(c).metrics() == {"cpu.clock", "cpu.name", "cpu.name_short"}
    lhm = LhmProvider(c).metrics()
    assert {"gpu.load", "gpu.temp", "disk.temp.0", "disk.temp.1"} <= lhm
    assert "cpu.temp" not in lhm


def test_lhm_disk_metrics_are_discovered_from_the_sample():
    c, _ = client_for(SAMPLE)
    assert LhmProvider(c).read()["disk.temp.1"] == 40.0


def test_probe_follows_caps():
    c, _ = client_for(SAMPLE, caps_override={"gsa1": False, "pdh": True, "lhm": True})
    gsa = Gsa1Provider(c)
    assert gsa.probe() is False
    assert "Gigabyte" in gsa.unavailable_reason
    assert PdhProvider(c).probe() is True


def test_msr_provider_is_unavailable_with_a_real_reason():
    m = MsrProvider()
    assert m.id == "msr"
    assert m.probe() is False
    assert "WinRing0" in m.unavailable_reason
    assert m.metrics() == {"cpu.power", "cpu.fan"}


def test_registry_over_sidecar_marks_unserved_metrics_unavailable():
    c, _ = client_for(SAMPLE, caps_override={"gsa1": False, "pdh": True, "lhm": True})
    r = Registry([Gsa1Provider(c), PdhProvider(c), LhmProvider(c), MsrProvider()])
    sample = r.read()
    assert sample["cpu.temp"] is UNAVAILABLE
    assert sample["cpu.power"] is UNAVAILABLE
    assert sample["cpu.clock"] == 4080
    assert "Gigabyte" in r.unavailable()["cpu.temp"]
    assert "WinRing0" in r.unavailable()["cpu.power"]


def test_stale_client_reports_not_fresh(monkeypatch):
    c, _ = client_for(SAMPLE)
    assert c.fresh
    monkeypatch.setattr("vmaxpanel.providers.sidecar.time.time", lambda: 1e12)
    assert not c.fresh


def test_a_stale_sidecar_stops_serving_instead_of_freezing_values(monkeypatch):
    """SidecarClient.fresh y STALE_AFTER existian sin ningun consumidor en
    el motor nuevo: el daemon viejo gateaba cada lectura contra eso
    (daemon/panel.py). Sin el gate, un sensors.ps1 colgado deja el panel
    pintando el ultimo cpu.temp para siempre y unavailable() no dice nada
    -- la misma mentira de estado por la que existe este proyecto."""
    c, _ = client_for(SAMPLE)
    r = Registry([Gsa1Provider(c), PdhProvider(c), LhmProvider(c)])
    assert r.read()["cpu.temp"] == 42.0

    monkeypatch.setattr("vmaxpanel.providers.sidecar.time.time", lambda: 1e12)
    sample = r.read()
    assert sample["cpu.temp"] is UNAVAILABLE
    assert sample["gpu.load"] is UNAVAILABLE
    assert "sidecar" in r.unavailable()["cpu.temp"]


def test_a_capability_lost_mid_run_stops_serving_and_recovers(monkeypatch):
    """sensors.ps1 documenta que si una fuente se cae deja de reportar
    caps=true y se recupera sola cuando vuelve. Del lado Python eso no tenia
    efecto: probe() corre una sola vez, en Registry.__init__."""
    c, _ = client_for(SAMPLE)
    r = Registry([Gsa1Provider(c), PdhProvider(c), LhmProvider(c)])
    assert r.read()["cpu.temp"] == 42.0

    caps = {"gsa1": False, "pdh": True, "lhm": True}
    monkeypatch.setattr(c, "caps", lambda: caps)
    assert r.read()["cpu.temp"] is UNAVAILABLE
    assert "Gigabyte" in r.unavailable()["cpu.temp"]

    caps["gsa1"] = True
    assert r.read()["cpu.temp"] == 42.0
    assert "cpu.temp" not in r.unavailable()


def test_smbios_provider_serves_the_memory_speed():
    c, _ = client_for({**SAMPLE, "smbios": {"mem.speed": 5600},
                       "caps": {"gsa1": True, "pdh": True, "lhm": True,
                                "smbios": True}})
    p = SmbiosProvider(c)
    assert p.metrics() == {"mem.speed"}
    assert p.probe() is True
    assert p.read()["mem.speed"] == 5600


def test_smbios_provider_is_unavailable_without_the_capability():
    c, _ = client_for(SAMPLE)          # SAMPLE no trae caps.smbios
    p = SmbiosProvider(c)
    assert p.probe() is False
    assert p.unavailable_reason


def test_close_waits_for_the_process_instead_of_only_signalling_it():
    """terminate() sin wait() vuelve enseguida: el caller que borra o mueve
    el directorio a continuacion todavia puede pegar contra el lock de
    LibreHardwareMonitorLib.dll, que es justo la trampa que el docstring del
    modulo dice que este close() evita."""
    c, proc = client_for(SAMPLE)
    c.close()
    assert proc.terminated and proc.waited


def test_close_during_a_respawn_does_not_leave_a_live_sidecar():
    """Carrera real: si close() cae entre el chequeo de _stop y el _spawn(),
    mata el proceso viejo, el thread levanta uno nuevo y sale por el return
    sin matarlo. Queda un powershell con el DLL tomado y sin dueno."""
    procs = []
    holder = {}

    def spawn():
        p = FakeProc([json.dumps(SAMPLE)])
        procs.append(p)
        if len(procs) == 1:
            holder["c"].close()          # close() justo mientras spawnea
        return p

    c = SidecarClient(script="ignored.ps1", spawn=spawn)
    holder["c"] = c
    c.start()

    deadline = time.time() + 5.0
    while time.time() < deadline and not all(p.terminated for p in procs):
        time.sleep(0.05)

    assert procs, "nunca spawneo"
    assert all(p.terminated for p in procs), \
        f"{sum(1 for p in procs if not p.terminated)} sidecar(s) vivos sin dueno"


def test_a_disk_without_a_reading_does_not_shift_the_others():
    """El sidecar emite disk.temp.N por POSICION del disco en la enumeracion,
    con null cuando esa vuelta no hubo lectura. Antes incrementaba el indice
    solo cuando habia temperatura, asi que un SSD intermitente corria el
    indice de todos los que venian despues y los tres numeros del panel
    cambiaban de significado entre muestras."""
    sample = {**SAMPLE, "lhm": {**SAMPLE["lhm"],
                                "disk.temp.0": 34.0,
                                "disk.temp.1": None,
                                "disk.temp.2": 41.0}}
    c, _ = client_for(sample)
    r = Registry([LhmProvider(c)])
    s = r.read()
    assert s["disk.temp.0"] == 34.0
    assert s["disk.temp.1"] is None          # ese disco, y solo ese, sin dato
    assert s["disk.temp.2"] == 41.0


def test_the_set_of_disk_ids_does_not_depend_on_which_disks_answered():
    """served() se descubre de la primera muestra: si un disco no aparece
    ahi, su id no vuelve a existir en toda la corrida. Por eso la clave se
    emite siempre, incluso en null."""
    sample = {**SAMPLE, "lhm": {**SAMPLE["lhm"],
                                "disk.temp.0": None,
                                "disk.temp.1": 36.0,
                                "disk.temp.2": None}}
    c, _ = client_for(sample)
    assert {"disk.temp.0", "disk.temp.1", "disk.temp.2"} <= LhmProvider(c).metrics()


def test_close_does_not_leave_the_reader_thread_sleeping_out_the_backoff(monkeypatch):
    """El respawn dormia con time.sleep, que no se entera de _stop: tras
    close() el hilo lector seguia vivo hasta 10 s (el backoff mas largo). Es
    daemon, asi que no impide salir, pero se queda con el objeto y su handle
    de mas -- y en la corrida de pytest deja hilos girando entre tests.

    Se fuerza un backoff largo: con el de produccion el primer reintento
    duerme 1 s y el test pasaria igual sin el arreglo. Y se mira EL hilo, no
    threading.active_count(), porque otros tests de este modulo dejan hilos
    dando vueltas y el contador global no prueba nada.
    """
    monkeypatch.setattr("vmaxpanel.providers.sidecar.BACKOFF", [30.0])
    procs = []

    def spawn():
        p = FakeProc([])                 # stdout vacio: cae directo al respawn
        procs.append(p)
        return p

    c = SidecarClient(script="ignored.ps1", spawn=spawn)
    c.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and not procs:
        time.sleep(0.02)
    assert procs, "nunca spawneo"
    time.sleep(0.3)                      # que entre al sleep del backoff

    t0 = time.time()
    c.close()
    assert c._thread is not None
    assert not c._thread.is_alive(),         f"el hilo sigue durmiendo el backoff {time.time() - t0:.1f}s despues de close()"


def test_pdh_provider_derives_the_short_cpu_name():
    """La regla vive en Python y no en sensors.ps1: el sidecar sigue
    emitiendo el nombre crudo y el provider deriva el corto. Asi la logica
    tiene tests y no hay que duplicarla en PowerShell."""
    c, _ = client_for({**SAMPLE, "pdh": {"cpu.clock": 4080,
                                         "cpu.name": "12th Gen Intel(R) Core(TM) i5-12400F"}})
    p = PdhProvider(c)
    assert "cpu.name_short" in p.metrics()
    muestra = p.read()
    assert muestra["cpu.name"] == "12th Gen Intel(R) Core(TM) i5-12400F"
    assert muestra["cpu.name_short"] == "Core i5-12400F"


def test_the_short_name_is_absent_when_there_is_no_name():
    c, _ = client_for({**SAMPLE, "pdh": {"cpu.clock": 4080}})
    assert PdhProvider(c).read().get("cpu.name_short") is None


# --- providers de CPU (LHM) y placa ---

MUESTRA_CPU = {"cpu.power": 11.4, "core.1.temp": 34.0, "core.1.clock": 4393,
               "core.1.load": 19.6, "core.2.temp": 37.0, "core.2.clock": 4193,
               "core.2.load": 8.0}
MUESTRA_MOBO = {"mb.temp.0": 29.0, "mb.temp.1": 34.0, "fan.1.rpm": 868,
                "fan.2.rpm": 0, "cpu.fan": 868}


def cliente_completo():
    return client_for({**SAMPLE, "cpulhm": MUESTRA_CPU, "mobo": MUESTRA_MOBO,
                       "caps": {"gsa1": True, "pdh": True, "lhm": True,
                                "cpulhm": True, "mobo": True}})[0]


def test_cpu_lhm_provider_serves_package_power_and_per_core():
    """cpu.power estaba documentado como imposible por WinRing0. LHM 0.9.3.0
    lo lee sin ningun driver: el sidecar simplemente no tenia IsCpuEnabled."""
    p = CpuLhmProvider(cliente_completo())
    assert p.probe() is True
    servidas = p.metrics()
    assert "cpu.power" in servidas
    assert {"core.1.temp", "core.1.clock", "core.1.load"} <= servidas
    m = p.read()
    assert m["cpu.power"] == 11.4
    assert m["core.1.load"] == 19.6


def test_mobo_provider_serves_fans_and_board_temps():
    p = MoboProvider(cliente_completo())
    servidas = p.metrics()
    assert {"fan.1.rpm", "fan.2.rpm", "mb.temp.0", "cpu.fan"} <= servidas
    assert p.read()["cpu.fan"] == 868


def test_every_id_these_providers_serve_is_valid():
    """Un id que no valida hace que Registry se caiga en el constructor."""
    c = cliente_completo()
    for p in (CpuLhmProvider(c), MoboProvider(c)):
        for mid in p.metrics():
            assert is_metric(mid), mid


def test_their_catalogs_use_friendly_names_and_groups():
    c = cliente_completo()
    cpu, mobo = CpuLhmProvider(c), MoboProvider(c)
    cat = {**cpu.catalog(), **mobo.catalog()}
    grupos = {**cpu.groups(), **mobo.groups()}
    assert "1" in cat["core.1.temp"].label
    assert grupos["core.1.temp"] == "Núcleos de CPU"
    assert grupos["fan.1.rpm"] == "Ventiladores"
    # el fan del CPU se identifica, no queda como "Fan 1" a secas
    assert "CPU" in cat["cpu.fan"].label.upper()
    assert grupos["mb.temp.0"] == "Placa madre"


def test_a_machine_without_these_sources_reports_why():
    c, _ = client_for(SAMPLE)          # SAMPLE no trae cpulhm ni mobo
    for p in (CpuLhmProvider(c), MoboProvider(c)):
        assert p.probe() is False
        assert p.unavailable_reason
