"""Video background, decoded by ffmpeg as an external process.

**Why ffmpeg and not a Python library.** This project gets shared with other
owners of the panel and the rule is to add no dependencies: PyAV or
imageio-ffmpeg mean a binary wheel per platform and Python version. A loose
executable, by contrast, is dropped beside the app and that is that -- and a great
many people already have one. The cost is that if it is missing, video does not
work; in exchange, it works with anything ffmpeg can open: mp4, webm, mkv, gif,
whatever.

**How.** One ffmpeg per background, emitting raw RGB at the panel's exact size to
stdout. A thread drains it and keeps the last complete frame. `-stream_loop -1`
makes the video repeat without Python noticing, and `-re` makes it run at its
natural pace: without that, ffmpeg decodes as fast as it can, fills the pipe and
burns a core for nothing.
"""
import shutil
import subprocess
import threading
from pathlib import Path

from PIL import Image

LIB = Path(__file__).resolve().parent.parent / "lib"
NOMBRES = ("ffmpeg.exe", "ffmpeg")

COMO_INSTALAR = ("ffmpeg is missing and video backgrounds need it: install it with "
                 "'winget install Gyan.FFmpeg', or drop ffmpeg.exe into "
                 "vmaxpanel/lib/")

# No console window: the tray runs under pythonw, and an ffmpeg launched normally
# would open a black window on every start.
SIN_VENTANA = 0x08000000


def buscar_ffmpeg():
    """The path to the executable, or None.

    Beside the app first and PATH second: dropping the .exe into vmaxpanel/lib/ has
    to be enough, because that is what avoids asking the user to touch environment
    variables.
    """
    for nombre in NOMBRES:
        candidato = LIB / nombre
        if candidato.exists():
            return str(candidato)
    for nombre in NOMBRES:
        encontrado = shutil.which(nombre)
        if encontrado:
            return encontrado
    return None


