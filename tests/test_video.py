"""Video backgrounds: mp4, webm, whatever ffmpeg can open.

The decoder is ffmpeg as an EXTERNAL PROCESS, not a Python dependency: the project
gets shared and adding PyAV or imageio-ffmpeg means a binary wheel per platform.
Here it is enough that the executable exists.

The tests do not need ffmpeg installed: a fake spawner is injected that emits raw
RGB frames at the exact size, which is the whole contract of the pipe.
"""
import subprocess
import sys
import time

import pytest
from PIL import Image

from vmaxpanel.layout import model
from vmaxpanel.render.video import VideoSource, buscar_ffmpeg

TAM = model.Size(8, 4)          # chico: 96 bytes por frame
BYTES_POR_FRAME = 8 * 4 * 3


def spawner_falso(colores, repetir=True):
    """Returns a spawn() producing frames of those colours, in order."""
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


def test_the_first_frame_call_waits_for_the_decoder():
    # --save and --once draw ONE frame and exit. If frame() returns None because
    # ffmpeg has not emitted anything yet, the background falls back to the flat
    # colour and the whole feature is invisible right where it is previewed.
    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawner_falso([(255, 0, 0)]))
    src.start()
    try:
        img = src.frame()
        assert img is not None, "the first frame() returned None"
        assert img.getpixel((0, 0)) == (255, 0, 0)
    finally:
        src.close()


def test_frame_waits_only_once_when_no_frame_ever_arrives():
    # The other side: if the decoder never produces anything (an ffmpeg that does
    # not open the file), waiting on EVERY call would hang the panel loop forever.
    # It waits once and never again.
    def spawn_mudo():
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    src = VideoSource("x.mp4", TAM, fps=30, spawn=spawn_mudo)
    src.espera_primero = 0.3
    src.start()
    try:
        t0 = time.time()
        assert src.frame() is None
        primera = time.time() - t0
        t1 = time.time()
        for _ in range(5):
            assert src.frame() is None
        siguientes = time.time() - t1
        assert primera >= 0.25, "the first call did not wait"
        assert siguientes < 0.1, "frame() still waits after the first time"
    finally:
        src.close()


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
    """ffmpeg writes a continuous stream: taking whatever is in the pipe without
    waiting for the exact W*H*3 bytes draws half an image plus garbage."""
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
    # With no first-frame wait: what is tested here is WHAT gets published, not how
    # long the caller waits. With the wait in place, the first call would sit there
    # until the complete frame arrived and the intermediate state -- which is
    # precisely the one that has to give None -- could not be observed.
    src.espera_primero = 0
    src.start()
    try:
        time.sleep(0.15)
        assert src.frame() is None, "mostro un frame incompleto"
        assert esperar(lambda: src.frame() is not None)
        assert src.frame().getpixel((0, 0)) == (17, 17, 17)
    finally:
        src.close()


def test_close_terminates_and_waits():
    """Same rule as the sidecar: an orphan ffmpeg keeps decoding video and burning
    CPU for nobody."""
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
    """Dropping ffmpeg.exe beside the app has to be enough: that is what avoids
    asking the user to touch PATH."""
    junto = tmp_path / "ffmpeg.exe"
    junto.write_bytes(b"")
    monkeypatch.setattr("vmaxpanel.render.video.LIB", tmp_path)
    assert buscar_ffmpeg() == str(junto)


def test_buscar_ffmpeg_returns_none_when_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("vmaxpanel.render.video.LIB", tmp_path)
    monkeypatch.setattr("vmaxpanel.render.video.shutil.which", lambda n: None)
    assert buscar_ffmpeg() is None


# The next two tests need ffmpeg FOR REAL: what they check is that the reason
# ffmpeg writes to stderr reaches the warning. Without ffmpeg the correct warning is
# a different one -- "ffmpeg is missing" -- and asserting on the first would be
# asserting about one particular machine. CI caught them, running on a Windows
# without ffmpeg.
necesita_ffmpeg = pytest.mark.skipif(
    buscar_ffmpeg() is None,
    reason="needs ffmpeg installed: they check the text ffmpeg writes to stderr")


@necesita_ffmpeg
def test_a_file_ffmpeg_cannot_open_says_so_instead_of_that_it_ended(tmp_path):
    """ffmpeg against a file that does not exist (or is not a video) closes stdout
    straight away, and that read as "the video ended". It sends the user to check
    the video's duration when the problem is the path or the codec. ffmpeg states
    the reason on stderr, which used to be thrown away."""
    fuente = VideoSource(tmp_path / "no-existe.mp4", model.Size(8, 8), fps=30)
    fuente.start()
    limite = time.monotonic() + 10
    while time.monotonic() < limite and not fuente.warnings:
        time.sleep(0.05)
    fuente.close()
    assert fuente.warnings, "it warned about nothing"
    aviso = " ".join(fuente.warnings)
    assert "could not open" in aviso
    assert "no-existe.mp4" in aviso
    assert "termino" not in aviso


@necesita_ffmpeg
def test_the_reason_ffmpeg_gives_is_included(tmp_path):
    """ffmpeg's text is the only thing that distinguishes "does not exist" from "not
    a video" from "codec missing". Without it the warning is generic and leads
    nowhere."""
    fuente = VideoSource(tmp_path / "no-existe.mp4", model.Size(8, 8), fps=30)
    fuente.start()
    limite = time.monotonic() + 10
    while time.monotonic() < limite and not fuente.warnings:
        time.sleep(0.05)
    fuente.close()
    assert any("no-existe" in w.lower() or "no such" in w.lower()
               for w in fuente.warnings), fuente.warnings


def test_reading_the_reason_does_not_hang_on_a_live_process(tmp_path):
    """`read()` on a LIVE process's stderr blocks until that process closes it, and
    this runs on the reader thread: an ffmpeg that closes stdout and stays alive
    would leave the thread hanging. It is simulated with a process that closes
    stdout immediately and stays alive: the warning has to arrive anyway, without a
    reason."""
    def spawn():
        return subprocess.Popen(
            [sys.executable, "-c",
             # os.close(1) and not sys.stdout.close(): closing the Python object does
             # not always close the descriptor, so the parent would not see the EOF
             # and the test would exercise nothing.
             "import os, time; os.close(1); time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    fuente = VideoSource(tmp_path / "x.mp4", model.Size(8, 8), fps=30, spawn=spawn)
    fuente.start()
    limite = time.monotonic() + 10
    while time.monotonic() < limite and not fuente.warnings:
        time.sleep(0.05)
    try:
        assert fuente.warnings, "the reader thread hung reading stderr"
    finally:
        fuente.close()
    assert fuente._thread is not None and not fuente._thread.is_alive()
