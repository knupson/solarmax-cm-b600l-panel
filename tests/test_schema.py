import copy

from vmaxpanel.layout import model, schema

MINIMAL = {
    "version": 1,
    "name": "Test",
    "designed_for": {"width": 320, "height": 1480},
    "panel": {"rotate": 180, "brightness": 100, "fps": 1, "jpeg_quality": 82},
    "fonts": {"mono-14": {"family": "Consolas", "size": 14},
              "mono-bold-60": {"family": "Consolas", "size": 60, "bold": True}},
    "background": {"type": "solid", "color": "#0F1218"},
    "widgets": [
        {"id": "hdr", "type": "label", "text": "CPU", "x": 24, "y": 230,
         "font": "mono-14", "color": "#898781"},
        {"id": "load", "type": "text", "metric": "cpu.load", "x": 20, "y": 248,
         "font": "mono-bold-60", "color": "#FFFFFF", "format": "{:.1f}%",
         "rules": [{"when": "> 85", "color": "#FF4444"}]},
        {"id": "bar", "type": "bar", "metric": "cpu.load", "x": 24, "y": 316,
         "w": 272, "h": 16, "radius": 5, "fill": "#3987E5", "track": "#242834"},
    ],
}


def broken(**changes):
    raw = copy.deepcopy(MINIMAL)
    raw.update(changes)
    return raw


def with_widget(w):
    raw = copy.deepcopy(MINIMAL)
    raw["widgets"] = [w]
    return raw


def test_minimal_layout_is_valid():
    assert schema.validate(MINIMAL) == []


def test_build_returns_typed_model():
    lay = schema.build(MINIMAL)
    assert isinstance(lay, model.Layout)
    assert lay.designed_for == model.Size(320, 1480)
    assert lay.panel.rotate == 180
    assert lay.fonts["mono-bold-60"].bold is True
    assert isinstance(lay.widgets[0], model.LabelWidget)
    assert isinstance(lay.widgets[1], model.TextWidget)
    assert isinstance(lay.widgets[2], model.BarWidget)
    assert lay.widgets[1].rules[0] == model.Rule(">", 85.0, "#FF4444")


def test_future_version_is_rejected_clearly():
    errs = schema.validate(broken(version=schema.SUPPORTED_VERSION + 1))
    assert any("version" in e and "soportada" in e for e in errs)


def test_unknown_metric_is_named_in_the_error():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "text", "metric": "cpu.powr", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}"}))
    assert any("cpu.powr" in e for e in errs)


def test_unknown_font_alias_is_named():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "label", "text": "x", "x": 0, "y": 0,
         "font": "no-existe", "color": "#FFFFFF"}))
    assert any("no-existe" in e for e in errs)


def test_duplicate_widget_ids_are_rejected():
    raw = copy.deepcopy(MINIMAL)
    raw["widgets"][1]["id"] = "hdr"
    assert any("hdr" in e and "repetido" in e for e in schema.validate(raw))


def test_bad_colors_are_rejected():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "label", "text": "x", "x": 0, "y": 0,
         "font": "mono-14", "color": "rojo"}))
    assert any("color" in e for e in errs)


def test_rotate_must_be_a_quarter_turn():
    assert any("rotate" in e for e in schema.validate(
        broken(panel={"rotate": 45, "brightness": 100, "fps": 1, "jpeg_quality": 82})))


def test_brightness_and_quality_ranges():
    errs = schema.validate(broken(
        panel={"rotate": 0, "brightness": 400, "fps": 1, "jpeg_quality": 200}))
    assert any("brightness" in e for e in errs)
    assert any("jpeg_quality" in e for e in errs)


def test_format_must_have_exactly_one_field():
    def fmt(f):
        return schema.validate(with_widget(
            {"id": "w", "type": "text", "metric": "cpu.load", "x": 0, "y": 0,
             "font": "mono-14", "color": "#FFFFFF", "format": f}))

    assert fmt("{:.1f}%") == []
    assert any("format" in e for e in fmt("sin campos"))
    assert any("format" in e for e in fmt("{:.0f} {:.0f}"))
    assert any("format" in e for e in fmt("{load}"))


def test_rule_operator_must_be_a_comparator():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "text", "metric": "cpu.load", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}",
         "rules": [{"when": "os.system('calc')", "color": "#FF0000"}]}))
    assert any("when" in e for e in errs)


def test_asset_paths_cannot_escape_the_assets_dir():
    assert schema.safe_asset_path("logos/mio.png") == "logos/mio.png"
    assert schema.safe_asset_path("sub/../ok.png") == "ok.png"
    for bad in ("../../windows/system32/config/sam", "C:\\Windows\\win.ini",
                "/etc/passwd", "\\\\server\\share\\x.png", ".."):
        assert schema.safe_asset_path(bad) is None, bad


def test_image_widget_with_escaping_src_is_rejected():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "image", "src": "..\\..\\secreto.png",
         "x": 0, "y": 0, "w": 32, "h": 32}))
    assert any("src" in e for e in errs)


def test_unknown_widget_type_is_rejected():
    errs = schema.validate(with_widget({"id": "w", "type": "hologram", "x": 0, "y": 0}))
    assert any("hologram" in e for e in errs)


def test_missing_required_field_is_named():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "bar", "metric": "cpu.load", "x": 0, "y": 0}))
    assert any("w" in e for e in errs) and any("h" in e for e in errs)


def test_background_types_are_checked():
    assert schema.validate(broken(background={"type": "plasma"}))
    assert schema.validate(broken(
        background={"type": "gradient",
                    "stops": [{"at": 0.0, "color": "#000000"},
                              {"at": 1.0, "color": "#101418"}],
                    "angle": 90})) == []
    assert any("src" in e for e in schema.validate(
        broken(background={"type": "image", "src": "../fuera.png"})))


def test_disk_metric_by_index_is_accepted():
    assert schema.validate(with_widget(
        {"id": "w", "type": "text", "metric": "disk.temp.2", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "format": "{:.0f}"})) == []


def test_errors_accumulate_instead_of_stopping_at_the_first():
    raw = broken(version=99, background={"type": "plasma"})
    raw["widgets"] = [{"id": "w", "type": "hologram", "x": 0, "y": 0}]
    assert len(schema.validate(raw)) >= 3
