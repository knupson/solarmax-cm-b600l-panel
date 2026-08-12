"""El motor manejado como una app de la sesion del usuario.

Toda la logica que la bandeja necesita vive aca, sin una sola llamada a
Win32: `tray.py` es solo el menu que invoca estos metodos. Asi el
comportamiento -- arrancar, pausar, reanudar, salir, reportar estado -- se
prueba entero sin ventanas y sin el panel enchufado.

Un servicio de Windows habria sido el lugar "canonico" para esto, pero corre
en la sesion 0: desde ahi no se puede mostrar un icono en la bandeja ni abrir
un editor, que es justamente lo que el usuario queria. La tarea programada al
logon hace de autostart y esta clase hace de servicio dentro de la sesion.
"""
import json
import threading
import time
from pathlib import Path

from .engine import Engine, EngineConfig
from .layout import loader, schema
from .providers.setup import build_registry
from .transport.panel_link import PanelLink


class _InterruptibleClock:
    """Reloj cuyo sleep se corta cuando alguien pide la baja.

    El engine duerme hasta 10 s entre reintentos de conexion. Con
    `time.sleep` eso significa que "Salir" en la bandeja tarda hasta 10 s en
    hacer efecto, con el menu ya cerrado y el usuario pensando que se colgo.
    """

    def __init__(self):
        self._wake = threading.Event()

    def time(self):
        return time.time()

    def sleep(self, seconds):
        if seconds > 0:
            self._wake.wait(seconds)

    def interrupt(self):
        self._wake.set()

    def reset(self):
        self._wake.clear()


