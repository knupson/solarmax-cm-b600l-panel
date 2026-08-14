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
    assert im.getbbox() is not None          # a black background does not count as ink


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


def test_set_layout_closes_the_previous_background():
    """Every hot reload goes through set_layout(), and a video background has an
    ffmpeg behind it. Without closing the previous one, editing the profile ten times
    leaves ten ffmpegs decoding for nobody -- the same orphan process this project
    already had with the sensor sidecar."""
    r = Renderer(layout())
    viejo = r._bg
    cerrados = []
    viejo.close = lambda: cerrados.append(True)
    r.set_layout(layout())
    assert cerrados == [True]
    assert r._bg is not viejo


def test_renderer_close_closes_the_background():
    r = Renderer(layout())
    cerrados = []
    r._bg.close = lambda: cerrados.append(True)
    r.close()
    assert cerrados == [True]


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


def test_set_layout_forgets_stale_missing_font_warnings():
    # El editor de fase 3 mantiene un Renderer de larga vida y llama
    # set_layout() on every edit. A family missing from the OLD layout must not keep
    # appearing in warnings() once the new layout does not even name it -- if it
    # survived, the editor's diagnostics would be lying about the layout that is
    # active now.
    r = Renderer(layout(fonts={"mono-14": {"family": "NoExiste", "size": 14},
                               "mono-bold-60": {"family": "NoExiste", "size": 60}}))
    assert any("NoExiste" in w for w in r.warnings())

    r.set_layout(layout())  # MINIMAL, with Consolas: it does not name "NoExiste"
    assert not any("NoExiste" in w for w in r.warnings())


def test_set_layout_keeps_a_still_missing_font_warning_when_reapplied():
    # The realistic editor case: it calls set_layout() on EVERY edit, even when the
    # change does not touch the fonts. If "NoExiste" still does not exist, the second
    # set_layout() (with the SAME layout) must not make the warning disappear -- that
    # was exactly the defect left after the first round of this fix. is_available()
    # is recalculated from the index every time warnings() is asked for, so it does
    # not depend on resolve() having recorded anything again on a cache miss.
    lay = layout(fonts={"mono-14": {"family": "NoExiste", "size": 14},
                        "mono-bold-60": {"family": "NoExiste", "size": 60}})
    r = Renderer(lay)
    assert any("NoExiste" in w for w in r.warnings())

    r.set_layout(lay)  # the same layout, "NoExiste" still does not exist
    assert any("NoExiste" in w for w in r.warnings())


def test_warnings_reports_a_font_alias_unused_by_any_widget():
    # Layout.fonts is the real declaration: a font alias no widget references yet
    # (say, a font the user has just picked in the editor for a widget they are
    # about to add) has to appear in warnings() if its family does not exist, even
    # though resolve() was never called for it from any widget's drawing.
    raw = dict(MINIMAL)
    raw["fonts"] = dict(MINIMAL["fonts"])
    raw["fonts"]["unused"] = {"family": "NoExiste", "size": 10}
    r = Renderer(schema.build(raw))
    assert any("NoExiste" in w for w in r.warnings())


def test_warnings_surfaces_a_degraded_background_before_any_frame():
    # Same principle as the fonts: BackgroundSource only adds its warnings the first
    # time the background is built (_build(), called from inside frame()).
    # set_layout() now forces that build up front, so warnings() has to see the
    # warning WITHOUT having called
    # frame() todavia.
    #
    # An 'image' with a file that does not exist: it is the degradation always
    # available for testing this. video/sequence/procedural are all implemented, so
    # none of them warns about anything merely by
    # existir.
    r = Renderer(layout(background={"type": "image", "src": "no-existe.png"}))
    assert any("no-existe.png" in w for w in r.warnings())


def _full_bleed_layout(dw, dh, color="#3987E5", bg="#0F1218"):
    """A minimal layout with a single 'bar' widget covering ALL of designed_for.
    _draw_bar draws its 'track' rectangle unconditionally (the value only affects the
    progress fill on top), so with w.track == w.fill a uniform colour marks exactly
    where the scaled content lands, without text or rounded corners complicating the
    pixel reading.
    """
    raw = {
        "version": 1, "name": "letterbox-test",
        "designed_for": {"width": dw, "height": dh},
        "panel": {"rotate": 0, "brightness": 100, "fps": 1, "jpeg_quality": 82},
        "fonts": {"mono-14": {"family": "Consolas", "size": 14}},
        "background": {"type": "solid", "color": bg},
        "widgets": [
            {"id": "full", "type": "bar", "metric": "cpu.load", "x": 0, "y": 0,
             "w": dw, "h": dh, "radius": 0, "fill": color, "track": color},
        ],
    }
    assert schema.validate(raw) == []
    return schema.build(raw)


