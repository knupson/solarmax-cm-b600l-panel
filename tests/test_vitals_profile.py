from pathlib import Path

import pytest
from PIL import Image, ImageChops

from vmaxpanel.layout import loader, schema
from vmaxpanel.metrics import UNAVAILABLE, is_metric
from vmaxpanel.render.renderer import Renderer, to_jpeg

PROFILE = Path("vmaxpanel/profiles/vitals.json")
GOLDEN = Path("tests/golden/vitals.png")

SAMPLE = {
    "clock.time": "14:32", "clock.date": "LUN 11 AGO",
    "cpu.name": "INTEL CORE i5-12400F", "cpu.name_short": "Core i5-12400F", "cpu.load": 55.5, "cpu.temp": 48.0,
    "cpu.clock": 4080.0, "cpu.vcore": 1.05, "cpu.vrm_temp": 41.0,
    "cpu.power": UNAVAILABLE, "cpu.fan": UNAVAILABLE,
    "gpu.name": "AMD RADEON RX 6800 XT", "gpu.load": 23.0, "gpu.temp": 51.0,
    "gpu.hotspot": 68.0, "gpu.clock": 1850.0, "gpu.power": 84.0, "gpu.vram": 37.0,
    "mem.load": 42.3, "mem.used": 13.5, "mem.total": 32.0, "mem.speed": 5600.0,
    "net.down": 1258291.0, "net.up": 40960.0,
    "disk.temp.0": 34.0, "disk.temp.1": 40.0, "disk.temp.2": 41.0,
}


def test_profile_is_valid():
    raw = __import__("json").loads(PROFILE.read_text(encoding="utf-8"))
    assert schema.validate(raw) == []


def test_profile_only_references_known_metrics():
    lay = loader.load(PROFILE)
    for w in lay.widgets:
        mid = getattr(w, "metric", None)
        if mid:
            assert is_metric(mid), mid


def test_profile_ships_no_bundled_font_files():
    """Consolas belongs to Microsoft: the profile asks for it by family, not by
    file."""
    lay = loader.load(PROFILE)
    for f in lay.fonts.values():
        assert not f.family.lower().endswith((".ttf", ".otf"))


def test_profile_uses_no_vendor_artwork():
    """back.png is artwork from LCD Control's Vitals theme and is not
    redistributed."""
    lay = loader.load(PROFILE)
    assert lay.background.type == "gradient"
    assert lay.background.src is None


def test_frame_matches_the_golden_image():
    im = Renderer(loader.load(PROFILE)).frame(SAMPLE)
    if not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        im.save(GOLDEN)
        pytest.skip("golden generado; revisalo a ojo y volve a correr")
    diff = ImageChops.difference(im, Image.open(GOLDEN).convert("RGB"))
    bbox = diff.getbbox()
    if bbox is None:
        return
    worst = max(max(band.getextrema()) for band in diff.split())
    assert worst <= 8, f"the render changed against the golden (delta {worst})"


def test_unavailable_metrics_render_as_dashes_not_crashes():
    sample = dict(SAMPLE, cpu_temp=None)
    sample["cpu.temp"] = UNAVAILABLE
    sample["gpu.hotspot"] = UNAVAILABLE
    im = Renderer(loader.load(PROFILE)).frame(sample)
    assert im.size == (320, 1480)


def test_end_to_end_frame_fits_the_panel_protocol():
    lay = loader.load(PROFILE)
    data = to_jpeg(Renderer(lay).frame(SAMPLE), lay.panel.rotate, lay.panel.jpeg_quality)
    assert data[:3] == b"\xff\xd8\xff" and data[-2:] == b"\xff\xd9"
    assert len(data) < 200_000          # entra holgado en un write serial


def test_every_section_header_has_a_rule_above_it():
    """The separator lines used to be baked into back.png, the vendor artwork this
    profile no longer uses. Without them the sections float with no structure."""
    lay = loader.load(PROFILE)
    rules = [w for w in lay.widgets if w.type == "rect"]
    headers = [w for w in lay.widgets if w.type == "label" and w.font == "section"]
    assert len(headers) == 4                 # CPU, GPU, RAM, SYS
    assert len(rules) == len(headers)
    for h in headers:
        above = [r for r in rules if 0 < h.y - r.y <= 40]
        assert len(above) == 1, f"{h.text} has no rule above it"
        assert above[0].h == 1               # a hairline, not a band


def test_the_profile_reads_the_memory_speed_instead_of_hardcoding_it():
    """It used to be a label reading "6000". A BIOS update reset the XMP profile,
    the machine dropped to 5600 and the panel went on showing 6000. A profile shared
    with other owners of the panel cannot carry the number of
    ESTA maquina escrito a mano."""
    lay = loader.load(PROFILE)
    speed = [w for w in lay.widgets if w.id == "mem-speed"][0]
    assert speed.type == "text" and speed.metric == "mem.speed"
    hardcoded = [w for w in lay.widgets
                 if w.type == "label" and w.text.strip().isdigit()]
    assert hardcoded == []


def test_the_shipped_profile_is_not_modified_by_the_test_suite():
    """A guard against an accident that already happened: a verification script
    wrote over the LIVE profile instead of over a copy, and left it on 'procedural'
    at 30 fps. The tests work on copies in tmp_path; if this one fails, something is
    writing where it should not."""
    raw = __import__("json").loads(PROFILE.read_text(encoding="utf-8"))
    assert raw["panel"]["fps"] == 1
    assert raw["background"]["type"] == "gradient"
    assert len(raw["background"]["stops"]) == 3
