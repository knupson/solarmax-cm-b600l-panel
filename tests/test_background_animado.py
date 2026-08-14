"""Fondos animados de fase 2: procedural y sequence.

The clock is injected: a background depending on real time cannot be tested
deterministically, and without determinism these tests would be useless.
"""
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from vmaxpanel.layout import model
from vmaxpanel.render.background import BackgroundSource

TAM = model.Size(64, 200)

STOPS = [{"at": 0.0, "color": "#101725"},
         {"at": 0.5, "color": "#3987E5"},
         {"at": 1.0, "color": "#141A26"}]


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def fuente(**kw):
    reloj = Reloj()
    bg = model.Background(**kw)
    return BackgroundSource(bg, TAM, Path("."), clock=reloj), reloj


def difiere(a, b) -> bool:
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


# --- procedural: scroll ---

def test_scroll_moves_with_time_and_reports_animated():
    src, reloj = fuente(type="procedural", name="scroll", stops=STOPS, speed=40.0)
    assert src.animated is True
    a = src.frame()
    reloj.t = 0.5
    b = src.frame()
    assert difiere(a, b), "the background did not move with time"
    assert src.warnings == []


def test_scroll_loops_seamlessly():
    """A scroll that jumps on wrapping around looks like a jolt every cycle."""
    src, reloj = fuente(type="procedural", name="scroll", stops=STOPS, speed=100.0)
    alto = TAM.height
    reloj.t = 0.0
    inicio = src.frame()
    reloj.t = (2 * alto) / 100.0          # a full cycle: the strip is 2x the height
    vuelta = src.frame()
    assert not difiere(inicio, vuelta), "the cycle does not close where it started"


def test_scroll_with_speed_zero_is_a_still_gradient():
    src, reloj = fuente(type="procedural", name="scroll", stops=STOPS, speed=0.0)
    a = src.frame()
    reloj.t = 10.0
    assert not difiere(a, src.frame())


# --- procedural: pulse ---

def test_pulse_changes_brightness_over_its_period():
    src, reloj = fuente(type="procedural", name="pulse", stops=STOPS, period=4.0)
    reloj.t = 0.0
    claro = ImageStat.Stat(src.frame()).mean
    reloj.t = 2.0                          # half a period: the other extreme
    oscuro = ImageStat.Stat(src.frame()).mean
    assert abs(sum(claro) - sum(oscuro)) > 3, f"{claro} vs {oscuro}"


def test_pulse_repeats_exactly_each_period():
    src, reloj = fuente(type="procedural", name="pulse", stops=STOPS, period=4.0)
    reloj.t = 1.0
    a = src.frame()
    reloj.t = 5.0
    assert not difiere(a, src.frame())


def test_an_unknown_procedural_name_degrades_with_a_warning():
    """A shared profile using a generator that does not exist has to keep
    opening."""
    src, _ = fuente(type="procedural", name="inventado", stops=STOPS)
    img = src.frame()
    assert img.size == (64, 200)
    assert any("inventado" in w for w in src.warnings)


# --- sequence ---

def carpeta_con_frames(tmp_path, n=3, ext="png"):
    d = tmp_path / "cuadros"
    d.mkdir()
    for i in range(n):
        # Each frame a different colour, so they can be told apart.
        Image.new("RGB", (32, 100), (10 + i * 60, 20, 30)).save(d / f"{i:03d}.{ext}")
    return d


def test_sequence_advances_through_the_files(tmp_path):
    src = BackgroundSource(model.Background(type="sequence", src="cuadros",
                                           fps=10.0, fit="stretch"),
                           TAM, tmp_path, clock=Reloj())
    reloj = src._clock
    carpeta_con_frames(tmp_path)
    vistos = []
    for i in range(3):
        reloj.t = i / 10.0
        vistos.append(ImageStat.Stat(src.frame()).mean[0])
    assert len(set(round(v) for v in vistos)) == 3, f"no avanzo: {vistos}"


def test_sequence_loops(tmp_path):
    carpeta_con_frames(tmp_path)
    src = BackgroundSource(model.Background(type="sequence", src="cuadros",
                                           fps=10.0, fit="stretch"),
                           TAM, tmp_path, clock=Reloj())
    reloj = src._clock
    reloj.t = 0.0
    primero = src.frame()
    reloj.t = 3 / 10.0                     # 3 cuadros: vuelve al primero
    assert not difiere(primero, src.frame())


