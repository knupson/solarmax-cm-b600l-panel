"""Fondos animados de fase 2: procedural y sequence.

El reloj se inyecta: un fondo que depende del tiempo real no se puede testear
de forma determinista, y sin determinismo estos tests serian inutiles.
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
    assert difiere(a, b), "el fondo no se movio con el tiempo"
    assert src.warnings == []


def test_scroll_loops_seamlessly():
    """Un scroll que salta al dar la vuelta se ve como un tiron cada ciclo."""
    src, reloj = fuente(type="procedural", name="scroll", stops=STOPS, speed=100.0)
    alto = TAM.height
    reloj.t = 0.0
    inicio = src.frame()
    reloj.t = (2 * alto) / 100.0          # un ciclo completo: la tira es 2x el alto
    vuelta = src.frame()
    assert not difiere(inicio, vuelta), "el ciclo no cierra donde arranco"


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
    reloj.t = 2.0                          # medio periodo: el otro extremo
    oscuro = ImageStat.Stat(src.frame()).mean
    assert abs(sum(claro) - sum(oscuro)) > 3, f"{claro} vs {oscuro}"


def test_pulse_repeats_exactly_each_period():
    src, reloj = fuente(type="procedural", name="pulse", stops=STOPS, period=4.0)
    reloj.t = 1.0
    a = src.frame()
    reloj.t = 5.0
    assert not difiere(a, src.frame())


def test_an_unknown_procedural_name_degrades_with_a_warning():
    """Un perfil compartido que use un generador que no existe tiene que
    seguir abriendo, como ya hacen los tipos de fase 2 no implementados."""
    src, _ = fuente(type="procedural", name="inventado", stops=STOPS)
    img = src.frame()
    assert img.size == (64, 200)
    assert any("inventado" in w for w in src.warnings)


# --- sequence ---

def carpeta_con_frames(tmp_path, n=3, ext="png"):
    d = tmp_path / "cuadros"
    d.mkdir()
    for i in range(n):
        # Cada cuadro de un color distinto, para poder distinguirlos.
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
    assert any("vacia" in w or "cuadro" in w for w in src.warnings)


def test_sequence_outside_the_assets_dir_is_refused(tmp_path):
    src = BackgroundSource(model.Background(type="sequence", src="../../etc"),
                           TAM, tmp_path, clock=Reloj())
    assert src.frame().size == (64, 200)
    assert any("ruta" in w for w in src.warnings)


def test_static_backgrounds_are_not_animated():
    for tipo in ("solid", "gradient", "image"):
        src, _ = fuente(type=tipo, stops=STOPS, color="#101010")
        assert src.animated is False, tipo


def test_a_frame_is_always_a_copy():
    """Quien recibe el frame le dibuja widgets encima: si fuera el cache, el
    cuadro siguiente arrancaria con la basura del anterior."""
    src, _ = fuente(type="procedural", name="scroll", stops=STOPS, speed=10.0)
    a, b = src.frame(), src.frame()
    assert a is not b
