import io

from PIL import Image

from vmaxpanel.layout import model, schema
from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.render.renderer import History, Renderer, to_jpeg
from tests.test_schema import MINIMAL

SAMPLE = {"cpu.load": 55.5}


def layout(**over):
    raw = dict(MINIMAL)
    raw.update(over)
    return schema.build(raw)


def test_frame_has_the_designed_size_by_default():
    im = Renderer(layout()).frame(SAMPLE)
    assert im.size == (320, 1480)
    assert im.mode == "RGB"


def test_frame_scales_uniformly_to_the_real_panel():
    r = Renderer(layout(), panel_size=model.Size(640, 2960))
    assert r.scale == 2.0
    assert r.frame(SAMPLE).size == (640, 2960)


def test_scale_uses_the_smaller_axis_and_centers():
    r = Renderer(layout(), panel_size=model.Size(320, 740))
    assert r.scale == 0.5
    assert r.frame(SAMPLE).size == (320, 740)


def test_widgets_are_drawn_over_the_background():
    lay = layout(background={"type": "solid", "color": "#000000"})
    im = Renderer(lay).frame(SAMPLE)
    assert im.getbbox() is not None          # el fondo negro no cuenta como tinta


def test_unavailable_metric_renders_dashes_without_crashing():
    im = Renderer(layout()).frame({"cpu.load": UNAVAILABLE})
    assert im.size == (320, 1480)


def test_empty_sample_renders_a_full_frame():
    assert Renderer(layout()).frame({}).size == (320, 1480)


def test_set_layout_rebuilds_the_background_cache():
    r = Renderer(layout(background={"type": "solid", "color": "#FF0000"}))
    assert r.frame({}).getpixel((5, 5)) == (255, 0, 0)
    r.set_layout(layout(background={"type": "solid", "color": "#00FF00"}))
    assert r.frame({}).getpixel((5, 5)) == (0, 255, 0)


def test_warnings_surface_missing_fonts_and_assets():
    lay = layout(fonts={"mono-14": {"family": "NoExiste", "size": 14},
                        "mono-bold-60": {"family": "NoExiste", "size": 60}})
    r = Renderer(lay)
    r.frame(SAMPLE)
    assert any("NoExiste" in w for w in r.warnings())


def test_to_jpeg_produces_a_baseline_jpeg():
    data = to_jpeg(Renderer(layout()).frame(SAMPLE), rotate=0, quality=82)
    assert data[:3] == b"\xff\xd8\xff"
    assert data[-2:] == b"\xff\xd9"
    assert Image.open(io.BytesIO(data)).size == (320, 1480)


def test_to_jpeg_rotation_swaps_the_axes_for_90():
    im = Renderer(layout()).frame(SAMPLE)
    assert Image.open(io.BytesIO(to_jpeg(im, rotate=90))).size == (1480, 320)
    assert Image.open(io.BytesIO(to_jpeg(im, rotate=180))).size == (320, 1480)


def test_lower_quality_produces_fewer_bytes():
    im = Renderer(layout()).frame(SAMPLE)
    assert len(to_jpeg(im, quality=50)) < len(to_jpeg(im, quality=90))


def test_history_keeps_only_numbers_and_respects_maxlen():
    h = History(maxlen=3)
    for v in (10, 20, UNAVAILABLE, 30, None, 40):
        h.push({"cpu.load": v})
    assert h.series()["cpu.load"] == [20, 30, 40]


def test_history_ignores_text_metrics():
    h = History()
    h.push({"cpu.name": "INTEL", "cpu.load": 5.0})
    assert "cpu.name" not in h.series()
