import pytest

from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.providers.base import Provider
from vmaxpanel.providers.registry import Registry


class Fake(Provider):
    def __init__(self, pid, served, values=None, ok=True, reason=None):
        self.id = pid
        self._served = set(served)
        self._values = values or {m: 1.0 for m in served}
        self._ok = ok
        self.unavailable_reason = reason
        self.closed = False

    def probe(self):
        return self._ok

    def metrics(self):
        return self._served

    def read(self):
        return dict(self._values)

    def close(self):
        self.closed = True


def test_unprobed_provider_serves_nothing():
    r = Registry([Fake("gsa1", ["cpu.temp"], ok=False, reason="requiere placa Gigabyte")])
    assert r.resolution() == {}
    assert r.unavailable()["cpu.temp"] == "requiere placa Gigabyte"
    assert r.read()["cpu.temp"] is UNAVAILABLE


def test_priority_prefers_more_specific_provider():
    r = Registry([
        Fake("lhm", ["cpu.temp"], {"cpu.temp": 50.0}),
        Fake("gsa1", ["cpu.temp"], {"cpu.temp": 42.0}),
    ])
    assert r.resolution()["cpu.temp"] == "gsa1"
    assert r.read()["cpu.temp"] == 42.0


def test_priority_is_independent_of_construction_order():
    a = Registry([Fake("gsa1", ["cpu.temp"]), Fake("lhm", ["cpu.temp"])])
    b = Registry([Fake("lhm", ["cpu.temp"]), Fake("gsa1", ["cpu.temp"])])
    assert a.resolution() == b.resolution()


def test_metric_nobody_serves_is_unavailable_with_reason():
    r = Registry([Fake("psutil", ["cpu.load"])])
    sample = r.read()
    assert sample["cpu.load"] == 1.0
    assert "cpu.power" not in r.resolution()


def test_none_value_is_not_unavailable():
    r = Registry([Fake("gsa1", ["cpu.temp"], {"cpu.temp": None})])
    assert r.read()["cpu.temp"] is None


def test_provider_exception_degrades_without_killing_read():
    class Boom(Fake):
        def read(self):
            raise RuntimeError("wmi murio")

    r = Registry([Boom("gsa1", ["cpu.temp"]), Fake("psutil", ["cpu.load"])])
    sample = r.read()
    assert sample["cpu.temp"] is UNAVAILABLE
    assert sample["cpu.load"] == 1.0
    assert "wmi murio" in r.unavailable()["cpu.temp"]


def test_unknown_metric_id_is_rejected_at_construction():
    with pytest.raises(ValueError, match="cpu.powr"):
        Registry([Fake("psutil", ["cpu.powr"])])


def test_close_closes_every_provider():
    f = Fake("psutil", ["cpu.load"])
    Registry([f]).close()
    assert f.closed