def test_fast_and_slow_paths_agree_at_identity_scale():
    # scale == 1.0 (panel_size None) takes the fast path: it draws the widgets
    # straight onto the copy of the background, without the intermediate RGBA layer
    # the letterbox case uses. Both paths have to produce the same frame -- if they
    # did not match, the editor (which can end up on either depending on the panel
    # size being tried) would show a preview different from what the engine sends to
    # the hardware. The slow path is forced by hand rather than trusting some
    # panel_size to trigger it, so both paths are really exercised and not just the
    # default one.
    fast = Renderer(layout())
    assert fast._exact_fit is True
    slow = Renderer(layout())
    slow._exact_fit = False

    a, b = fast.frame(SAMPLE), slow.frame(SAMPLE)
    assert a.size == b.size == (320, 1480)
    assert a.tobytes() == b.tobytes()


def test_centering_places_content_symmetrically_away_from_both_margins():
    # designed_for 100x200, panel 100x100: the height governs the scale (0.5), the
    # scaled content measures 50 px wide against a 100 px canvas -- 50 px of slack
    # split 25/25. If the offset were not computed (the original bug), the block
    # would occupy columns 0..49 and not 25..74: the LEFT margin (absent in that bug)
    # is verified, not merely that "something" is centred.
    lay = _full_bleed_layout(100, 200)
    r = Renderer(lay, panel_size=model.Size(100, 100))
    assert r.scale == 0.5
    assert r._content_size == (50, 100)
    assert r._offset == (25, 0)

    im = r.frame({})
    bg, fg = (15, 18, 24), (57, 135, 229)          # #0F1218, #3987E5
    assert im.getpixel((0, 50)) == bg              # margen izquierdo
    assert im.getpixel((24, 50)) == bg             # last column still without content
    assert im.getpixel((25, 50)) == fg             # the content starts
    assert im.getpixel((74, 50)) == fg             # ultima columna de contenido
    assert im.getpixel((75, 50)) == bg             # margen derecho
    assert im.getpixel((99, 50)) == bg


def test_centering_floor_divides_an_odd_leftover_pixel():
    # The same layout, but a 101x100 panel: the horizontal slack is 51 px (odd), so
    # it cannot be split evenly on both sides. (target.width - content.width) // 2 --
    # floor, not round -- has to give 25 on the left and leave the spare pixel on the
    # right (26), not split the difference with a rounding that even numbers cannot
    # tell apart.
    lay = _full_bleed_layout(100, 200)
    r = Renderer(lay, panel_size=model.Size(101, 100))
    assert r._content_size == (50, 100)
    assert r._offset == (25, 0)                    # floor(51/2), no round(51/2)

    im = r.frame({})
    bg, fg = (15, 18, 24), (57, 135, 229)
    assert im.getpixel((24, 50)) == bg              # margen izquierdo: 25px (0..24)
    assert im.getpixel((25, 50)) == fg
    assert im.getpixel((74, 50)) == fg
    assert im.getpixel((75, 50)) == bg              # margen derecho: 26px (75..100)
    assert im.getpixel((100, 50)) == bg


def test_warnings_still_answer_after_close():
    """`close()` releases the background, and warnings() read it directly: a call
    after closing raised AttributeError. It matters because warnings() is what the
    tray paints when the menu opens -- which is to say it runs on a different thread
    from the one bringing the engine down, and "the panel closed exactly when you
    opened the menu" cannot be an exception. The closed background's warnings are
    kept: they are the reason it ended up this way, right when the user is about to
    read them."""
    r = Renderer(layout(background={"type": "image", "src": "no-existe.png"}))
    antes = r.warnings()
    assert any("no-existe.png" in w for w in antes)
    r.close()
    assert r.warnings() == antes


def test_warnings_say_which_family_was_used_instead():
    """Saying a bare "X is missing" makes the user guess what they are looking at.
    With the chain declared in the profile, the warning can say exactly what it was
    drawn with -- and that is the point of having the chain."""
    lay = layout(fonts={"mono-14": {"family": "NoExisteEnNingunaParte", "size": 14,
                                    "fallbacks": ["Consolas"]},
                        "mono-bold-60": {"family": "Consolas", "size": 60}})
    r = Renderer(lay)
    r.frame(SAMPLE)
    avisos = " ".join(r.warnings())
    assert "NoExisteEnNingunaParte" in avisos
    assert "Consolas" in avisos, avisos


def test_a_family_that_resolves_generates_no_warning():
    lay = layout(fonts={"mono-14": {"family": "Consolas", "size": 14,
                                    "fallbacks": ["Courier New"]},
                        "mono-bold-60": {"family": "Consolas", "size": 60}})
    r = Renderer(lay)
    r.frame(SAMPLE)
    assert not [w for w in r.warnings() if "Consolas" in w]