def test_sequence_without_files_degrades_with_a_warning(tmp_path):
    (tmp_path / "vacia").mkdir()
    src = BackgroundSource(model.Background(type="sequence", src="vacia"),
                           TAM, tmp_path, clock=Reloj())
    assert src.frame().size == (64, 200)
    assert any("no frames" in w or "frame" in w for w in src.warnings)


def test_sequence_outside_the_assets_dir_is_refused(tmp_path):
    src = BackgroundSource(model.Background(type="sequence", src="../../etc"),
                           TAM, tmp_path, clock=Reloj())
    assert src.frame().size == (64, 200)
    assert any("path" in w for w in src.warnings)


def test_static_backgrounds_are_not_animated():
    for tipo in ("solid", "gradient", "image"):
        src, _ = fuente(type=tipo, stops=STOPS, color="#101010")
        assert src.animated is False, tipo


def test_a_frame_is_always_a_copy():
    """Whoever receives the frame draws widgets on top of it: if it were the cache,
    the next frame would start with the previous one's leftovers."""
    src, _ = fuente(type="procedural", name="scroll", stops=STOPS, speed=10.0)
    a, b = src.frame(), src.frame()
    assert a is not b


# --- video ---

def test_video_uses_the_video_source(tmp_path, monkeypatch):
    """The 'video' background stops degrading to a flat colour: it delegates to
    ffmpeg."""
    from PIL import Image as I
    import vmaxpanel.render.background as bg

    class FalsoVideo:
        creado = []

        def __init__(self, ruta, size, fps=30.0, **kw):
            FalsoVideo.creado.append((str(ruta), tuple(size) if not hasattr(size, "width")
                                      else (size.width, size.height), fps))
            self.warnings = []
            self.cerrado = False

        def start(self):
            return self

        def frame(self):
            return I.new("RGB", (64, 200), (7, 8, 9))

        def close(self):
            self.cerrado = True

    monkeypatch.setattr(bg, "VideoSource", FalsoVideo)
    (tmp_path / "clip.mp4").write_bytes(b"")
    src = BackgroundSource(model.Background(type="video", src="clip.mp4", fps=24),
                           TAM, tmp_path, clock=Reloj())
    assert src.animated is True
    assert src.frame().getpixel((0, 0)) == (7, 8, 9)
    assert FalsoVideo.creado[0][1] == (64, 200)      # at the panel's size
    assert FalsoVideo.creado[0][2] == 24


def test_video_falls_back_to_solid_while_there_is_no_frame(tmp_path, monkeypatch):
    """The first frame takes a while: ffmpeg has to start. Until it arrives, a flat
    colour rather than a black frame with no explanation."""
    import vmaxpanel.render.background as bg

    class SinFrames:
        def __init__(self, *a, **kw):
            self.warnings = ["ffmpeg is missing and video backgrounds need it"]

        def start(self):
            return self

        def frame(self):
            return None

        def close(self):
            pass

    monkeypatch.setattr(bg, "VideoSource", SinFrames)
    src = BackgroundSource(model.Background(type="video", src="clip.mp4",
                                           color="#123456"),
                           TAM, tmp_path, clock=Reloj())
    img = src.frame()
    assert img.size == (64, 200)
    assert img.getpixel((0, 0)) == (0x12, 0x34, 0x56)
    assert any("ffmpeg" in w for w in src.warnings)


def test_closing_the_background_closes_the_video(tmp_path, monkeypatch):
    """An orphan ffmpeg keeps decoding for nobody. The Renderer changes background
    on every set_layout, so without close() each hot reload
    dejaria un proceso mas."""
    import vmaxpanel.render.background as bg
    cerrados = []

    class Falso:
        def __init__(self, *a, **kw):
            self.warnings = []

        def start(self):
            return self

        def frame(self):
            return None

        def close(self):
            cerrados.append(True)

    monkeypatch.setattr(bg, "VideoSource", Falso)
    src = BackgroundSource(model.Background(type="video", src="clip.mp4"),
                           TAM, tmp_path, clock=Reloj())
    src.frame()
    src.close()
    assert cerrados == [True]


def test_a_static_background_close_is_harmless():
    src, _ = fuente(type="solid", color="#101010")
    src.frame()
    src.close()
