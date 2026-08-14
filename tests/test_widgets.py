from pathlib import Path

import pytest
from PIL import Image

from vmaxpanel.layout import model
from vmaxpanel.metrics import UNAVAILABLE
from vmaxpanel.render import widgets
from vmaxpanel.render.fonts import FontResolver

FONTS = {"m": model.Font("Consolas", 20), "big": model.Font("Consolas", 60, bold=True)}


def ctx(scale=1.0, history=None, assets_dir=None):
    layout = model.Layout(1, "t", model.Size(320, 1480), model.PanelCfg(),
                          FONTS, model.Background(), [])
    return widgets.DrawCtx(fonts=FontResolver(), layout=layout, scale=scale,
                           assets_dir=assets_dir or Path("."),
                           history=history or {})


def canvas(w=320, h=200):
    return Image.new("RGB", (w, h), (0, 0, 0))


def text_widget(**kw):
    base = dict(id="w", type="text", x=10, y=10, metric="cpu.load", font="m",
                color="#FFFFFF", format="{:.1f}%")
    base.update(kw)
    return model.TextWidget(**base)


def test_format_value_formats_numbers():
    assert widgets.format_value(text_widget(), 12.34) == "12.3%"


def test_format_value_dashes_none_and_unavailable():
    w = text_widget()
    assert widgets.format_value(w, None) == "--%"
    assert widgets.format_value(w, UNAVAILABLE) == "--%"


def test_format_value_keeps_the_suffix_outside_the_field():
    w = text_widget(format="{:.0f} MHz")
    assert widgets.format_value(w, 4080) == "4080 MHz"
    assert widgets.format_value(w, None) == "-- MHz"


def test_format_value_passes_text_metrics_through():
    w = text_widget(metric="cpu.name", format="{}")
    assert widgets.format_value(w, "INTEL CORE i5") == "INTEL CORE i5"


def test_format_value_survives_a_type_mismatch():
    w = text_widget(format="{:.1f}")
    assert widgets.format_value(w, "no numerico") == "--"


def test_color_for_applies_the_first_matching_rule():
    w = text_widget(rules=[model.Rule(">", 85.0, "#FF4444"),
                           model.Rule(">", 60.0, "#FFAA00")])
    assert widgets.color_for(w, 40.0) == "#FFFFFF"
    assert widgets.color_for(w, 70.0) == "#FFAA00"
    assert widgets.color_for(w, 90.0) == "#FF4444"


def test_color_for_ignores_rules_on_unavailable():
    w = text_widget(rules=[model.Rule(">", 85.0, "#FF4444")])
    assert widgets.color_for(w, UNAVAILABLE) == "#FFFFFF"


def test_draw_text_puts_ink_on_the_canvas():
    im = canvas()
    widgets.draw(im, text_widget(), 55.5, ctx())
    assert im.getbbox() is not None


def test_align_shifts_the_text_left():
    left, right = canvas(), canvas()
    widgets.draw(left, text_widget(x=160, align="left"), 55.5, ctx())
    widgets.draw(right, text_widget(x=160, align="right"), 55.5, ctx())
    assert right.getbbox()[0] < left.getbbox()[0]


def test_draw_label_needs_no_metric():
    im = canvas()
    widgets.draw(im, model.LabelWidget(id="l", type="label", x=5, y=5, text="CPU",
                                       font="m", color="#898781"), None, ctx())
    assert im.getbbox() is not None


def test_bar_fill_grows_with_the_value():
    def width_at(pct):
        im = canvas()
        w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                            w=272, h=16, radius=5, fill="#3987E5", track="#242834")
        widgets.draw(im, w, pct, ctx())
        px = im.load()
        return sum(1 for x in range(320) if px[x, 18] == (57, 135, 229))

    assert width_at(25) < width_at(75)


def test_bar_with_unavailable_draws_only_the_track():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=272, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, UNAVAILABLE, ctx())
    px = im.load()
    assert (57, 135, 229) not in [px[x, 18] for x in range(320)]
    assert im.getbbox() is not None          # the track is drawn


def test_bar_clamps_out_of_range_values():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 500.0, ctx())
    px = im.load()
    assert px[109, 18] == (57, 135, 229)
    assert px[200, 18] == (0, 0, 0)          # it does not overrun the width


