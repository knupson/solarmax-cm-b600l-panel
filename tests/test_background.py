from PIL import Image

from vmaxpanel.layout import model
from vmaxpanel.render.background import BackgroundSource

SIZE = model.Size(64, 200)


def src(bg, assets_dir="."):
    return BackgroundSource(bg, SIZE, assets_dir)


def test_solid_fills_the_whole_frame():
    im = src(model.Background(type="solid", color="#0F1218")).frame()
    assert im.size == (64, 200)
    assert im.mode == "RGB"
    assert im.getpixel((0, 0)) == im.getpixel((63, 199)) == (15, 18, 24)


def test_gradient_changes_along_the_axis():
    bg = model.Background(type="gradient", angle=90.0, stops=[
        {"at": 0.0, "color": "#000000"}, {"at": 1.0, "color": "#FFFFFF"}])
    im = src(bg).frame()
    top, bottom = im.getpixel((32, 0)), im.getpixel((32, 199))
    assert sum(top) < sum(bottom)
    assert im.getpixel((0, 100)) == im.getpixel((63, 100))   # 90 grados = vertical


def test_gradient_at_zero_degrees_is_horizontal():
    bg = model.Background(type="gradient", angle=0.0, stops=[
        {"at": 0.0, "color": "#000000"}, {"at": 1.0, "color": "#FFFFFF"}])
    im = src(bg).frame()
    assert sum(im.getpixel((0, 100))) < sum(im.getpixel((63, 100)))
    assert im.getpixel((32, 0)) == im.getpixel((32, 199))


def test_gradient_honours_intermediate_stops():
    bg = model.Background(type="gradient", angle=90.0, stops=[
        {"at": 0.0, "color": "#000000"},
        {"at": 0.5, "color": "#FF0000"},
        {"at": 1.0, "color": "#000000"}])
    im = src(bg).frame()
    assert im.getpixel((32, 100))[0] > 200
    assert im.getpixel((32, 0))[0] < 20


def test_gradient_duplicate_stops_at_the_same_at_do_not_crash():
    # dos paradas con el mismo "at": el span es 0, _sample no puede dividir
    # por cero. No hay una "interpolacion correcta" para esto -- lo unico
    # que importa es que no reviente y que devuelva un color valido.
    bg = model.Background(type="gradient", angle=90.0, stops=[
        {"at": 0.5, "color": "#FF0000"}, {"at": 0.5, "color": "#00FF00"}])
    im = src(bg).frame()
    assert im.size == (64, 200)


def test_gradient_stops_not_spanning_0_to_1_clamp_at_the_ends():
    bg = model.Background(type="gradient", angle=90.0, stops=[
        {"at": 0.2, "color": "#000000"}, {"at": 0.8, "color": "#FFFFFF"}])
    im = src(bg).frame()
    assert im.getpixel((32, 0)) == (0, 0, 0)          # antes de 0.2: clampeado
    assert im.getpixel((32, 199)) == (255, 255, 255)  # despues de 0.8: clampeado


def test_image_cover_fills_without_letterboxing(tmp_path):
    Image.new("RGB", (10, 10), (200, 40, 40)).save(tmp_path / "b.png")
    im = src(model.Background(type="image", src="b.png", fit="cover"), tmp_path).frame()
    assert im.size == (64, 200)
    assert im.getpixel((0, 0)) == (200, 40, 40)


def test_image_cover_has_no_rounding_gap_at_the_far_edge(tmp_path):
    # 32x97 -> 64x200 con fit=cover: el eje que manda es la altura, y
    # 97 * (200/97) da 199.99999999999997 en punto flotante. Truncar con
    # int() deja la imagen escalada en 199px de alto en vez de 200 y la
    # ultima fila se ve el color de relleno en vez de la foto: un hueco de
    # 1px en el borde que "cover" prometio no dejar. Confirmado con
    # aritmetica real (ver reporte); round() en vez de int() lo corrige.
    Image.new("RGB", (32, 97), (200, 40, 40)).save(tmp_path / "b.png")
    im = src(model.Background(type="image", src="b.png", fit="cover"), tmp_path).frame()
    assert im.getpixel((32, 199)) == (200, 40, 40)


def test_image_contain_letterboxes(tmp_path):
    Image.new("RGB", (10, 10), (200, 40, 40)).save(tmp_path / "b.png")
    im = src(model.Background(type="image", src="b.png", fit="contain"), tmp_path).frame()
    assert im.getpixel((32, 0)) == (0, 0, 0)          # banda arriba
    assert im.getpixel((32, 100)) == (200, 40, 40)


def test_missing_image_degrades_to_solid_with_a_warning(tmp_path):
    s = src(model.Background(type="image", src="no-existe.png"), tmp_path)
    assert s.frame().size == (64, 200)
    assert any("no-existe.png" in w for w in s.warnings)


def test_image_path_traversal_degrades_safely_instead_of_escaping(tmp_path):
    # BackgroundSource se construye tambien en estos tests directo desde un
    # model.Background a mano, sin pasar por schema.build() (que es quien
    # normalmente confina src). Si alguna vez se instancia asi con datos no
    # confiables, no debe intentar abrir nada fuera de assets_dir.
    #
    # El archivo "secreto" tiene que ser una imagen abrible de verdad y tiene
    # que estar FUERA de assets_dir/, si no la prueba pasaria igual sin la
    # revalidacion: Image.open() fallaria por "no existe" en vez de por
    # "esta fuera del directorio", y ambos casos degradan con warning.
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    Image.new("RGB", (10, 10), (9, 9, 9)).save(tmp_path / "secret.png")
    s = src(model.Background(type="image", src="../secret.png"), assets_dir)
    im = s.frame()
    assert im.size == (64, 200)
    assert im.getpixel((0, 0)) != (9, 9, 9)            # nunca se abrio el archivo de afuera
    assert any("invalida" in w for w in s.warnings)


def test_phase2_types_degrade_with_a_warning():
    for t in ("sequence", "video", "procedural"):
        s = src(model.Background(type=t, src="x.mp4"))
        assert s.frame().size == (64, 200)
        assert any("fase 2" in w for w in s.warnings), t


def test_frame_is_cached_and_returns_a_copy():
    s = src(model.Background(type="solid", color="#101010"))
    a, b = s.frame(), s.frame()
    assert a is not b                                  # mutar uno no ensucia el cache
    assert list(a.getdata()) == list(b.getdata())


def test_repeated_frame_calls_do_not_multiply_warnings(tmp_path):
    s = src(model.Background(type="image", src="no-existe.png"), tmp_path)
    s.frame()
    s.frame()
    s.frame()
    assert len(s.warnings) == 1


def test_animated_is_false_in_phase_one():
    assert src(model.Background(type="solid")).animated is False
