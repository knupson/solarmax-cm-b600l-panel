"""Loop de render.

Cadencias separadas: los sensores se muestrean cada `sample_period` (1 s por
defecto) y los frames salen al fps del layout. En la fase 2, con fondos
animados, esa separacion es lo que permite un fondo a 10 fps con datos a 1 Hz
sin releer sensores 10 veces por segundo.

El transporte se inyecta (`link_factory`), asi que el loop entero se testea con
FakeTransport, sin el panel enchufado.
"""
import time
from dataclasses import dataclass

from .render.renderer import History, Renderer, to_jpeg
from .transport.panel_link import PanelNotFound


@dataclass
class EngineConfig:
    profile_path: str
    sample_period: float = 1.0
    reconnect_backoff: tuple = (1.0, 2.0, 5.0, 10.0)
    max_iterations: int | None = None       # None = para siempre; los tests lo acotan
    history_len: int = 320


class Engine:
    def __init__(self, store, registry, config, link_factory, clock=time):
        self.store = store
        self.registry = registry
        self.cfg = config
        self._link_factory = link_factory
        self._clock = clock
        self._stop = False
        self._link = None
        self._renderer = None
        self._history = History(config.history_len)
        self._sample = {}
        # None, no 0.0: distingue "todavia no muestree nunca" de "muestree en
        # el instante 0.0". Si se usara la verdad de self._sample para eso
        # (como en una version anterior de este loop), un registry que
        # devuelve {} -- sin providers o con todas las metricas UNAVAILABLE
        # y ninguna registrada -- nunca se vuelve "verdadero" y la guarda de
        # cadencia (`self._sample and ...`) jamas se activa: cada frame
        # volveria a leer el registry, exactamente la re-lectura de 10x que
        # esta separacion de cadencias existe para evitar.
        self._last_sample_at = None
        self._last_error = None
        self.stats = {"frames": 0, "reconnects": 0}

    # --- ciclo de vida ---

    def stop(self):
        self._stop = True

    def state(self) -> dict:
        layout = self.store.current
        return {
            "panel": "ok" if self._link is not None else "desconectado",
            "profile": layout.name if layout else None,
            "sn": self._link.serial_number if self._link else None,
            "fps": layout.panel.fps if layout else None,
            "resolution": self.registry.resolution(),
            "unavailable": self.registry.unavailable(),
            "warnings": (self._renderer.warnings() if self._renderer else []) + self.store.errors,
            "frames": self.stats["frames"],
            "last_error": self._last_error,
        }

    def run(self):
        attempt = 0
        try:
            while not self._done():
                try:
                    self._connect()
                    attempt = 0
                    self._serve()
                except (OSError, PanelNotFound) as e:
                    self._last_error = str(e)
                    self._drop_link()
                    if self._done():
                        break
                    self.stats["reconnects"] += 1
                    delay = self.cfg.reconnect_backoff[
                        min(attempt, len(self.cfg.reconnect_backoff) - 1)]
                    self._clock.sleep(delay)
                    attempt += 1
        finally:
            # Salida limpia (stop() o max_iterations) o una excepcion que se
            # escapa del loop entero (ValueError/RuntimeError de
            # programacion -- a proposito no capturadas arriba, ver
            # _render_once): en cualquiera de los dos casos hay que cerrar
            # el transporte que quedo abierto, no confiar en que el
            # recolector de basura lo haga cuando nada quede referenciando
            # el objeto. No se pasa por _drop_link() -- que ademas pone
            # self._link/self._renderer en None -- porque el loop ya
            # termino y no hay un proximo _connect() que necesite ese
            # estado limpio; state() sigue pudiendo reportar el ultimo sn y
            # "ok" despues de que run() retorna (asi lo verifica
            # test_serial_failure_reconnects_with_backoff).
            if self._link is not None:
                try:
                    self._link.close()
                except Exception:
                    pass

    def _done(self):
        if self._stop:
            return True
        limit = self.cfg.max_iterations
        return limit is not None and self.stats["frames"] >= limit

    # --- conexion ---

    def _connect(self):
        if self._link is not None:
            return
        # El chequeo de layout va ANTES de abrir el transporte: si se
        # invirtiera (abrir primero, chequear despues) un layout invalido
        # dejaria un transporte recien abierto sin cerrar -- _drop_link()
        # solo cierra self._link, y self._link todavia no se habria asignado
        # en esa rama, asi que el descriptor/puerto quedaria filtrado en
        # cada intento de reconexion. Chequear primero tambien evita abrir
        # el puerto de verdad para un problema que no tiene nada que ver con
        # el transporte.
        layout = self.store.current
        if layout is None:
            raise OSError("no hay un layout valido cargado")
        link = self._link_factory()
        try:
            link.open()
            link.set_brightness(layout.panel.brightness)
        except Exception:
            # link_factory() puede devolver un transporte que ya esta
            # abierto de verdad (SerialTransport abre el puerto serie en su
            # propio __init__, antes de que open() mande el handshake). Si
            # el handshake o el brillo inicial fallan aca, self._link nunca
            # llega a asignarse -- asi que _drop_link(), en el except de
            # run(), no tiene nada que cerrar y el handle recien abierto
            # quedaria filtrado en cada intento de reconexion, confiando en
            # el recolector de basura para cerrarlo. Mismo patron de "handle
            # abierto bloqueando el recurso" que este proyecto ya tiene
            # documentado para sensors.ps1/LibreHardwareMonitorLib.dll,
            # aplicado ahora al puerto COM del panel. Se relanza la
            # excepcion sin modificar: el backoff de run() la sigue viendo
            # igual.
            try:
                link.close()
            except Exception:
                pass
            raise
        self._link = link
        self._renderer = Renderer(layout, panel_size=link.geometry)

    def _drop_link(self):
        if self._link is not None:
            try:
                self._link.close()
            except Exception:
                pass
        self._link = None
        self._renderer = None

    # --- loop ---

    def _serve(self):
        while not self._done():
            t0 = self._clock.time()
            self._render_once()
            period = 1.0 / max(0.1, self.store.current.panel.fps)
            if self._done():
                return
            # max(0.0, ...): un frame que tarda mas que su periodo (render
            # lento, o el t0 de mas arriba mal medido) no puede volverse un
            # sleep negativo. No se acumula el atraso para "ponerse al dia"
            # con sleeps mas largos despues -- eso encadenaria la demora de
            # un frame lento a todos los que le siguen. Se lo deja caer: el
            # siguiente frame arranca en cuanto termina este, sin castigo
            # adicional ni intento de recuperar el tiempo perdido.
            self._clock.sleep(max(0.0, period - (self._clock.time() - t0)))

    def _render_once(self):
        self._refresh_layout()
        self._refresh_sample()
        layout = self.store.current
        img = self._renderer.frame(self._sample, self._history.series())
        self._link.send_frame(to_jpeg(img, layout.panel.rotate, layout.panel.jpeg_quality))
        self.stats["frames"] += 1

    def _refresh_layout(self):
        changed, _errors = self.store.reload_if_changed()
        if changed and self._renderer is not None:
            layout = self.store.current
            self._renderer.set_layout(layout)
            self._link.set_brightness(layout.panel.brightness)

    def _refresh_sample(self):
        now = self._clock.time()
        if self._last_sample_at is not None and now - self._last_sample_at < self.cfg.sample_period:
            return
        self._sample = self.registry.read()
        self._history.push(self._sample)
        self._last_sample_at = now