def test_bar_uses_metric_range_when_min_max_absent():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=0, y=10, metric="cpu.vcore",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 1.0, ctx())          # cpu.vcore runs 0..2 -> half
    px = im.load()
    assert px[40, 18] == (57, 135, 229)
    assert px[80, 18] != (57, 135, 229)


def test_arc_draws_something():
    im = canvas()
    w = model.ArcWidget(id="a", type="arc", x=100, y=100, metric="cpu.load", r=40,
                        thickness=8, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 50.0, ctx())
    assert im.getbbox() is not None


def test_graph_uses_history():
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=200, h=60, color="#3987E5", samples=10)
    widgets.draw(im, w, 50.0, ctx(history={"cpu.load": [10, 30, 90, 20, 60]}))
    assert im.getbbox() is not None


def test_graph_without_history_falls_back_to_the_current_value():
    # The --save case, and the FIRST frame of any run: no sample has accumulated
    # yet, but the current value is already there and arrives as an argument.
    # Without this the box comes out empty, which is what the README's screenshots
    # showed.
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=200, h=60, color="#3987E5", samples=10)
    widgets.draw(im, w, 50.0, ctx(history={}))
    px = im.load()
    fila = [y for y in range(10, 71) if px[110, y] == (57, 135, 229)]
    assert fila, "with no history it did not draw the current value's line"
    assert 38 <= sum(fila) / len(fila) <= 42


def test_graph_with_a_single_sample_draws_a_flat_line():
    # The first frame of a run has ONE sample. That used to fall into the same case
    # as "no history" and the widget was left an empty box, which is what shows in
    # --save and in the panel's first few seconds.
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=200, h=60, color="#3987E5", samples=10)
    widgets.draw(im, w, 50.0, ctx(history={"cpu.load": [50]}))
    assert im.getbbox() is not None, "with one sample it drew nothing"
    # 50% of a 0..100 range in a 60-tall box starting at y=10: the line runs down
    # the middle, neither at the top nor at the bottom.
    px = im.load()
    fila = [y for y in range(10, 71) if px[110, y] == (57, 135, 229)]
    assert fila, "the line does not appear in the middle of the box"
    assert 38 <= sum(fila) / len(fila) <= 42


def test_graph_with_empty_history_does_not_crash():
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=200, h=60, color="#3987E5")
    widgets.draw(im, w, None, ctx(history={}))


def test_image_widget_is_skipped_when_the_asset_is_missing(tmp_path):
    im = canvas()
    w = model.ImageWidget(id="i", type="image", x=0, y=0, src="no-existe.png",
                          w=32, h=32)
    widgets.draw(im, w, None, ctx(assets_dir=tmp_path))
    assert im.getbbox() is None              # it drew nothing, and did not blow up


def test_image_widget_draws_an_existing_asset(tmp_path):
    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_path / "logo.png")
    im = canvas()
    w = model.ImageWidget(id="i", type="image", x=4, y=4, src="logo.png", w=16, h=16)
    widgets.draw(im, w, None, ctx(assets_dir=tmp_path))
    assert im.getbbox() is not None


def test_scale_moves_and_grows_a_bar():
    im = canvas(640, 400)
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 100.0, ctx(scale=2.0))
    px = im.load()
    assert px[150, 36] == (57, 135, 229)     # x*2=20, w*2=200 -> 20..220


# --- Real bugs the earlier code had ---

def test_format_value_dashes_survive_a_repr_conversion():
    """"{!r}" calls repr(value) BEFORE __format__, so a _Dash without its own
    __repr__ would leak as "<...widgets._Dash object at 0x...>" onto the panel
    instead of "--"."""
    w = text_widget(format="{!r} MHz")
    assert widgets.format_value(w, None) == "-- MHz"
    assert widgets.format_value(w, UNAVAILABLE) == "-- MHz"


def test_format_value_dashes_survive_a_str_conversion():
    w = text_widget(format="{!s} MHz")
    assert widgets.format_value(w, None) == "-- MHz"


