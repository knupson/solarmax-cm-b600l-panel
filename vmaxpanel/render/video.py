"""Fondo de video, decodificado por ffmpeg como proceso externo.

**Por que ffmpeg y no una libreria de Python.** Este proyecto se reparte a otros
duenos del panel y el criterio es no sumar dependencias: PyAV o imageio-ffmpeg
significan una rueda binaria por plataforma y version de Python. Un ejecutable
suelto, en cambio, se deja al lado de la app y listo -- y muchisima gente ya lo
tiene. El costo es que si falta, el video no anda; a cambio, funciona con todo lo
que ffmpeg sepa abrir: mp4, webm, mkv, gif, lo que sea.

**Como.** Un solo ffmpeg por fondo, escupiendo RGB crudo del tamano exacto del
panel a stdout. Un hilo lo drena y guarda el ultimo cuadro completo. `-stream_loop
-1` hace que el video se repita sin que Python se entere, y `-re` lo hace ir al
ritmo natural: sin eso ffmpeg decodifica lo mas rapido que puede, llena el pipe y
quema un nucleo para nada.
"""
import shutil
import subprocess
import threading
from pathlib import Path

from PIL import Image

LIB = Path(__file__).resolve().parent.parent / "lib"
NOMBRES = ("ffmpeg.exe", "ffmpeg")

COMO_INSTALAR = ("falta ffmpeg para los fondos de video: instalalo con "
                 "'winget install Gyan.FFmpeg' o deja ffmpeg.exe en "
                 "vmaxpanel/lib/")

# Sin ventana de consola: la bandeja corre con pythonw y un ffmpeg lanzado normal
# abriria una ventana negra en cada arranque.
SIN_VENTANA = 0x08000000


def buscar_ffmpeg():
    """Ruta al ejecutable, o None.

    Primero al lado de la app y despues el PATH: dejar el .exe en vmaxpanel/lib/
    tiene que alcanzar, porque es lo que evita pedirle al usuario que toque
    variables de entorno.
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
    """Un ffmpeg decodificando a RGB crudo, y el ultimo cuadro completo.

    `spawn` se inyecta para los tests: el contrato de la tuberia es "bytes de
    W*H*3 en orden", y eso se prueba con cualquier proceso, sin ffmpeg instalado.
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

    # --- arranque y baja ---

    def _comando(self):
        exe = self._ffmpeg or buscar_ffmpeg()
        if exe is None:
            raise FileNotFoundError(COMO_INSTALAR)
        ancho, alto = self.size
        return [
            exe, "-hide_banner", "-loglevel", "error",
            # -stream_loop antes de -i: repite la ENTRADA, asi que el loop lo hace
            # ffmpeg y Python no tiene que reiniciar nada al terminar el video.
            "-stream_loop", "-1",
            # -re: al ritmo natural. Sin esto ffmpeg decodifica a fondo, llena el
            # pipe y consume un nucleo entero para adelantar cuadros que nadie va
            # a ver.
            "-re", "-i", self.ruta,
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{ancho}x{alto}", "-r", f"{self.fps:g}",
            "-an", "-sn", "-",
        ]

    def _spawn_ffmpeg(self):
        # stderr a un pipe y no a DEVNULL: es el UNICO lugar donde ffmpeg dice por
        # que no pudo abrir el archivo -- no existe, no es un video, falta el codec
        # -- y sin eso el aviso al usuario es generico. Con -loglevel error son unas
        # pocas lineas, no hay riesgo de llenar el pipe.
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
            self._proc = self._spawn()
        except FileNotFoundError:
            # Siempre el texto accionable, sin importar que diga la excepcion: un
            # aviso que dice "ffmpeg" y nada mas deja al usuario en el mismo lugar
            # que estaba. El FileNotFoundError puede venir de _comando() -- no se
            # encontro el ejecutable -- o del propio Popen, si la ruta existia y el
            # binario ya no; el remedio es el mismo.
            self._avisar(COMO_INSTALAR)
            return
        except Exception as e:
            self._avisar(f"no se pudo abrir el video: {e}")
            return
        ancho, alto = self.size
        tamano = ancho * alto * 3
        flujo = self._proc.stdout
        while not self._stop.is_set():
            # readexactly a mano: ffmpeg escribe un stream continuo y un read()
            # devuelve lo que haya. Dibujar eso seria media imagen con basura, asi
            # que solo se publica un cuadro cuando estan los W*H*3 bytes.
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

    def _explicar_el_final(self, hubo_cuadros):
        """Distingue "no se pudo abrir" de "se corto en el medio".

        Los dos llegan como un stdout vacio, y tratarlos igual mandaba al usuario a
        mirar la duracion del video cuando el problema era la ruta. Si nunca llego un
        cuadro, el video nunca arranco: eso es lo que hay que decir, con el motivo
        que dio ffmpeg.

        `-stream_loop -1` hace que un video que se abrio bien no termine nunca, asi
        que el caso "hubo cuadros y ahora no hay" tampoco es un final normal.
        """
        motivo = self._stderr()
        if not hubo_cuadros:
            self._avisar(f"ffmpeg no pudo abrir {Path(self.ruta).name}"
                         + (f": {motivo}" if motivo else ""))
            return
        self._avisar(f"el video se corto (ffmpeg dejo de escribir)"
                     + (f": {motivo}" if motivo else ""))

    def _stderr(self) -> str:
        """Lo ultimo que dijo ffmpeg, en una linea.

        Se lee sin bloquear indefinidamente: el proceso ya cerro stdout, asi que su
        stderr esta cerrado o a punto. Si algo sale mal leyendo, se devuelve "" --
        un aviso sin motivo sigue siendo mejor que una excepcion en el hilo lector.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
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
        """El ultimo cuadro completo, o None si todavia no llego ninguno."""
        with self._lock:
            return self._ultimo

    def close(self):
        """Baja ffmpeg y espera.

        Un ffmpeg huerfano sigue decodificando video para nadie: es la misma
        clase de proceso colgado que el sidecar de sensores ya tuvo, y el mismo
        remedio -- terminate y wait, no solo terminate.
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
