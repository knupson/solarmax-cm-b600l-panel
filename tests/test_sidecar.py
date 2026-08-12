import io
import json

import pytest

from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.providers.msr import MsrProvider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.providers.sidecar import SidecarClient
from vmaxpanel.providers.sidecar_providers import (Gsa1Provider, LhmProvider,
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

    def terminate(self):
        self.terminated = True

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
    assert PdhProvider(c).metrics() == {"cpu.clock", "cpu.name"}
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
