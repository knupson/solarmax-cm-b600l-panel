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
    # "{0!r:>{1}}" reporta un solo campo de nivel superior para
    # Formatter().parse(), pero anida un segundo campo en el format_spec que
    # revienta en .format(valor) con un solo argumento posicional.
    assert any("format" in e for e in fmt("{0!r:>{1}}"))


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


def test_asset_paths_cannot_reveal_a_drive_letter_via_normalization():
    # un ".." de mas puede consumirse contra un segmento real anterior y
    # dejar una ruta absoluta de Windows recien expuesta tras normalizar.
    for bad in ("a/../C:/Windows/win.ini", "x/..\\C:\\Windows\\win.ini",
                "a/b/../../C:/x.png"):
        assert schema.safe_asset_path(bad) is None, bad


def test_image_widget_with_escaping_src_is_rejected():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "image", "src": "..\\..\\secreto.png",
         "x": 0, "y": 0, "w": 32, "h": 32}))
    assert any("src" in e for e in errs)


def test_unknown_widget_key_is_rejected():
    errs = schema.validate(with_widget(
        {"id": "w", "type": "label", "text": "x", "x": 0, "y": 0,
         "font": "mono-14", "color": "#FFFFFF", "algin": "center"}))
    assert any("algin" in e for e in errs)


def test_unknown_panel_key_is_rejected():
    errs = schema.validate(broken(panel={
        "rotate": 0, "brightness": 100, "fps": 1, "jpeg_quality": 82,
        "birghtness": 50}))
    assert any("birghtness" in e for e in errs)


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


def test_humanize_must_be_a_known_mode():
    def check(mode):
        return schema.validate(with_widget(
            {"id": "w", "type": "text", "metric": "net.down", "x": 0, "y": 0,
             "font": "mono-14", "color": "#FFFFFF", "format": "{}",
             "humanize": mode}))

    assert check("rate") == []
    assert check("bytes") == []
    assert check("none") == []
    assert any("humanize" in e for e in check("plasma"))


def test_format_with_a_suffix_is_ignored_by_humanize_and_rejected():
    """humanize reemplaza el formato entero (widgets.format_value lo aplica
    ANTES de mirar w.format), asi que un sufijo en format() nunca aparece en
    pantalla. Sin este chequeo, alguien que escriba format="{} Mbps" con
    humanize="rate" nunca ve "Mbps" en el panel y no hay ningun aviso de por
    que -- el mismo tipo de campo mintiendo en silencio que este proyecto ya
    evita en otros lados (ver _range() en widgets.py)."""
    def check(fmt):
        return schema.validate(with_widget(
            {"id": "w", "type": "text", "metric": "net.down", "x": 0, "y": 0,
             "font": "mono-14", "color": "#FFFFFF", "format": fmt,
             "humanize": "rate"}))

    assert check("{}") == []
    assert check("{0}") == []
    assert any("humanize" in e and "format" in e for e in check("{} Mbps"))


# --- rect: separadores y marcos ---

RECT = {"id": "sep", "type": "rect", "x": 24, "y": 232, "w": 272, "h": 1,
        "fill": "#242834"}


def rect(**changes):
    r = dict(RECT)
    r.update(changes)
    return with_widget(r)


def test_rect_widget_is_valid_without_metric_or_font():
    assert schema.validate(rect()) == []


def test_build_returns_a_rect_widget():
    lay = schema.build(rect(stroke="#FFFFFF", stroke_width=2, radius=4))
    w = lay.widgets[0]
    assert isinstance(w, model.RectWidget)
    assert (w.w, w.h, w.radius) == (272, 1, 4)
    assert (w.fill, w.stroke, w.stroke_width) == ("#242834", "#FFFFFF", 2)


def test_rect_defaults_have_no_stroke():
    w = schema.build(rect()).widgets[0]
    assert w.stroke is None and w.radius == 0


def test_rect_requires_w_and_h():
    r = dict(RECT)
    del r["h"]
    errs = schema.validate(with_widget(r))
    assert any("'h'" in e for e in errs)


def test_rect_without_fill_or_stroke_is_rejected():
    """Un rect sin ninguno de los dos no dibuja nada y no habria como
    notarlo mirando el panel."""
    r = dict(RECT)
    del r["fill"]
    errs = schema.validate(with_widget(r))
    assert any("fill" in e and "stroke" in e for e in errs)


def test_rect_rejects_an_invalid_stroke_color():
    errs = schema.validate(rect(stroke="rojo"))
    assert any("color invalido" in e for e in errs)


def test_rect_rejects_a_null_fill():
    errs = schema.validate(rect(fill=None, stroke="#FFFFFF"))
    assert any("color invalido" in e for e in errs)


