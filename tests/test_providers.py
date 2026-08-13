import time

from vmaxpanel.metrics import METRICS, is_metric
from vmaxpanel.providers.psutil_provider import PsutilProvider


def test_psutil_provider_probes_true():
    p = PsutilProvider()
    assert p.id == "psutil"
    assert p.probe() is True
    assert p.unavailable_reason is None


def test_psutil_declares_only_registered_metrics():
    p = PsutilProvider()
    # is_metric and not "a subset of METRICS": since there are per-device families
    # (net.<adapter>.down), a valid id has no reason to be in the flat table.
    for mid in p.metrics():
        assert is_metric(mid), mid
    assert {"cpu.load", "mem.used", "net.down", "clock.time"} <= p.metrics()


def test_psutil_read_returns_declared_keys_only():
    p = PsutilProvider()
    p.read()                      # the first call establishes the network baseline
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


def test_psutil_also_serves_the_short_cpu_name():
    """psutil is the fallback when the sidecar dies, so it has to serve the same
    metrics by name. platform.processor() on Windows returns "Intel64 Family 6
    Model 151...", which has no model in it: the short name must not end up empty
    or disappear."""
    p = PsutilProvider()
    assert "cpu.name_short" in p.metrics()
    muestra = p.read()
    assert isinstance(muestra["cpu.name_short"], str)
    assert muestra["cpu.name_short"]


def test_psutil_serves_network_rates_per_adapter():
    """net.down/net.up are the machine total. With two NICs or a VPN up, that does
    not say whose traffic it is."""
    p = PsutilProvider()
    porads = {m for m in p.metrics() if m.startswith("net.") and m.count(".") == 2}
    assert porads, "no publico ninguna metrica de red por adaptador"
    m = p.read()
    for mid in porads:
        assert mid in m
        assert m[mid] is None or m[mid] >= 0
    # and the catalogue carries the adapter's real name, not the slug
    cat = p.catalog()
    assert any("Ethernet" in c.label or "Wi" in c.label
               for mid, c in cat.items() if mid in porads), \
        f"etiquetas: {[cat[m].label for m in porads]}"
    grupos = p.groups()
    assert all(grupos[mid].startswith("Red") for mid in porads)


def test_psutil_serves_the_clock_with_seconds():
    """clock.time is HH:MM. At 30 fps the seconds field moves, so it is worth
    having: it is the cheapest signal that the panel is alive."""
    p = PsutilProvider()
    assert "clock.time_hms" in p.metrics()
    valor = p.read()["clock.time_hms"]
    assert len(valor) == 8 and valor.count(":") == 2
    hh, mm, ss = valor.split(":")
    assert all(x.isdigit() and len(x) == 2 for x in (hh, mm, ss))
