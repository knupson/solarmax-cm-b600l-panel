import time

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
    # Two stops with the same "at": the span is 0 and _sample cannot divide by
    # zero. There is no "correct interpolation" for this -- all that matters is that
    # it does not blow up and returns a valid colour.
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
    # 32x97 -> 64x200 with fit=cover: the governing axis is the height, and
    # 97 * (200/97) gives 199.99999999999997 in floating point. Truncating with
    # int() leaves the scaled image 199 px tall instead of 200, and the last row
    # shows the fill colour instead of the photo: a 1 px gap at the edge that
    # "cover" promised not to leave. Confirmed with
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
    # BackgroundSource is also built in these tests straight from a hand-made
    # model.Background, without going through schema.build() (which is what normally
    # confines src). If it is ever instantiated that way with untrusted data, it must
    # not try to open anything outside assets_dir.
    #
    # The "secret" file has to be a genuinely openable image and it has to be
    # OUTSIDE assets_dir/, otherwise the test would pass anyway without the
    # revalidacion: Image.open() fallaria por "no existe" en vez de por
    # "it is outside the directory", and both cases degrade with a warning.
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    Image.new("RGB", (10, 10), (9, 9, 9)).save(tmp_path / "secret.png")
    s = src(model.Background(type="image", src="../secret.png"), assets_dir)
    im = s.frame()
    assert im.size == (64, 200)
    assert im.getpixel((0, 0)) != (9, 9, 9)            # the outside file was never opened
    assert any("invalid" in w for w in s.warnings)


def test_video_without_ffmpeg_degrades_and_says_how_to_fix_it(monkeypatch):
    """Video needs ffmpeg, which is external and may be absent. A shared profile
    using video has to keep opening on that machine anyway: a flat colour, and the
    warning carrying the install command -- not an exception, and not a warning that
    just says "ffmpeg" and leaves the user where they were."""
    from vmaxpanel.render import video
    monkeypatch.setattr(video, "buscar_ffmpeg", lambda: None)
    s = src(model.Background(type="video", src="x.mp4", color="#0A0B0C"))
    assert s.frame().getpixel((0, 0)) == (10, 11, 12)
    # The warning is set by the reader thread, so this waits on a deadline rather
    # than a fixed sleep: a sleep one notch too short makes the test flaky and one
    # notch too long makes it slow for nothing.
    limite = time.monotonic() + 5.0
    while time.monotonic() < limite and not s.warnings:
        s.frame()
    try:
        assert any(video.COMO_INSTALAR in w for w in s.warnings), s.warnings
    finally:
        s.close()


def test_frame_is_cached_and_returns_a_copy():
    s = src(model.Background(type="solid", color="#101010"))
    a, b = s.frame(), s.frame()
    assert a is not b                                  # mutating one does not dirty the cache
    # tobytes() and not getdata(): getdata() has been deprecated since Pillow 12 and
    # goes away in 14, and its DeprecationWarning was the only dirty output in the
    # corrida de tests.
    assert a.tobytes() == b.tobytes()


def test_repeated_frame_calls_do_not_multiply_warnings(tmp_path):
    s = src(model.Background(type="image", src="no-existe.png"), tmp_path)
    s.frame()
    s.frame()
    s.frame()
    assert len(s.warnings) == 1


def test_animated_is_false_in_phase_one():
    assert src(model.Background(type="solid")).animated is False