def test_rect_rejects_a_non_integer_stroke_width():
    errs = schema.validate(rect(stroke="#FFFFFF", stroke_width=1.5))
    assert any("stroke_width" in e for e in errs)


def test_rect_rejects_an_unknown_key():
    errs = schema.validate(rect(metric="cpu.load"))
    assert any("desconocida" in e and "metric" in e for e in errs)


def test_rect_rejects_a_stroke_width_below_one():
    """El render clampea a 1 px, asi que un 0 escrito en el layout se
    dibujaria como 1 sin que nada avise de la diferencia."""
    errs = schema.validate(rect(stroke="#FFFFFF", stroke_width=0))
    assert any("stroke_width" in e for e in errs)


# --- campos que el validador dejaba pasar sin chequear el tipo ---
#
# Todos estos daban validate() == [] y despues TypeError dentro de
# Renderer.frame(). Engine.run() captura solo (OSError, PanelNotFound) --
# a proposito, ver el comentario de engine.py -- asi que el error se
# escapaba del loop y el daemon moria. Por hot-reload es peor: el layout
# malo pasa la validacion y REEMPLAZA al bueno, asi que no queda a que
# volver, en contra del invariante "un JSON roto no apaga el panel".

def bar(**changes):
    b = {"id": "b", "type": "bar", "metric": "cpu.load", "x": 24, "y": 316,
         "w": 272, "h": 16, "fill": "#3987E5", "track": "#242834"}
    b.update(changes)
    return with_widget(b)


def test_bar_rejects_a_non_numeric_min_or_max():
    assert any("min" in e for e in schema.validate(bar(min="0")))
    assert any("max" in e for e in schema.validate(bar(max=[100])))


def test_bar_rejects_a_max_that_is_not_above_min():
    """hi <= lo hace que _fraction() devuelva None y la barra quede vacia
    para siempre, sin ningun aviso."""
    assert any("max" in e for e in schema.validate(bar(min=80, max=20)))


def test_arc_rejects_a_non_numeric_angle():
    arc = {"id": "a", "type": "arc", "metric": "cpu.load", "x": 100, "y": 100,
           "r": 40, "fill": "#3987E5", "track": "#242834"}
    assert any("start_angle" in e for e in
               schema.validate(with_widget({**arc, "start_angle": "x"})))
    assert any("sweep" in e for e in
               schema.validate(with_widget({**arc, "sweep": None})))


def test_label_rejects_a_non_string_text():
    lbl = {"id": "l", "type": "label", "text": 7, "x": 24, "y": 198,
           "font": "mono-14", "color": "#FFFFFF"}
    assert any("text" in e for e in schema.validate(with_widget(lbl)))


def test_a_non_string_font_alias_is_rejected():
    """El alias solo se buscaba en la tabla de fuentes cuando ya era str."""
    lbl = {"id": "l", "type": "label", "text": "CPU", "x": 24, "y": 198,
           "font": 3, "color": "#FFFFFF"}
    assert any("font" in e for e in schema.validate(with_widget(lbl)))


def test_graph_rejects_samples_below_one():
    """series[-0:] es series[0:]: samples=0 grafica TODO el historial en vez
    de nada, y un negativo corta por el frente."""
    g = {"id": "g", "type": "graph", "metric": "cpu.load", "x": 10, "y": 10,
         "w": 200, "h": 60, "color": "#3987E5"}
    assert any("samples" in e for e in schema.validate(with_widget({**g, "samples": 0})))
    assert any("samples" in e for e in schema.validate(with_widget({**g, "samples": -5})))
    assert schema.validate(with_widget({**g, "samples": 120})) == []


def test_fps_accepts_up_to_the_panel_refresh_rate():
    """El panel refresca a 60 Hz (medido por el usuario). El tope de 30 era un
    numero mio de cuando no se sabia el refresco real."""
    def con_fps(v):
        return broken(panel={"rotate": 180, "brightness": 100, "fps": v,
                             "jpeg_quality": 82})

    assert schema.validate(con_fps(60)) == []
    assert schema.validate(con_fps(30)) == []
    assert schema.validate(con_fps(0.5)) == []
    # Por encima del refresco del panel los frames se descartan: es CPU
    # quemada al vacio, asi que se rechaza en vez de dejarlo pasar.
    assert any("fps" in e for e in schema.validate(con_fps(61)))
    assert any("fps" in e for e in schema.validate(con_fps(120)))
    assert any("fps" in e for e in schema.validate(con_fps(0)))


# --- fondos animados ---

def con_fondo(bg):
    raw = copy.deepcopy(MINIMAL)
    raw["background"] = bg
    return raw


