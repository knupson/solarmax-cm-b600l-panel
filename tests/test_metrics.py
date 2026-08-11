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
