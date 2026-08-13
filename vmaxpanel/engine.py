"""Loop de render.

Cadencias separadas: los sensores se muestrean cada `sample_period` (1 s por
defecto) y los frames salen al fps del layout. En la fase 2, con fondos
animados, esa separacion es lo que permite un fondo a 10 fps con datos a 1 Hz
sin releer sensores 10 veces por segundo.

El transporte se inyecta (`link_factory`), asi que el loop entero se testea con
FakeTransport, sin el panel enchufado.
"""
import sys
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
        self._rechazo_avisado = None
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
            "unavailable": self._sin_datos(layout),
            "warnings": (self._renderer.warnings() if self._renderer else []) + self.store.errors,
            "frames": self.stats["frames"],
            "last_error": self._last_error,
        }

    def _sin_datos(self, layout) -> dict:
        """Metricas sin dato: las que el Registry sabe, mas las que el LAYOUT usa y
        nadie sirve.

        Las de familia (`fan.9.rpm`, `core.12.temp`, `vol.Z.free`) no se pueden
        enumerar -- son un patron, no una lista --, asi que el Registry no puede
        reportarlas por su cuenta. El unico que sabe cuales se usan de verdad es este
        loop, que tiene el layout adelante. Sin esto, un perfil que pide una metrica
        que esta maquina no tiene dibuja guiones y el estado dice que no falta nada:
        exactamente la mentira de status que este proyecto existe para no cometer.
        """
        faltan = dict(self.registry.unavailable())
        if layout is None:
            return faltan
        servidas = self.registry.resolution()
        for w in layout.widgets:
            mid = getattr(w, "metric", None)
            if mid and mid not in servidas:
                faltan.setdefault(mid, "the profile uses it and no provider on this "
                                       "machine serves it")
        return faltan

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
            # _render_once): en cualquiera de los dos casos run() ya no va a
            # volver a escribir en el panel, asi que hay que cerrar el
            # transporte Y olvidarse de el -- pasar por _drop_link() en vez
            # de un close() suelto. Un close() sin poner self._link en None
            # dejaria state()["panel"] devolviendo "ok" para un link
            # provablemente cerrado (dead.closed/made[0].closed en los
            # tests de mas abajo), que es la misma clase de mentira de
            # estado que este proyecto existe para evitar: LCD Control
            # reportaba una carga de CPU que no era la real, y un
            # state()["panel"] == "ok" para un puerto cerrado es un status
            # field mintiendo por la misma razon, solo que en un campo
            # distinto. El contrato de este campo es binario -- "ok" o
            # "desconectado", sin un tercer estado de "estuvo conectado
            # pero ya no" -- asi que "desconectado" es la unica respuesta
            # honesta una vez que run() termino, para cualquier motivo por
            # el que haya terminado.
            self._drop_link()

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
            # Releer ACA y no solo en _serve(): _serve() corre despues de
            # conectar, asi que un engine arrancado sin layout valido nunca
            # llegaba a mirar el archivo de nuevo y giraba en el backoff para
            # siempre, incluso despues de que el usuario corrigiera el JSON.
            # En fase 3 el servicio arranca antes de que el perfil este
            # garantizado, o sea que este es el camino normal.
            self.store.reload_if_changed()
            layout = self.store.current
        if layout is None:
            errs = "; ".join(self.store.errors) or "no valid layout is loaded"
            raise OSError(errs)
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
        # El renderer tambien se cierra, no solo se olvida: es el dueno del
        # fondo, y un fondo de video tiene un ffmpeg atras. Soltar la referencia
        # sin cerrar dejaria un decoder corriendo por cada reconexion, esperando
        # que el recolector de basura lo limpie -- el mismo patron de proceso
        # huerfano que ya paso con el sidecar de sensores.
        if self._renderer is not None:
            try:
                self._renderer.close()
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
        rotate = layout.panel.rotate
        problem = self._rotation_problem(rotate)
        if problem is not None:
            # No se manda nada: el panel acepta un frame de la forma
            # equivocada sin chistar y lo pinta como basura. Tampoco se
            # levanta una excepcion -- reconectar no arregla un error de
            # configuracion, y matar el loop impediria que el perfil
            # corregido se recargue en caliente. Se registra y se sigue
            # girando al fps del layout hasta que alguien arregle el rotate.
            self._last_error = problem
            return
        self._link.send_frame(to_jpeg(img, rotate, layout.panel.jpeg_quality))
        self.stats["frames"] += 1

    def _rotation_problem(self, rotate) -> str | None:
        """Motivo por el que este `rotate` no encaja en este panel, o None.

        El validador de layouts no puede decidir esto: no conoce la geometria
        del panel, y un layout disenado 1480x320 con rotate 90 es
        perfectamente valido para un panel 320x1480. Aca se conocen las dos
        cosas -- el lienzo lo fija `panel_size=link.geometry` en _connect(),
        asi que 90/270 solo entran si el panel es cuadrado.
        """
        if rotate not in (90, 270):
            return None
        g = self._link.geometry
        if g.width == g.height:
            return None
        return (f"panel.rotate {rotate} turns the frame into {g.height}x{g.width}, "
                f"but the panel is {g.width}x{g.height}: use 0 or 180")

    def _refresh_layout(self):
        changed, errors = self.store.reload_if_changed()
        if errors:
            self._reportar_rechazo(errors)
        elif changed:
            self._rechazo_avisado = None
        if changed and self._renderer is not None:
            layout = self.store.current
            self._renderer.set_layout(layout)
            self._link.set_brightness(layout.panel.brightness)

    def _reportar_rechazo(self, errors):
        """Avisa que se rechazo un perfil, una sola vez por contenido.

        El invariante "un JSON roto no apaga el panel" tenia un costo escondido:
        un perfil rechazado era COMPLETAMENTE silencioso. El motor seguia
        dibujando el layout anterior y no quedaba rastro en ningun lado, asi que
        desde afuera se ve como "edite el perfil y el panel no cambio". Paso dos
        veces con el usuario mirando el panel, y las dos por el mismo motivo: una
        metrica nueva que el proceso vivo no conoce, porque el codigo cambio
        despues de que arranco.

        Una sola vez por contenido de error: a 30 fps, un aviso por cuadro son
        1800 lineas por minuto. Se resetea cuando entra un layout bueno, asi que
        el proximo rechazo vuelve a avisar.
        """
        firma = tuple(errors)
        if firma == getattr(self, "_rechazo_avisado", None):
            return
        self._rechazo_avisado = firma
        print(f"profile rejected, keeping the previous one: {'; '.join(errors)}",
              file=sys.stderr)
        print("  if you just added a metric, this process started earlier and does "
              "not know it: the tray has to be restarted.", file=sys.stderr)

    def _refresh_sample(self):
        now = self._clock.time()
        if self._last_sample_at is not None and now - self._last_sample_at < self.cfg.sample_period:
            return
        self._sample = self.registry.read()
        self._history.push(self._sample)
        self._last_sample_at = now
