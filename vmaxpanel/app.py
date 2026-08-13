"""The engine run as an app inside the user's session.

All the logic the tray needs lives here, without a single Win32 call: `tray.py`
is only the menu that invokes these methods. That way the
comportamiento -- arrancar, pausar, reanudar, salir, reportar estado -- se
is tested in full without windows and without the panel plugged in.

A Windows service would have been the "canonical" place for this, but it runs in
session 0: from there it cannot show a tray icon or open an editor, which is
exactly what was wanted. The scheduled task at logon acts as the autostart and
this class acts as the service inside the session.
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
    """A clock whose sleep is cut short when somebody asks to shut down.

    The engine sleeps up to 10 s between connection retries. With `time.sleep`
    that means "Exit" in the tray takes up to 10 s to take effect, with the menu
    already closed and the user thinking it hung.
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
    """An engine running on its own thread, with start/pause/stop.

    `pause()` is not "stop drawing": it brings the engine down and releases the
    port, which is how the user lends the panel to LCD Control without closing
    this.
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
        # The status file is opt-in: the engine tests and a single-pass --once have
        # no reason to litter the directory.
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
            store.load_now()          # a broken profile does not stop start-up:
                                      # the engine retries and re-reads it itself
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
            # The state is frozen BEFORE releasing the resources: once the registry
            # is closed, unavailable()/resolution() no longer describe the run that
            # just ended, and the tray still wants to show why it ended that way.
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
        """Asks for shutdown and waits. The engine's sleep is interruptible, so this
        returns immediately even in the middle of the backoff."""
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
        # Published AFTER shutting down: if the file were left with running=True,
        # `--status` would lie in exactly the case that matters most -- the panel
        # that switched itself off and somebody has to find out why.
        self.publicar_estado()

    def pause(self):
        if self._paused:
            return
        self.stop()
        self._paused = True
        # Again afterwards, and not inside stop(): stop() publishes with paused
        # still False, and "stopped" sends the user to restart something that is
        # actually paused at their own request.
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
        """The .json files sitting beside the current profile, sorted."""
        try:
            carpeta = Path(self.profile_path).parent
            return sorted(p for p in carpeta.glob("*.json"))
        except Exception:
            return [Path(self.profile_path)]

    def set_profile(self, path) -> list:
        """Switches profile and restarts the engine. Returns the errors.

        Validated BEFORE touching the running engine: switching to an invalid
        profile would leave the panel with nothing to draw, and the user would have
        lost the one that worked by picking wrongly from a list.

        A restart and not a hot reload because the Registry is built at start-up: a
        profile using metrics from a different provider needs the new registry, and
        the hot reload only changes the layout.
        """
        nuevo = Path(path)
        if nuevo == Path(self.profile_path):
            return []
        try:
            loader.load(nuevo)
        except loader.LayoutError as e:
            return e.errors
        except OSError as e:
            return [f"could not read {nuevo.name}: {e}"]
        corria = self.running()
        if corria:
            self.stop()
        self.profile_path = nuevo
        if corria:
            self.start()
        return []

    # --- estado publicado a un archivo ---
    #
    # See status.py for the why. In one sentence: the tray shows the status in its
    # menu, but from a console there was nothing, and verifying the panel worked by
    # watching a pythonw process's CPU is guessing.

    def publicar_estado(self) -> bool:
        """Writes the current state to the file, if one is configured."""
        if self._status is None:
            return False
        st = dict(self.state())
        # problems() already merges last_error + warnings + metrics with no data and
        # deduplicates: the reader does not have to know they were in three fields.
        st["problems"] = self.problems()
        ok = self._status.write(st)
        if not ok and not self._aviso_estado:
            # Once only: the heartbeat runs every 5 s, forever. And to the log, which
            # is the only place a warning can go -- if the file is not written,
            # `--status` will say "it is not running" for a panel that IS drawing,
            # and that lie cannot be detected from outside in any other way. It
            # really happens with the app installed in a read-only
            # lectura.
            self._aviso_estado = True
            print(f"could not publish the status to {self._status.path}: "
                  f"'--status' will not be able to answer", file=sys.stderr)
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
        """Publishes every `status_period` until asked to stop.

        Event.wait and not sleep: bringing the engine down cannot wait a whole
        period for this thread to notice.
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
        """Saves the profile and its assets into a bundle. -> (path | None, message).

        No file dialog: the tray is pure ctypes and opening a GetSaveFileName from
        the message pump is more surgery than it is worth. It goes to a fixed folder
        with the date in the name, and the message says where it landed. For picking
        the destination there is the editor, which has Tkinter.

        It never raises: what calls this is the Win32 message pump, where an
        exception is seen by nobody -- pythonw has no console -- and leaves the
        bandeja muda.
        """
        from . import bundle
        from .cli import HERE, assets_dir as assets_por_defecto
        # The folder is derived from where the package is INSTALLED, not from the
        # profile: a profile opened from the Desktop made "three levels up" land in
        # C:\Users, and the bundle appeared somewhere nobody was going to look.
        carpeta = Path(carpeta) if carpeta else HERE.parent / "perfiles-exportados"
        assets = Path(assets_dir) if assets_dir else assets_por_defecto()
        if fecha is None:
            fecha = time.strftime("%Y-%m-%d")
        base = f"{Path(self.profile_path).stem}-{fecha}"
        destino = carpeta / f"{base}{bundle.EXT}"
        # Exporting twice on the same day must not overwrite the previous bundle: it
        # may be the one the user already shared.
        i = 2
        while destino.exists():
            destino = carpeta / f"{base}-{i}{bundle.EXT}"
            i += 1
        try:
            info = bundle.export_profile(self.profile_path, destino, assets)
        except Exception as e:
            return None, f"could not export: {e}"
        cuantos = len(info["assets"])
        return destino, (f"exported to {destino.name}"
                         + (f" with {cuantos} asset(s)" if cuantos else ""))

    # --- fps ---
    #
    # The cost of each cadence is measured against the real panel (i5-12400F, 12
    # threads): the process's CPU sustained over 5 s. It is shown beside each option
    # because choosing 60 fps without knowing it is 37% of one core -- continuously,
    # for as long as the panel is on -- is not choosing.
    FPS_OPCIONES = ((1, 0.6), (10, 5.9), (30, 17.2), (60, 37.2))

    def fps_options(self) -> list:
        """[(fps, label)] for the menu."""
        return [(v, f"{v} fps · {c:.0f}% of one core") for v, c in self.FPS_OPCIONES]

    def fps(self):
        """The fps the profile on disk states, or None if it cannot be read."""
        return self._campo_panel("fps")

    def set_fps(self, valor) -> list:
        """Writes the fps into the profile. Returns whatever errors prevented it.

        The fps lives in the profile, so changing it means editing the profile: the
        engine hot-reloads it and nothing needs restarting. It is validated BEFORE
        writing -- an invalid profile on disk would be rejected by the engine, which
        would keep the previous one, meaning the user would have "changed" something
        the panel ignores.

        If the profile cannot be read, nothing is written: overwriting it with a new
        fps would destroy whatever was left in there.
        """
        return self._escribir_panel("fps", valor)

    # --- brillo ---
    #
    # It lives in the profile, and the engine reapplies it on every hot reload
    # (_refresh_layout calls link.set_brightness), so changing it needs NO restart.
    # It is the cheapest setting to expose.
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
        """Writes one `panel` field into the profile, validating first.

        Same rule as set_fps: an invalid profile on disk is rejected by the engine,
        which keeps the previous one, meaning the user would have "changed"
        something the panel ignores. And if the profile cannot be read nothing is
        written, because overwriting it would destroy whatever was left in there.
        """
        ruta = Path(self.profile_path)
        try:
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception as e:
            return [f"could not read the profile: {e}"]
        crudo.setdefault("panel", {})[clave] = valor
        errores = schema.validate(crudo)
        if errores:
            return errores
        loader.save_raw(crudo, ruta)
        return []

    # --- problems, in a single list ---

    def problems(self) -> list:
        """Everything that is wrong right now, together and in plain language.

        The state had them spread across three fields -- warnings, unavailable and
        last_error -- and the menu looked at only two, so a rejected profile appeared
        nowhere in the interface: it stayed in the log and nowhere else. A problem
        the user cannot see is a problem that does not exist until it confuses them.
        """
        st = self.state()
        fuera = []
        if st.get("last_error"):
            fuera.append(st["last_error"])
        fuera.extend(st.get("warnings") or [])
        faltan = st.get("unavailable") or {}
        if faltan:
            fuera.append(f"no data: {', '.join(sorted(faltan))}")
        # Deduplicated while preserving order: the same warning can come from both
        # the renderer and the store.
        vistos, unicos = set(), []
        for p in fuera:
            if p not in vistos:
                vistos.add(p)
                unicos.append(p)
        return unicos

    # --- state for the tray ---

    def state(self) -> dict:
        """It never raises: the tray paints this every time the menu opens."""
        if self.running():
            return self._snapshot()
        # Engine down: the last live snapshot is returned, with running/paused
        # updated. Rebuilding it from an already-closed engine would give
        # "disconnected" and zero metrics, erasing the reason it fell over at
        # exactly the moment the user is about to read it.
        return {**self._last_state, "running": False, "paused": self._paused}

    def _snapshot(self) -> dict:
        eng = self._engine
        if eng is None:
            return {"running": False, "paused": self._paused, "frames": 0,
                    "profile": None, "panel": "disconnected", "warnings": [],
                    "unavailable": {}, "resolution": {}, "last_error": None}
        st = dict(eng.state())
        st["running"] = self.running()
        st["paused"] = self._paused
        return st