def test_bar_never_fills_for_a_non_finite_value():
    """nan/inf no son "un numero fuera de rango", son basura de sensor. La
    earlier version computed max(0.0, min(1.0, nan_or_inf)) == 1.0 given how Python
    compares with NaN, and ended up drawing the bar full as if it were a real 100%
    reading. It has to behave like UNAVAILABLE: the track only."""
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=100, h=16, fill="#3987E5", track="#242834")
    for bad in (float("nan"), float("inf"), float("-inf")):
        widgets.draw(im, w, bad, ctx())
        px = im.load()
        assert (57, 135, 229) not in [px[x, 18] for x in range(320)], bad


def test_bar_on_an_unbounded_metric_stays_empty_without_an_explicit_max():
    """net.down declares max=None in its spec (it has no natural ceiling). An
    earlier version, resolving the range, ended up giving THAT hi a last-resort
    100.0, so any real download (thousands of B/s) clamped to 1.0 and the bar looked
    permanently full, lying exactly the way LCD Control's CpuUsage did. Without an
    explicit max on the widget, the bar cannot compute a useful fraction and must
    not draw any fill."""
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="net.down",
                        w=100, h=16, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 5_000_000.0, ctx())
    px = im.load()
    assert (57, 135, 229) not in [px[x, 18] for x in range(320)]
    assert im.getbbox() is not None          # the track is drawn


def test_bar_on_an_unbounded_metric_fills_once_the_widget_sets_a_max():
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="net.down",
                        w=100, h=16, fill="#3987E5", track="#242834",
                        max=10_000_000.0)
    widgets.draw(im, w, 5_000_000.0, ctx())  # mitad de 10 MB/s
    px = im.load()
    assert px[40, 18] == (57, 135, 229)


def test_graph_on_an_unbounded_metric_without_a_max_does_not_crash():
    """The same problem as the bar, but in the graph: _range() returning hi=None (an
    unresolved range) cannot be subtracted from lo (None - float
    revienta) si no se lo cubre explicitamente."""
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="net.down",
                          w=200, h=60, color="#3987E5", samples=10)
    widgets.draw(im, w, 500.0, ctx(history={"net.down": [100, 200, 300]}))


def test_bar_with_negative_dimensions_does_not_crash():
    """The layout validator only requires that w/h/radius be integers, not that they
    be positive. In an earlier version, a negative width or height produced a box
    with an inverted corner and Pillow raised ValueError instead of simply drawing
    nothing."""
    im = canvas()
    w = model.BarWidget(id="b", type="bar", x=10, y=10, metric="cpu.load",
                        w=-50, h=-10, radius=-5, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 50.0, ctx())


def test_arc_with_negative_radius_does_not_crash():
    im = canvas()
    w = model.ArcWidget(id="a", type="arc", x=100, y=100, metric="cpu.load", r=-40,
                        thickness=8, fill="#3987E5", track="#242834")
    widgets.draw(im, w, 50.0, ctx())


def test_graph_with_negative_dimensions_does_not_crash():
    im = canvas()
    w = model.GraphWidget(id="g", type="graph", x=10, y=10, metric="cpu.load",
                          w=-200, h=-60, color="#3987E5", samples=10)
    widgets.draw(im, w, 50.0, ctx(history={"cpu.load": [10, 30, 90]}))


# --- humanize: rate/bytes ---

def test_human_rate_scales_the_unit():
    assert widgets.human_rate(500) == "500 B/s"
    assert widgets.human_rate(2048) == "2 KB/s"
    assert widgets.human_rate(5 * 1048576) == "5.0 MB/s"


def test_humanize_rate_replaces_the_format():
    w = text_widget(metric="net.down", format="{}", humanize="rate")
    assert widgets.format_value(w, 1258291) == "1.2 MB/s"


def test_humanize_dashes_on_missing_value():
    w = text_widget(metric="net.down", format="{}", humanize="rate")
    assert widgets.format_value(w, UNAVAILABLE) == widgets.DASH


def test_humanize_bytes_uses_binary_units():
    w = text_widget(metric="mem.used", format="{}", humanize="bytes")
    assert widgets.format_value(w, 3221225472) == "3.0 GiB"


def test_unknown_humanizer_falls_back_to_format():
    w = text_widget(format="{:.0f}", humanize="inventado")
    assert widgets.format_value(w, 7.0) == "7"


# --- rect: separadores y marcos ---

def rect_widget(**kw):
    base = dict(id="r", type="rect", x=10, y=20, w=100, h=1, fill="#FFFFFF")
    base.update(kw)
    return model.RectWidget(**base)