class PanelApp:
    """Un motor corriendo en su propio thread, con arranque/pausa/baja.

    `pause()` no es "dejar de dibujar": baja el motor y suelta el puerto, que
    es como el usuario le presta el panel a LCD Control sin cerrar esto.
    `resume()` lo vuelve a levantar.
    """

    def __init__(self, profile_path, link_factory=None, registry_factory=None,
                 port=None):
        self.profile_path = profile_path
        self._link_factory = link_factory or (lambda: PanelLink.autodetect(port))
        self._registry_factory = registry_factory or build_registry
        self._lock = threading.Lock()
        self._thread = None
        self._clock = _InterruptibleClock()
        self._engine = None
        self._registry = None
        self._client = None
        self._paused = False
        self._last_state = {}

    # --- ciclo de vida ---

    @property
    def paused(self) -> bool:
        return self._paused

    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self):
        """Idempotente: llamarlo dos veces no levanta dos motores."""
        with self._lock:
            if self.running():
                return
            self._paused = False
            self._clock.reset()
            store = loader.ProfileStore(self.profile_path)
            store.load_now()          # un perfil roto no impide arrancar: el
                                      # engine reintenta y lo relee solo
            self._registry, self._client = self._registry_factory()
            self._engine = Engine(store, self._registry,
                                  EngineConfig(profile_path=self.profile_path),
                                  link_factory=self._link_factory,
                                  clock=self._clock)
            self._thread = threading.Thread(target=self._serve, daemon=True,
                                            name="vmaxpanel-engine")
            self._thread.start()

    def _serve(self):
        try:
            self._engine.run()
        finally:
            # El estado se congela ANTES de soltar los recursos: despues de
            # cerrar el registry, unavailable()/resolution() ya no describen
            # la corrida que acabo de terminar, y la bandeja sigue queriendo
            # mostrar por que quedo asi.
            self._last_state = self._snapshot()
            self._release()

    def _release(self):
        for closeable in (self._registry, self._client):
            if closeable is None:
                continue
            try:
                closeable.close()
            except Exception:
                pass
        self._registry = self._client = None

    def stop(self):
        """Pide la baja y espera. El sleep del engine es interrumpible, asi
        que esto vuelve enseguida incluso en medio del backoff."""
        with self._lock:
            eng, thread = self._engine, self._thread
        if eng is not None:
            eng.stop()
        self._clock.interrupt()
        if thread is not None:
            thread.join(timeout=10.0)
        with self._lock:
            self._engine = None
            self._thread = None

    def pause(self):
        if self._paused:
            return
        self.stop()
        self._paused = True

    def resume(self):
        if not self._paused:
            return
        self._paused = False
        self.start()

    def toggle(self):
        self.resume() if self._paused else self.pause()

    # --- perfiles ---

    def profiles(self) -> list:
        """Los .json que hay al lado del perfil actual, ordenados."""
        try:
            carpeta = Path(self.profile_path).parent
            return sorted(p for p in carpeta.glob("*.json"))
        except Exception:
            return [Path(self.profile_path)]

    def set_profile(self, path) -> list:
        """Cambia de perfil y reinicia el motor. Devuelve los errores.

        Se valida ANTES de tocar el motor que esta andando: cambiar a un perfil
        invalido dejaria el panel sin nada que dibujar, y el usuario habria
        perdido el que funcionaba por elegir mal de una lista.

        Reiniciar y no recargar en caliente porque el Registry se arma al
        arrancar: un perfil que usa metricas de un provider distinto necesita el
        registry nuevo, y el hot-reload solo cambia el layout.
        """
        nuevo = Path(path)
        if nuevo == Path(self.profile_path):
            return []
        try:
            loader.load(nuevo)
        except loader.LayoutError as e:
            return e.errors
        except OSError as e:
            return [f"no se pudo leer {nuevo.name}: {e}"]
        corria = self.running()
        if corria:
            self.stop()
        self.profile_path = nuevo
        if corria:
            self.start()
        return []

    # --- fps ---
    #
    # El costo de cada cadencia esta medido contra el panel real (i5-12400F, 12
    # hilos): CPU del proceso sostenida sobre 5 s. Se muestra al lado de cada
    # opcion porque elegir 60 fps sin saber que son 37% de un nucleo -- continuo,
    # mientras el panel este prendido -- no es elegir.
    FPS_OPCIONES = ((1, 0.6), (10, 5.9), (30, 17.2), (60, 37.2))

    def fps_options(self) -> list:
        """[(fps, etiqueta)] para el menu."""
        return [(v, f"{v} fps · {c:.0f}% de un núcleo") for v, c in self.FPS_OPCIONES]

    def fps(self):
        """El fps que dice el perfil en disco, o None si no se puede leer."""
        try:
            crudo = json.loads(self.profile_path.read_text(encoding="utf-8"))
            return (crudo.get("panel") or {}).get("fps")
        except Exception:
            return None

    def set_fps(self, valor) -> list:
        """Escribe el fps en el perfil. Devuelve los errores que lo impidieron.

        El fps vive en el perfil, asi que cambiarlo es editarlo: el motor lo
        recarga en caliente y no hace falta reiniciar. Se valida ANTES de
        escribir -- un perfil invalido en disco lo rechazaria el motor y se
        quedaria con el anterior, o sea que el usuario habria "cambiado" algo
        que el panel ignora.

        Si el perfil no se puede leer no se escribe nada: pisarlo con un fps
        nuevo destruiria lo que haya quedado ahi.
        """
        try:
            crudo = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except Exception as e:
            return [f"no se pudo leer el perfil: {e}"]
        crudo.setdefault("panel", {})["fps"] = valor
        errores = schema.validate(crudo)
        if errores:
            return errores
        loader.save_raw(crudo, self.profile_path)
        return []

    # --- estado para la bandeja ---

    def state(self) -> dict:
        """Nunca levanta: la bandeja pinta esto en cada apertura del menu."""
        if self.running():
            return self._snapshot()
        # Motor bajado: se devuelve la ultima foto viva, con running/paused
        # actualizados. Reconstruirlo desde un engine ya cerrado daria
        # "desconectado" y cero metricas, borrando el motivo por el que se
        # cayo justo cuando el usuario lo va a leer.
        return {**self._last_state, "running": False, "paused": self._paused}

    def _snapshot(self) -> dict:
        eng = self._engine
        if eng is None:
            return {"running": False, "paused": self._paused, "frames": 0,
                    "profile": None, "panel": "desconectado", "warnings": [],
                    "unavailable": {}, "resolution": {}, "last_error": None}
        st = dict(eng.state())
        st["running"] = self.running()
        st["paused"] = self._paused
        return st
