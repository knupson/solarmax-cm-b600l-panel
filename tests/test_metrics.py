import pytest

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


def test_the_disk_name_is_a_text_metric_with_the_index_in_its_label():
    """The profiles bind their SSD headings to disk.name.N. Two things have to
    hold: the validator accepts the id (or the profile is rejected whole and the
    panel keeps the previous layout), and spec_for carries the index into the
    label -- without it every disk collapses into one entry in the editor's
    selector and picking one writes another, which already happened with
    disk.temp.0/1/2."""
    assert is_metric("disk.name.0")
    assert is_metric("disk.name.7")
    assert not is_metric("disk.name.x")
    assert not is_metric("disk.name.")
    spec = metrics.spec_for("disk.name.2")
    assert spec.kind == "text"          # a drive letter, not a number to format
    assert "2" in spec.label
    assert metrics.spec_for("disk.name.2").label != metrics.spec_for("disk.name.3").label
    assert metrics.group_for("disk.name.0") == "Disks"


def test_is_metric_rejects_a_non_string_instead_of_raising():
    """schema.validate() passes it whatever is in the JSON. With an integer,
    _DISK_RE.match tiraba TypeError y validate() reventaba en vez de
    returning the list of errors: start-up died with a traceback and a hot reload
    took the render loop down with it."""
    for bad in (123, None, True, ["cpu.load"], {"a": 1}):
        assert metrics.is_metric(bad) is False
        assert metrics.spec_for(bad) is None


def test_mem_speed_is_a_known_metric():
    """It used to be baked in as a label reading "6000". A BIOS update reset the XMP
    profile and the number was left lying."""
    assert is_metric("mem.speed")
    spec = metrics.spec_for("mem.speed")
    assert spec.kind == "number" and spec.unit == "MT/s"


# --- cpu.name_short: the CPU name without the marketing noise ---

