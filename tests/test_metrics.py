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


def test_mem_speed_is_a_known_metric():
    """Estaba horneada como label "6000" en el perfil. Una actualizacion de
    BIOS reseteo el XMP y el numero quedo mintiendo."""
    assert is_metric("mem.speed")
    spec = metrics.spec_for("mem.speed")
    assert spec.kind == "number" and spec.unit == "MT/s"


# --- cpu.name_short: el nombre de CPU sin la basura de marketing ---

def test_short_cpu_name_on_real_strings():
    """Casos reales, no inventados: lo que devuelve Win32_Processor.Name en
    distintas maquinas. El objetivo es familia + modelo, que es lo que sirve
    en un panel de 320 px de ancho."""
    casos = [
        ("12th Gen Intel(R) Core(TM) i5-12400F", "Core i5-12400F"),
        ("Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz", "Core i7-9750H"),
        ("13th Gen Intel(R) Core(TM) i9-13900K", "Core i9-13900K"),
        ("AMD Ryzen 5 5600X 6-Core Processor", "Ryzen 5 5600X"),
        ("AMD Ryzen 9 7950X3D 16-Core Processor", "Ryzen 9 7950X3D"),
        ("Intel(R) Xeon(R) E-2288G CPU @ 3.70GHz", "Xeon E-2288G"),
    ]
    for crudo, esperado in casos:
        assert metrics.short_cpu_name(crudo) == esperado, crudo


def test_short_cpu_name_leaves_an_unknown_string_usable():
    """Un nombre que no matchea ningun patron no puede quedar vacio: mejor
    el original que un hueco en el panel."""
    assert metrics.short_cpu_name("Cortex-A72") == "Cortex-A72"
    assert metrics.short_cpu_name("") == ""
    assert metrics.short_cpu_name(None) is None


def test_cpu_name_short_is_a_registered_text_metric():
    assert is_metric("cpu.name_short")
    assert metrics.spec_for("cpu.name_short").kind == "text"