WHITE = (255, 255, 255)
BLUE = (57, 135, 229)


def test_rect_fill_paints_exactly_w_by_h_pixels():
    """On a rect, w/h are the real size in px: a separator of h=1 measures one row,
    not two. bar/graph inherit Pillow's inclusive box (h=16 -> 17 px) and are left as
    they are; a rect cannot be, because a 2 px hairline
    ve al doble de gruesa de lo pedido."""
    im = canvas()
    widgets.draw(im, rect_widget(), None, ctx())
    px = im.load()
    assert px[10, 20] == WHITE
    assert px[109, 20] == WHITE
    assert px[110, 20] == (0, 0, 0)          # ni un pixel de mas a lo ancho
    assert px[10, 21] == (0, 0, 0)           # not one row taller


def test_rect_stroke_only_leaves_the_interior_untouched():
    im = canvas()
    widgets.draw(im, rect_widget(w=50, h=40, fill=None, stroke="#FFFFFF"),
                 None, ctx())
    px = im.load()
    assert px[10, 20] == WHITE               # borde superior izquierdo
    assert px[59, 59] == WHITE               # borde inferior derecho
    assert px[35, 40] == (0, 0, 0)           # the fill is not drawn


def test_rect_draws_fill_and_stroke_together():
    im = canvas()
    widgets.draw(im, rect_widget(w=50, h=40, fill="#3987E5", stroke="#FFFFFF"),
                 None, ctx())
    px = im.load()
    assert px[10, 20] == WHITE
    assert px[35, 40] == BLUE


def test_rect_stroke_width_thickens_the_border_inward():
    im = canvas()
    widgets.draw(im, rect_widget(w=50, h=40, fill="#3987E5", stroke="#FFFFFF",
                                 stroke_width=3), None, ctx())
    px = im.load()
    assert px[12, 22] == WHITE               # the third row of the stroke
    assert px[13, 23] == BLUE                # ya es relleno


def test_rect_scales_with_the_context():
    im = canvas(640, 400)
    widgets.draw(im, rect_widget(w=100, h=1), None, ctx(scale=2.0))
    px = im.load()
    assert px[20, 40] == WHITE
    assert px[219, 40] == WHITE
    assert px[220, 40] == (0, 0, 0)


def test_rect_hairline_survives_a_downscale():
    """At a scale < 1 an h=1 rounds to 0 px. A separator that vanishes on a panel
    smaller than designed_for is a silent regression: the layout looks different
    with nothing warning about it."""
    im = canvas()
    widgets.draw(im, rect_widget(w=100, h=1), None, ctx(scale=0.5))
    assert im.getbbox() is not None


def test_rect_with_negative_dimensions_does_not_crash():
    im = canvas()
    widgets.draw(im, rect_widget(w=-50, h=-10), None, ctx())
    assert im.getbbox() is None              # it draws nothing, and does not blow up


def test_rect_radius_larger_than_the_box_does_not_crash():
    """Pillow rejects a radius larger than half the shorter side. The validator only
    requires that radius be an integer, so the render clamps it."""
    im = canvas()
    widgets.draw(im, rect_widget(w=20, h=4, radius=50), None, ctx())
    assert im.getbbox() is not None


def test_rect_without_fill_or_stroke_draws_nothing():
    """schema.validate() rejects it, but the render does not revalidate."""
    im = canvas()
    widgets.draw(im, rect_widget(fill=None), None, ctx())
    assert im.getbbox() is None


# --- humanize: duration ---

def test_human_duration_reads_like_a_person_says_it():
    """sys.uptime is in seconds. "33098" tells nobody anything; "9h 11m" does."""
    assert widgets.human_duration(45) == "45s"
    assert widgets.human_duration(90) == "1m 30s"
    assert widgets.human_duration(3600) == "1h 0m"
    assert widgets.human_duration(33098) == "9h 11m"
    assert widgets.human_duration(90000) == "1d 1h"
    assert widgets.human_duration(0) == "0s"


def test_humanize_duration_is_available_to_a_text_widget():
    w = text_widget(metric="sys.uptime", format="{}", humanize="duration")
    assert widgets.format_value(w, 33098) == "9h 11m"
    assert widgets.format_value(w, None) == widgets.DASH