def test_short_cpu_name_on_real_strings():
    """Real cases, not invented ones: what Win32_Processor.Name returns on different
    machines. The goal is family + model, which is what is useful
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
    """A name matching no pattern must not end up empty: better the original than a
    hole in the panel."""
    assert metrics.short_cpu_name("Cortex-A72") == "Cortex-A72"
    assert metrics.short_cpu_name("") == ""
    assert metrics.short_cpu_name(None) is None


def test_cpu_name_short_is_a_registered_text_metric():
    assert is_metric("cpu.name_short")
    assert metrics.spec_for("cpu.name_short").kind == "text"


# --- familias de metricas por dispositivo ---
#
# The id is technical and stable (vol.C.free); the friendly label is built by
# spec_for() and refined by the provider with the device's real name.

def test_volume_metrics_are_valid_and_have_a_friendly_label():
    assert is_metric("vol.C.free")
    assert is_metric("vol.D.load")
    spec = metrics.spec_for("vol.C.free")
    assert spec.kind == "number" and spec.unit == "GiB"
    assert "C:" in spec.label                    # the label names the volume
    carga = metrics.spec_for("vol.D.load")
    assert (carga.min, carga.max) == (0.0, 100.0)
    assert carga.unit == "%"


def test_core_metrics_are_valid_per_core():
    assert is_metric("core.0.temp")
    assert is_metric("core.11.load")
    assert is_metric("core.3.clock")
    spec = metrics.spec_for("core.3.temp")
    assert "3" in spec.label and spec.unit == "°C"


def test_fan_and_motherboard_metrics_are_valid():
    assert is_metric("fan.1.rpm")
    assert is_metric("mb.temp.2")
    assert metrics.spec_for("fan.1.rpm").unit == "RPM"
    assert metrics.spec_for("mb.temp.2").unit == "°C"


def test_network_metrics_per_adapter_accept_a_slug():
    assert is_metric("net.ethernet.down")
    assert is_metric("net.wi-fi-2.up")
    assert not is_metric("net.Ethernet 2.down")   # not with spaces and capitals
    assert metrics.spec_for("net.ethernet.down").unit == "B/s"


def test_unknown_families_are_still_rejected():
    for malo in ("vol.C.inventado", "vol..free", "core.x.temp", "fan.rpm",
                 "mb.temp", "net.eth.sideways", "vol.CC.free"):
        assert not is_metric(malo), malo
        assert metrics.spec_for(malo) is None


def test_the_slug_helper_is_reversible_enough_to_be_readable():
    """The id carries a slug of the device name because it has to fit inside a
    metric id; the pretty name is published separately by the provider."""
    assert metrics.slug("Ethernet") == "ethernet"
    assert metrics.slug("Wi-Fi 2") == "wi-fi-2"
    assert metrics.slug("Realtek PCIe GbE Family Controller") == \
        "realtek-pcie-gbe-family-controller"


def test_registered_metrics_have_unique_labels():
    """The label is what the user picks in the editor's selector: two metrics with
    the same label are indistinguishable there, and choosing one writes the other.
    mem.load and mem.used were both called "RAM used"."""
    por_etiqueta = {}
    for mid, spec in METRICS.items():
        assert spec.label not in por_etiqueta, \
            f"{mid} y {por_etiqueta.get(spec.label)} comparten {spec.label!r}"
        por_etiqueta[spec.label] = mid


def test_group_names_are_friendly_not_id_prefixes():
    """The group is read by the user too: "NET" and "MEM" are id prefixes,
    no nombres."""
    assert metrics.group_for("net.down") == "Network"
    assert metrics.group_for("mem.load") == "RAM"
    assert metrics.group_for("clock.time") == "Clock"
    assert metrics.group_for("disk.temp.1") == "Disks"
    assert metrics.group_for("vol.C.free") == "Disks"
    assert metrics.group_for("core.2.temp") == "CPU cores"
    assert metrics.group_for("fan.1.rpm") == "Fans"
    assert metrics.group_for("mb.temp.0") == "Motherboard"
    assert metrics.group_for("sys.uptime") == "System"
    assert metrics.group_for("cpu.load") == "CPU"
    assert metrics.group_for("gpu.load") == "GPU"
    # an unknown prefix must not be left without a group
    assert metrics.group_for("inventado.algo") == "INVENTADO"


def test_every_registered_metric_is_covered_by_this_file():
    """The original test covered 7 of 23 ids by hand, so renaming a metric broke no
    test and the profile found out in production. Now the coverage is the whole
    registry."""
    for mid, spec in METRICS.items():
        assert is_metric(mid), mid
        assert metrics.spec_for(mid) is spec or metrics.spec_for(mid) == spec
        assert spec.label and spec.kind in ("number", "text")
        if spec.kind == "number":
            assert spec.min is None or isinstance(spec.min, float)
            assert spec.max is None or isinstance(spec.max, float)
        assert metrics.group_for(mid) != "OTRAS"


def test_disk_metric_and_is_metric_agree():
    """disk_metric(-1) produced "disk.temp.-1", which is_metric rejects: the
    generator and the validator disagreed."""
    for n in (0, 1, 7, 99):
        assert is_metric(disk_metric(n)), n
    for malo in (-1, -5):
        with pytest.raises(ValueError):
            disk_metric(malo)


def test_every_metric_the_shipped_profiles_use_exists():
    """Worth more than a hand-written list of ids: a rename in METRICS breaks no test
    if the tests name 7 loose ids, but it DOES break the profiles that ship with the
    repo -- and that is what the user sees. It covers the shipped profiles in full,
    not a sample."""
    import json
    from pathlib import Path
    from vmaxpanel.metrics import is_metric

    perfiles = sorted(Path("vmaxpanel/profiles").glob("*.json"))
    assert perfiles, "could not find the repo's profiles"
    for p in perfiles:
        raw = json.loads(p.read_text(encoding="utf-8"))
        usadas = {w["metric"] for w in raw["widgets"] if "metric" in w}
        assert usadas, f"{p.name} no usa ninguna metrica"
        for mid in sorted(usadas):
            assert is_metric(mid), f"{p.name} uses {mid!r}, which no longer exists"


def test_every_metric_spec_is_complete():
    """A half-built spec (no label, no unit) shows up in the editor as an empty row
    and on the panel as a value with no context. The whole catalogue is checked,
    which is what the list of 7 ids did not do."""
    for mid, spec in METRICS.items():
        assert spec.label and spec.label.strip(), mid
        assert isinstance(spec.kind, str) and spec.kind, mid
        if spec.max is not None and spec.min is not None:
            assert spec.max > spec.min, mid


def test_the_motherboard_model_is_a_known_metric():
    """It was a hand-written label in Apex reading the author's own board, which
    every other machine would have displayed as its own."""
    from vmaxpanel import metrics
    from vmaxpanel.metrics import is_metric
    assert is_metric("mb.name")
    assert metrics.spec_for("mb.name").kind == "text"
