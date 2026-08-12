from vmaxpanel import metrics
from vmaxpanel.metrics import METRICS, UNAVAILABLE, MetricSpec, disk_metric, is_metric


def test_unavailable_is_falsy_and_singleton():
    assert not UNAVAILABLE
    assert UNAVAILABLE is type(UNAVAILABLE)()
    assert repr(UNAVAILABLE) == "UNAVAILABLE"


def test_core_metrics_are_registered():
    for mid in ("cpu.load", "cpu.temp", "gpu.load", "mem.used", "net.down",
                "clock.time", "cpu.name"):
        assert mid in METRICS, mid


def test_metric_spec_shape():
    spec = METRICS["cpu.load"]
    assert isinstance(spec, MetricSpec)
    assert spec.unit == "%"
    assert spec.kind == "number"
    assert (spec.min, spec.max) == (0.0, 100.0)


def test_name_metrics_are_text_kind():
    assert METRICS["cpu.name"].kind == "text"
    assert METRICS["clock.time"].kind == "text"


def test_disk_metrics_are_positional():
    assert disk_metric(0) == "disk.temp.0"
    assert is_metric("disk.temp.0")
    assert is_metric("disk.temp.7")
    assert not is_metric("disk.temp.x")
    assert not is_metric("cpu.powr")


def test_is_metric_rejects_a_non_string_instead_of_raising():
    """schema.validate() le pasa lo que haya en el JSON. Con un entero,
    _DISK_RE.match tiraba TypeError y validate() reventaba en vez de
    devolver la lista de errores: el arranque moria con traceback y un
    hot-reload se llevaba el loop de render puesto."""
    for bad in (123, None, True, ["cpu.load"], {"a": 1}):
        assert metrics.is_metric(bad) is False
        assert metrics.spec_for(bad) is None