class VideoSource:
    """One ffmpeg decoding to raw RGB, and the last complete frame.

    `spawn` is injected for the tests: the pipe's contract is "W*H*3 bytes in
    order", and that can be exercised with any process, without ffmpeg installed.
    """

    def __init__(self, ruta, size, fps=30.0, spawn=None, ffmpeg=None):
        self.ruta = str(ruta)
        self.size = (size.width, size.height) if hasattr(size, "width") else tuple(size)
        self.fps = max(0.1, float(fps or 30.0))
        self.warnings: list[str] = []
        self._spawn = spawn or self._spawn_ffmpeg
        self._ffmpeg = ffmpeg
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ultimo = None
        # ffmpeg takes tens of milliseconds to emit its first frame, and --save
        # draws one and exits: without waiting for it, the video background comes
        # out as a flat colour. It waits ONCE and at most this long.
        self.espera_primero = 2.0
        self._hubo_uno = threading.Event()
        self._ya_espere = False

    # --- start-up and shutdown ---

    def _comando(self):
        exe = self._ffmpeg or buscar_ffmpeg()
        if exe is None:
            raise FileNotFoundError(COMO_INSTALAR)
        ancho, alto = self.size
        return [
            exe, "-hide_banner", "-loglevel", "error",
            # -stream_loop before -i: it repeats the INPUT, so ffmpeg does the
            # looping and Python has nothing to restart when the video ends.
            "-stream_loop", "-1",
            # -re: at the natural pace. Without it ffmpeg decodes flat out, fills
            # the pipe and burns a whole core running ahead with frames nobody
            # is going to see.
            "-re", "-i", self.ruta,
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{ancho}x{alto}", "-r", f"{self.fps:g}",
            "-an", "-sn", "-",
        ]

    def _spawn_ffmpeg(self):
        # stderr to a pipe and not to DEVNULL: it is the ONLY place ffmpeg says why
        # it could not open the file -- it does not exist, it is not a video, the
        # codec is missing -- and without that the warning to the user is generic.
        # With -loglevel error it is a few lines, with no risk of filling the pipe.
        return subprocess.Popen(self._comando(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                creationflags=SIN_VENTANA)

    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._leer, daemon=True,
                                        name="vmaxpanel-video")
        self._thread.start()
        return self

    def _leer(self):
        try:
            self._leer_hasta_el_final()
        finally:
            # Whatever happens -- no ffmpeg, an unreadable file, a cut in the
            # middle -- whoever is waiting for the first frame has to stop waiting.
            # Without this, a decoder that dies instantly leaves frame() paying the
            # full timeout for nothing.
            self._hubo_uno.set()

    def _leer_hasta_el_final(self):
        try:
            self._proc = self._spawn()
        except FileNotFoundError:
            # Always the actionable text, whatever the exception says: a warning
            # reading "ffmpeg" and nothing else leaves the user exactly where they
            # were. The FileNotFoundError can come from _comando() -- the executable
            # was not found -- or from Popen itself, if the path existed and the
            # binary no longer does; the remedy is the same.
            self._avisar(COMO_INSTALAR)
            return
        except Exception as e:
            self._avisar(f"could not open the video: {e}")
            return
        ancho, alto = self.size
        tamano = ancho * alto * 3
        flujo = self._proc.stdout
        while not self._stop.is_set():
            # A hand-written readexactly: ffmpeg writes a continuous stream and a
            # read() returns whatever is there. Drawing that would be half an image plus
            # garbage, so a frame is published only once all W*H*3 bytes are in.
            datos = bytearray()
            while len(datos) < tamano and not self._stop.is_set():
                trozo = flujo.read(tamano - len(datos))
                if not trozo:
                    self._explicar_el_final(bool(self._ultimo))
                    return
                datos.extend(trozo)
            if len(datos) < tamano:
                return
            img = Image.frombytes("RGB", (ancho, alto), bytes(datos))
            with self._lock:
                self._ultimo = img
            self._hubo_uno.set()

    def _explicar_el_final(self, hubo_cuadros):
        """Tells "could not open" apart from "cut off in the middle".

        Both arrive as an empty stdout, and treating them alike sent the user to
        check the video's duration when the problem was the path. If no frame ever
        arrived, the video never started: that is what has to be said, with the
        reason ffmpeg gave.

        `-stream_loop -1` means a video that opened correctly never ends, so the
        case "there were frames and now there are none" is not a normal ending
        either.
        """
        motivo = self._stderr()
        if not hubo_cuadros:
            self._avisar(f"ffmpeg could not open {Path(self.ruta).name}"
                         + (f": {motivo}" if motivo else ""))
            return
        self._avisar(f"the video was cut short (ffmpeg stopped writing)"
                     + (f": {motivo}" if motivo else ""))

    def _stderr(self) -> str:
        """The last thing ffmpeg said, on one line.

        **It waits for the process to die before reading.** `read()` on a live
        process's stderr blocks until that process closes it, and this runs on the
        reader thread: an ffmpeg that closes stdout and stays alive would leave the
        thread hanging until somebody calls close(). If it has not died within a
        second, "" is returned and the warning goes out without a reason -- which is
        still better than a wedged thread, and better than an exception, because
        nobody would see it.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            proc.wait(timeout=1.0)
        except Exception:
            return ""
        try:
            crudo = proc.stderr.read() or b""
        except Exception:
            return ""
        lineas = [l.strip() for l in crudo.decode("utf-8", "replace").splitlines()
                  if l.strip()]
        return lineas[-1] if lineas else ""

    def _avisar(self, texto):
        if texto not in self.warnings:
            self.warnings.append(texto)

    def frame(self):
        """El ultimo cuadro completo, o None si todavia no llego ninguno.

        The FIRST call waits up to `espera_primero` seconds for the decoder to
        produce something; later calls never wait. That asymmetry is the point:
        whoever draws a single frame really needs the first one, and the panel loop
        cannot pay a wait per frame if the video never opens at all.
        """
        if not self._ya_espere:
            self._ya_espere = True
            self._hubo_uno.wait(self.espera_primero)
        with self._lock:
            return self._ultimo

    def close(self):
        """Baja ffmpeg y espera.

        An orphan ffmpeg keeps decoding video for nobody: the same class of stuck
        process the sensor sidecar already had, and the same remedy -- terminate and
        wait, not terminate alone.
        """
        self._stop.set()
        proc = self._proc
        if proc is not None:
            for accion in (proc.terminate, proc.kill):
                try:
                    accion()
                    proc.wait(timeout=3)
                    break
                except Exception:
                    continue
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
