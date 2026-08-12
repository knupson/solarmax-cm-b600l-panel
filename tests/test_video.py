"""Fondo de video: mp4, webm, lo que ffmpeg sepa abrir.

El decoder es ffmpeg como PROCESO EXTERNO, no una dependencia de Python: el
proyecto se reparte y sumar PyAV o imageio-ffmpeg significa una rueda binaria por
plataforma. Aca alcanza con que el ejecutable exista.

Los tests no necesitan ffmpeg instalado: se inyecta un spawner falso que emite
frames RGB crudos del tamano exacto, que es todo el contrato de la tuberia.
"""
import subprocess
import sys
import time

from PIL import Image

from vmaxpanel.layout import model
from vmaxpanel.render.video import VideoSource, buscar_ffmpeg

TAM = model.Size(8, 4)          # chico: 96 bytes por frame
BYTES_POR_FRAME = 8 * 4 * 3


def spawner_falso(colores, repetir=True):
    """Devuelve un spawn() que produce frames de esos colores, en orden."""
    guion = (
        "import sys\n"
        f"colores = {colores!r}\n"
        f"repetir = {repetir!r}\n"
        "while True:\n"
        "    for c in colores:\n"
        f"        sys.stdout.buffer.write(bytes(c) * {8 * 4})\n"
        "        sys.stdout.buffer.flush()\n"
        "    if not repetir:\n"
        "        break\n"
    )

    def spawn():
        return subprocess.Popen([sys.executable, "-c", guion],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    return spawn


def esperar(pred, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_frames_arrive_and_advance():
    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawner_falso([(255, 0, 0),
                                                                (0, 255, 0)]))
    src.start()
    try:
        assert esperar(lambda: src.frame() is not None)
        vistos = set()
        fin = time.time() + 3.0
        while time.time() < fin and len(vistos) < 2:
            f = src.frame()
            if f is not None:
                vistos.add(f.getpixel((0, 0)))
            time.sleep(0.02)
        assert vistos >= {(255, 0, 0), (0, 255, 0)}, vistos
    finally:
        src.close()


def test_the_frame_is_the_requested_size():
    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawner_falso([(9, 9, 9)]))
    src.start()
    try:
        assert esperar(lambda: src.frame() is not None)
        assert src.frame().size == (8, 4)
    finally:
        src.close()


def test_a_partial_frame_is_never_shown():
    """ffmpeg escribe un stream continuo: si se toma lo que hay en el pipe sin
    esperar los W*H*3 bytes exactos, se dibuja media imagen con basura."""
    guion = ("import sys, time\n"
             "sys.stdout.buffer.write(b'\x11' * 40)\n"   # menos de un frame
             "sys.stdout.buffer.flush()\n"
             "time.sleep(0.4)\n"
             f"sys.stdout.buffer.write(b'\x11' * {BYTES_POR_FRAME - 40})\n"
             "sys.stdout.buffer.flush()\n"
             "time.sleep(5)\n")

    def spawn():
        return subprocess.Popen([sys.executable, "-c", guion],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawn)
    src.start()
    try:
        time.sleep(0.15)
        assert src.frame() is None, "mostro un frame incompleto"
        assert esperar(lambda: src.frame() is not None)
        assert src.frame().getpixel((0, 0)) == (17, 17, 17)
    finally:
        src.close()


def test_close_terminates_and_waits():
    """Mismo criterio que el sidecar: un ffmpeg huerfano sigue decodificando
    video y quemando CPU para nadie."""
    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawner_falso([(1, 2, 3)]))
    src.start()
    assert esperar(lambda: src.frame() is not None)
    proc = src._proc
    src.close()
    assert proc.poll() is not None, "ffmpeg sigue vivo despues de close()"


def test_a_missing_ffmpeg_reports_how_to_fix_it():
    def spawn():
        raise FileNotFoundError("ffmpeg")

    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawn)
    src.start()
    try:
        assert esperar(lambda: src.warnings)
        aviso = " ".join(src.warnings).lower()
        assert "ffmpeg" in aviso
        assert "winget" in aviso or "path" in aviso or "lib" in aviso
        assert src.frame() is None
    finally:
        src.close()


def test_buscar_ffmpeg_prefers_the_bundled_one(tmp_path, monkeypatch):
    """Dejar ffmpeg.exe al lado de la app tiene que alcanzar: es lo que evita
    pedirle al usuario que toque el PATH."""
    junto = tmp_path / "ffmpeg.exe"
    junto.write_bytes(b"")
    monkeypatch.setattr("vmaxpanel.render.video.LIB", tmp_path)
    assert buscar_ffmpeg() == str(junto)


def test_buscar_ffmpeg_returns_none_when_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("vmaxpanel.render.video.LIB", tmp_path)
    monkeypatch.setattr("vmaxpanel.render.video.shutil.which", lambda n: None)
    assert buscar_ffmpeg() is None


def test_a_file_ffmpeg_cannot_open_says_so_instead_of_that_it_ended(tmp_path):
    """ffmpeg contra un archivo que no existe (o que no es video) cierra stdout de
    entrada, y eso se leia como "el video termino". Manda al usuario a mirar la
    duracion del video cuando el problema es la ruta o el codec. El motivo lo dice
    ffmpeg en stderr, que hasta ahora se tiraba a la basura."""
    fuente = VideoSource(tmp_path / "no-existe.mp4", model.Size(8, 8), fps=30)
    fuente.start()
    limite = time.monotonic() + 10
    while time.monotonic() < limite and not fuente.warnings:
        time.sleep(0.05)
    fuente.close()
    assert fuente.warnings, "no aviso nada"
    aviso = " ".join(fuente.warnings)
    assert "no pudo abrir" in aviso
    assert "no-existe.mp4" in aviso
    assert "termino" not in aviso


def test_the_reason_ffmpeg_gives_is_included(tmp_path):
    """El texto de ffmpeg es lo unico que distingue "no existe" de "no es un video"
    de "falta el codec". Sin eso el aviso es generico y no lleva a ninguna parte."""
    fuente = VideoSource(tmp_path / "no-existe.mp4", model.Size(8, 8), fps=30)
    fuente.start()
    limite = time.monotonic() + 10
    while time.monotonic() < limite and not fuente.warnings:
        time.sleep(0.05)
    fuente.close()
    assert any("no-existe" in w.lower() or "no such" in w.lower()
               for w in fuente.warnings), fuente.warnings