STOPS_OK = [{"at": 0.0, "color": "#101725"}, {"at": 1.0, "color": "#141A26"}]


def test_procedural_scroll_and_pulse_are_valid():
    assert schema.validate(con_fondo({"type": "procedural", "name": "scroll",
                                      "stops": STOPS_OK, "speed": 20})) == []
    assert schema.validate(con_fondo({"type": "procedural", "name": "pulse",
                                      "stops": STOPS_OK, "period": 6})) == []


def test_procedural_needs_a_known_generator():
    errs = schema.validate(con_fondo({"type": "procedural", "name": "inventado",
                                      "stops": STOPS_OK}))
    assert any("name" in e for e in errs)


def test_procedural_needs_stops_like_a_gradient():
    """Los dos generadores parten del gradiente: sin paradas no hay nada que
    animar."""
    errs = schema.validate(con_fondo({"type": "procedural", "name": "scroll"}))
    assert any("stops" in e for e in errs)


def test_procedural_rejects_a_negative_speed_or_period():
    assert any("speed" in e for e in schema.validate(
        con_fondo({"type": "procedural", "name": "scroll", "stops": STOPS_OK,
                   "speed": -5})))
    assert any("period" in e for e in schema.validate(
        con_fondo({"type": "procedural", "name": "pulse", "stops": STOPS_OK,
                   "period": 0})))


def test_sequence_is_valid_with_a_folder_and_an_fps():
    assert schema.validate(con_fondo({"type": "sequence", "src": "fondos/lluvia",
                                      "fps": 12, "fit": "cover"})) == []


def test_sequence_rejects_an_fps_the_panel_cannot_show():
    errs = schema.validate(con_fondo({"type": "sequence", "src": "x", "fps": 120}))
    assert any("fps" in e for e in errs)


def test_sequence_rejects_a_path_outside_the_assets_dir():
    errs = schema.validate(con_fondo({"type": "sequence", "src": "../../etc"}))
    assert any("src" in e for e in errs)


# --- nombres de dispositivo reservados de Windows ---

def test_reserved_device_names_are_refused_as_asset_paths():
    """CON, NUL, COM1..COM9, LPT1.. y PRN son dispositivos, no archivos: en
    Windows `open("CON")` abre la consola y una lectura puede quedarse
    esperando para siempre. No es un escape del directorio, pero cuelga el
    hilo de render, y con las secuencias de fondo esa ruta se abre de verdad.
    """
    for nombre in ("CON", "con", "NUL", "PRN", "AUX", "COM1", "com9", "LPT1",
                   "CON.png", "fondos/NUL", "NUL.jpg", "COM1.txt"):
        assert schema.safe_asset_path(nombre) is None, nombre


def test_normal_names_that_only_start_like_a_device_are_allowed():
    """CONSOLA no es CON: un prefijo no puede descartar un nombre legitimo."""
    for nombre in ("consola.ttf", "CONSOLAS.png", "nulo.png", "com.png",
                   "auxiliar/fondo.png", "printer.png", "lpt.png"):
        assert schema.safe_asset_path(nombre) is not None, nombre


def test_panel_and_font_keys_come_from_the_model():
    """Estaban escritos a mano: agregarle un campo a PanelCfg o a Font hacia
    que el validador empezara a rechazar layouts validos hasta que alguien se
    acordara de actualizar el set. El chequeo de widgets ya se derivaba de
    __dataclass_fields__; esto lo iguala."""
    from vmaxpanel.layout.model import Font, PanelCfg
    assert schema.PANEL_KEYS == set(PanelCfg.__dataclass_fields__)
    assert schema.FONT_KEYS == set(Font.__dataclass_fields__)


def test_an_unknown_widget_type_still_reports_its_bad_coordinates():
    """El early return por tipo desconocido se saltaba el chequeo de x/y, asi
    que un widget con las dos cosas mal solo reportaba una."""
    errs = schema.validate(with_widget({"id": "raro", "type": "inventado",
                                        "x": "aca", "y": None}))
    assert any("tipo desconocido" in e for e in errs)
    assert any("x debe ser entero" in e for e in errs)
    assert any("y debe ser entero" in e for e in errs)


def test_a_missing_required_field_is_named_precisely():
    """El test original preguntaba si la letra "w" aparecia en el mensaje, que
    matchea cualquier palabra que la contenga."""
    b = {"id": "b", "type": "bar", "metric": "cpu.load", "x": 1, "y": 1, "h": 4}
    errs = schema.validate(with_widget(b))
    assert any("falta el campo obligatorio 'w'" in e for e in errs)
    assert not any("falta el campo obligatorio 'h'" in e for e in errs)

