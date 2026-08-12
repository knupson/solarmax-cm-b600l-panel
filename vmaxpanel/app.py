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
import sys
import threading
import time
from pathlib import Path

from .engine import Engine, EngineConfig
from .layout import loader, schema
from .providers.setup import build_registry
from .status import PERIODO as status_PERIODO
from .status import StatusFile
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
                 port=None, status_path=None, status_period=None):
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
        # El archivo de estado es opt-in: los tests del motor y un --once de una
        # sola pasada no tienen por que ensuciar el directorio.
        self._status = StatusFile(status_path) if status_path else None
        self._status_period = status_period or status_PERIODO
        self._status_stop = threading.Event()
        self._status_thread = None
        self._aviso_estado = False

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
            self._arrancar_latido()

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
        self._parar_latido()
        with self._lock:
            self._engine = None
            self._thread = None
        # Se publica DESPUES de bajar: si el archivo quedara con running=True,
        # `--estado` mentiria justo en el caso que mas importa -- el panel que se
        # apago solo y hay que averiguar por que.
        self.publicar_estado()

    def pause(self):
        if self._paused:
            return
        self.stop()
        self._paused = True
        # Otra vez despues, y no dentro de stop(): stop() publica con paused todavia
        # en False, y "detenido" manda a reiniciar algo que en realidad esta en
        # pausa a pedido del usuario.
        self.publicar_estado()

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

    # --- estado publicado a un archivo ---
    #
    # Ver status.py para el por que. En una frase: la bandeja muestra el estado en
    # su menu, pero desde una consola no habia nada, y verificar que el panel andaba
    # midiendo el CPU de un pythonw es adivinar.

    def publicar_estado(self) -> bool:
        """Escribe el estado actual al archivo, si hay uno configurado."""
        if self._status is None:
            return False
        st = dict(self.state())
        # problems() ya junta last_error + warnings + metricas sin datos y
        # deduplica: el lector no tiene que saber que estaban en tres campos.
        st["problems"] = self.problems()
        ok = self._status.write(st)
        if not ok and not self._aviso_estado:
            # Una sola vez: el latido corre cada 5 s, para siempre. Y al log, que es
            # el unico lugar donde se puede avisar -- si el archivo no se escribe,
            # `--estado` va a decir "no esta corriendo" para un panel que SI esta
            # dibujando, y esa mentira no se detecta desde afuera de ninguna otra
            # forma. Pasa de verdad con la app instalada en una carpeta de solo
            # lectura.
            self._aviso_estado = True
            print(f"no se pudo publicar el estado en {self._status.path}: "
                  f"'--estado' no va a poder contestar", file=sys.stderr)
        return ok

    def _arrancar_latido(self):
        if self._status is None or (self._status_thread and
                                   self._status_thread.is_alive()):
            return
        self._status_stop.clear()
        self._status_thread = threading.Thread(target=self._latido, daemon=True,
                                               name="vmaxpanel-estado")
        self._status_thread.start()

    def _latido(self):
        """Publica cada `status_period` hasta que le pidan parar.

        Event.wait y no sleep: bajar el motor no puede esperar hasta un periodo
        entero para que este hilo se entere.
        """
        self.publicar_estado()
        while not self._status_stop.wait(self._status_period):
            self.publicar_estado()

    def _parar_latido(self):
        self._status_stop.set()
        t = self._status_thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._status_thread = None

    # --- exportar ---

    def export_profile(self, carpeta=None, assets_dir=None, fecha=None) -> tuple:
        """Guarda el perfil y sus assets en un bundle. -> (ruta | None, mensaje).

        Sin dialogo de archivo: la bandeja es ctypes puro y abrir un
        GetSaveFileName desde el bombeo de mensajes es mas cirugia que valor. Va a
        una carpeta fija con la fecha en el nombre, y el mensaje dice donde quedo.
        Para elegir el destino esta el editor, que tiene Tkinter.

        Nunca levanta: quien llama a esto es el bombeo de mensajes de Win32, donde
        una excepcion no la ve nadie -- pythonw no tiene consola -- y deja la
        bandeja muda.
        """
        from . import bundle
        from .cli import HERE, assets_dir as assets_por_defecto
        # La carpeta se deriva de donde esta INSTALADO el paquete, no del perfil: un
        # perfil abierto desde el Escritorio hacia que "tres niveles arriba" cayera
        # en C:\Users, y el bundle aparecia en un lugar que nadie iba a mirar.
        carpeta = Path(carpeta) if carpeta else HERE.parent / "perfiles-exportados"
        assets = Path(assets_dir) if assets_dir else assets_por_defecto()
        if fecha is None:
            fecha = time.strftime("%Y-%m-%d")
        base = f"{Path(self.profile_path).stem}-{fecha}"
        destino = carpeta / f"{base}{bundle.EXT}"
        # Exportar dos veces el mismo dia no puede pisar el bundle anterior: puede
        # ser el que el usuario ya compartio.
        i = 2
        while destino.exists():
            destino = carpeta / f"{base}-{i}{bundle.EXT}"
            i += 1
        try:
            info = bundle.export_profile(self.profile_path, destino, assets)
        except Exception as e:
            return None, f"no se pudo exportar: {e}"
        cuantos = len(info["assets"])
        return destino, (f"exportado a {destino.name}"
                         + (f" con {cuantos} asset(s)" if cuantos else ""))

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
        return self._campo_panel("fps")

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
        return self._escribir_panel("fps", valor)

    # --- brillo ---
    #
    # Vive en el perfil, y el motor lo reaplica en cada recarga en caliente
    # (_refresh_layout llama a link.set_brightness), asi que cambiarlo NO
    # necesita reiniciar nada. Es el ajuste mas barato de exponer.
    BRILLOS = (25, 50, 75, 100)

    def brightness_options(self) -> list:
        return [(v, f"{v}%") for v in self.BRILLOS]

    def brightness(self):
        return self._campo_panel("brightness")

    def set_brightness(self, valor) -> list:
        return self._escribir_panel("brightness", valor)

    def _campo_panel(self, clave):
        try:
            crudo = json.loads(Path(self.profile_path).read_text(encoding="utf-8"))
            return (crudo.get("panel") or {}).get(clave)
        except Exception:
            return None

    def _escribir_panel(self, clave, valor) -> list:
        """Escribe un campo de `panel` en el perfil, validando antes.

        Mismo criterio que set_fps: un perfil invalido en disco lo rechaza el
        motor y se queda con el anterior, o sea que el usuario habria "cambiado"
        algo que el panel ignora. Y si el perfil no se puede leer no se escribe
        nada, porque pisarlo destruiria lo que haya quedado ahi.
        """
        ruta = Path(self.profile_path)
        try:
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception as e:
            return [f"no se pudo leer el perfil: {e}"]
        crudo.setdefault("panel", {})[clave] = valor
        errores = schema.validate(crudo)
        if errores:
            return errores
        loader.save_raw(crudo, ruta)
        return []

    # --- problemas, en una sola lista ---

    def problems(self) -> list:
        """Todo lo que anda mal ahora, junto y en lenguaje llano.

        El estado los tenia repartidos en tres campos -- warnings, unavailable y
        last_error -- y el menu miraba solo dos, asi que un perfil rechazado no
        aparecia en ninguna parte de la interfaz: quedaba en el log y nada mas.
        Un problema que el usuario no puede ver es un problema que no existe
        hasta que lo confunde.
        """
        st = self.state()
        fuera = []
        if st.get("last_error"):
            fuera.append(st["last_error"])
        fuera.extend(st.get("warnings") or [])
        faltan = st.get("unavailable") or {}
        if faltan:
            fuera.append(f"sin datos: {', '.join(sorted(faltan))}")
        # Se deduplica conservando el orden: el mismo aviso puede venir del
        # renderer y del store.
        vistos, unicos = set(), []
        for p in fuera:
            if p not in vistos:
                vistos.add(p)
                unicos.append(p)
        return unicos

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
