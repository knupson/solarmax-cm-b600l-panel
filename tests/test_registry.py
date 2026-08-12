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


def test_provider_recovers_after_degrading():
    class Flaky(Fake):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._calls = 0

        def read(self):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("wmi murio")
            return super().read()

    r = Registry([Flaky("gsa1", ["cpu.temp"], {"cpu.temp": 42.0})])

    first = r.read()
    assert first["cpu.temp"] is UNAVAILABLE
    assert "cpu.temp" not in r.resolution()
    assert "wmi murio" in r.unavailable()["cpu.temp"]

    second = r.read()
    assert r.resolution()["cpu.temp"] == "gsa1"
    assert "cpu.temp" not in r.unavailable()
    assert second["cpu.temp"] == 42.0


def test_unknown_metric_id_is_rejected_at_construction():
    with pytest.raises(ValueError, match="cpu.powr"):
        Registry([Fake("psutil", ["cpu.powr"])])


def test_close_closes_every_provider():
    f = Fake("psutil", ["cpu.load"])
    Registry([f]).close()
    assert f.closed


class TwoWayProvider(Provider):
    """Sirve las mismas metricas que otro, con prioridad distinta."""

    def __init__(self, pid, served, sample=None, fail=False):
        self.id = pid
        self._served = set(served)
        self._sample = sample or {}
        self.fail = fail

    def probe(self):
        return True

    def metrics(self):
        return set(self._served)

    def read(self):
        if self.fail:
            raise RuntimeError(f"{self.id} caido")
        return dict(self._sample)


def test_a_failing_owner_falls_back_to_a_lower_priority_provider():
    """cpu.clock y cpu.name los sirven pdh Y psutil. Cuando pdh fallaba, la
    metrica se marcaba degradada y psutil quedaba salteado por
    self._resolution.get(mid) != p.id: iba a "--" con un provider vivo al
    lado que la servia igual."""
    pdh = TwoWayProvider("pdh", {"cpu.clock"}, {"cpu.clock": 4080})
    psu = TwoWayProvider("psutil", {"cpu.clock"}, {"cpu.clock": 3200})
    r = Registry([pdh, psu])
    assert r.read()["cpu.clock"] == 4080
    assert r.resolution()["cpu.clock"] == "pdh"

    pdh.fail = True
    sample = r.read()
    assert sample["cpu.clock"] == 3200, "no hizo failover a psutil"
    assert r.resolution()["cpu.clock"] == "psutil"
    assert "cpu.clock" not in r.unavailable()


def test_the_owner_takes_the_metric_back_when_it_recovers():
    pdh = TwoWayProvider("pdh", {"cpu.clock"}, {"cpu.clock": 4080}, fail=True)
    psu = TwoWayProvider("psutil", {"cpu.clock"}, {"cpu.clock": 3200})
    r = Registry([pdh, psu])
    assert r.read()["cpu.clock"] == 3200

    pdh.fail = False
    assert r.read()["cpu.clock"] == 4080
    assert r.resolution()["cpu.clock"] == "pdh"


def test_a_metric_with_no_surviving_provider_is_unavailable_with_the_reason():
    only = TwoWayProvider("pdh", {"cpu.clock"}, {"cpu.clock": 4080})
    r = Registry([only])
    assert r.read()["cpu.clock"] == 4080
    only.fail = True
    assert r.read()["cpu.clock"] is UNAVAILABLE
    assert "pdh" in r.unavailable()["cpu.clock"]
