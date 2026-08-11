import time

from vmaxpanel.metrics import METRICS
from vmaxpanel.providers.psutil_provider import PsutilProvider


def test_psutil_provider_probes_true():
    p = PsutilProvider()
    assert p.id == "psutil"
    assert p.probe() is True
    assert p.unavailable_reason is None


def test_psutil_declares_only_registered_metrics():
    p = PsutilProvider()
    assert p.metrics() <= set(METRICS)
    assert {"cpu.load", "mem.used", "net.down", "clock.time"} <= p.metrics()


def test_psutil_read_returns_declared_keys_only():
    p = PsutilProvider()
    p.read()                      # primer llamada arma la linea base de red
    time.sleep(0.2)
    sample = p.read()
    assert set(sample) == p.metrics()
    assert 0.0 <= sample["cpu.load"] <= 100.0
    assert sample["mem.used"] > 0.0
    assert sample["net.down"] >= 0.0
    assert isinstance(sample["cpu.name"], str) and sample["cpu.name"]


def test_psutil_clock_and_date_are_strings():
    sample = PsutilProvider().read()
    assert len(sample["clock.time"]) == 5 and sample["clock.time"][2] == ":"
    assert isinstance(sample["clock.date"], str)
