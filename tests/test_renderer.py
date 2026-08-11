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


def test_set_layout_forgets_stale_missing_font_warnings():
    # El editor de fase 3 mantiene un Renderer de larga vida y llama
    # set_layout() en cada edicion. Una familia ausente en el layout VIEJO
    # no puede seguir apareciendo en warnings() una vez que el layout nuevo
    # ni siquiera la nombra -- si sobreviviera, el diagnostico del editor
    # estaria mintiendo sobre el layout que esta activo ahora.
    r = Renderer(layout(fonts={"mono-14": {"family": "NoExiste", "size": 14},
                               "mono-bold-60": {"family": "NoExiste", "size": 60}}))
    assert any("NoExiste" in w for w in r.warnings())

    r.set_layout(layout())  # MINIMAL, con Consolas: no nombra "NoExiste"
    assert not any("NoExiste" in w for w in r.warnings())


def test_set_layout_keeps_a_still_missing_font_warning_when_reapplied():
    # El caso realista del editor de fase 3: llama set_layout() en CADA
    # edicion, incluso cuando el cambio no toca las fuentes. Si "NoExiste"
    # sigue sin existir, el segundo set_layout() (con el MISMO layout) no
    # puede hacer desaparecer la advertencia -- ese era exactamente el
    # defecto que quedo tras la primera ronda de este fix: is_available()
    # se recalcula del indice cada vez que se pide warnings(), asi que no
    # depende de que resolve() haya vuelto a anotar nada en un cache miss.
    lay = layout(fonts={"mono-14": {"family": "NoExiste", "size": 14},
                        "mono-bold-60": {"family": "NoExiste", "size": 60}})
    r = Renderer(lay)
    assert any("NoExiste" in w for w in r.warnings())

    r.set_layout(lay)  # el mismo layout, "NoExiste" sigue sin existir
    assert any("NoExiste" in w for w in r.warnings())


def test_warnings_reports_a_font_alias_unused_by_any_widget():
    # Layout.fonts es la declaracion real: un alias de fuente que ningun
    # widget referencia todavia (p.ej. una fuente que el usuario acaba de
    # elegir en el editor para un widget que va a agregar despues) tiene
    # que aparecer en warnings() si su familia no existe, aunque resolve()
    # nunca se haya llamado para ella desde el dibujo de ningun widget.
    raw = dict(MINIMAL)
    raw["fonts"] = dict(MINIMAL["fonts"])
    raw["fonts"]["unused"] = {"family": "NoExiste", "size": 10}
    r = Renderer(schema.build(raw))
    assert any("NoExiste" in w for w in r.warnings())


def test_warnings_surfaces_background_phase2_notice_before_any_frame():
    # Mismo principio que las fuentes: BackgroundSource solo agrega sus
    # warnings la primera vez que se construye el fondo (_build(), llamado
    # de adentro de frame()). set_layout() ahora fuerza ese build de una
    # vez, asi que warnings() tiene que ver el aviso de "fase 2" de un
    # fondo sequence/video/procedural SIN haber llamado frame() todavia.
    r = Renderer(layout(background={"type": "sequence", "src": "x.mp4"}))
    assert any("fase 2" in w for w in r.warnings())


def _full_bleed_layout(dw, dh, color="#3987E5", bg="#0F1218"):
    """Layout minimo con un solo widget 'bar' que cubre TODO designed_for.
    _draw_bar dibuja su rectangulo 'track' sin condicion (el valor solo
    afecta el relleno de progreso encima), asi que con w.track == w.fill un
    color uniforme marca exactamente donde cae el contenido escalado, sin
    que texto ni bordes redondeados compliquen la lectura de pixeles.
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
    # scale == 1.0 (panel_size None) toma el camino rapido: dibuja los
    # widgets directo sobre la copia del fondo, sin la capa RGBA
    # intermedia que usa el caso con letterbox. Ambos caminos tienen que
    # producir el mismo frame -- si no coincidieran, el editor de fase 3
    # (que puede terminar en cualquiera de los dos segun el tamano de
    # panel que este probando) mostraria una preview distinta de lo que el
    # servicio manda al hardware. Se fuerza el camino lento a mano en vez
    # de confiar en que algun panel_size lo dispare, para probar los dos
    # caminos de verdad y no solo el que sale por default.
    fast = Renderer(layout())
    assert fast._exact_fit is True
    slow = Renderer(layout())
    slow._exact_fit = False

    a, b = fast.frame(SAMPLE), slow.frame(SAMPLE)
    assert a.size == b.size == (320, 1480)
    assert a.tobytes() == b.tobytes()


def test_centering_places_content_symmetrically_away_from_both_margins():
    # designed_for 100x200, panel 100x100: la altura manda la escala
    # (0.5), el contenido escalado mide 50px de ancho contra un lienzo de
    # 100 -- 50px de sobrante repartidos 25/25. Si el offset no se
    # calculara (el bug del brief original), el bloque ocuparia las
    # columnas 0..49 y no 25..74: se verifica el margen IZQUIERDO
    # (ausente en ese bug) y no solo que "algo" este centrado.
    lay = _full_bleed_layout(100, 200)
    r = Renderer(lay, panel_size=model.Size(100, 100))
    assert r.scale == 0.5
    assert r._content_size == (50, 100)
    assert r._offset == (25, 0)

    im = r.frame({})
    bg, fg = (15, 18, 24), (57, 135, 229)          # #0F1218, #3987E5
    assert im.getpixel((0, 50)) == bg              # margen izquierdo
    assert im.getpixel((24, 50)) == bg             # ultima columna aun sin contenido
    assert im.getpixel((25, 50)) == fg             # arranca el contenido
    assert im.getpixel((74, 50)) == fg             # ultima columna de contenido
    assert im.getpixel((75, 50)) == bg             # margen derecho
    assert im.getpixel((99, 50)) == bg


def test_centering_floor_divides_an_odd_leftover_pixel():
    # Mismo layout, pero panel 101x100: el sobrante horizontal es 51px
    # (impar), asi que no se puede repartir igual de los dos lados.
    # (target.width - content.width) // 2 -- floor, no round -- tiene que
    # dar 25 a la izquierda y dejar el pixel suelto a la derecha (26), no
    # partir la diferencia con un redondeo que numeros pares no distinguen.
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
