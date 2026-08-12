import json

import pytest

from vmaxpanel.engine import Engine, EngineConfig
from vmaxpanel.layout import loader
from vmaxpanel.providers.base import Provider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.transport.panel_link import FakeTransport, PanelLink
from tests.test_schema import MINIMAL


class FakeCpu(Provider):
    id = "psutil"

    def __init__(self, value=42.0):
        self.value = value
        self.reads = 0

    def probe(self):
        return True

    def metrics(self):
        return {"cpu.load"}

    def read(self):
        self.reads += 1
        return {"cpu.load": self.value}


class FakeClock:
    """Reloj virtual: el loop avanza sin dormir de verdad."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)


def profile(tmp_path, **over):
    raw = dict(MINIMAL)
    raw.update(over)
    path = tmp_path / "vitals.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def engine(tmp_path, transports=None, iterations=3, **over):
    path = profile(tmp_path, **over)
    store = loader.ProfileStore(path)
    store.load_now()
    made = []

    def factory():
        t = (transports or [FakeTransport()]).pop(0) if transports else FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    cfg = EngineConfig(profile_path=path, max_iterations=iterations)
    eng = Engine(store, Registry([FakeCpu()]), cfg, link_factory=factory, clock=clock)
    return eng, made, clock


def test_run_sends_one_frame_per_iteration(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=3)
    eng.run()
    frames = [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"]
    assert len(frames) == 3
    assert eng.state()["frames"] == 3


def test_run_handshakes_and_sets_brightness_once(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=3)
    eng.run()
    writes = made[0].writes
    assert writes[0] == b"\xf0\xa5\x5a\x0f"
    assert sum(1 for w in writes if w[:2] == b"\xaa\xbb") == 1


def test_state_reports_the_panel_and_the_profile(tmp_path):
    # El estado "ok" solo tiene sentido MIENTRAS el link esta abierto: run()
    # cierra y descarta el link al volver (por cualquier motivo, ver
    # test_clean_exit_closes_the_link), asi que state() consultado despues
    # de run() siempre da "desconectado". Para probar el estado "conectado"
    # de verdad hay que mirarlo desde adentro del loop, no despues.
    eng, _, _ = engine(tmp_path, iterations=1)
    captured = {}
    original = eng._render_once

    def patched():
        original()
        captured.update(eng.state())

    eng._render_once = patched
    eng.run()
    assert captured["panel"] == "ok"
    assert captured["profile"] == "Test"
    assert captured["sn"].startswith("VMAX")
    assert captured["resolution"]["cpu.load"] == "psutil"


def test_state_lists_unavailable_metrics_with_reasons(tmp_path):
    from vmaxpanel.providers.msr import MsrProvider
    path = profile(tmp_path)
    store = loader.ProfileStore(path)
    store.load_now()
    eng = Engine(store, Registry([FakeCpu(), MsrProvider()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=lambda: PanelLink(FakeTransport()), clock=FakeClock())
    eng.run()
    assert "WinRing0" in eng.state()["unavailable"]["cpu.power"]


def test_frame_rate_respects_the_layout_fps(tmp_path):
    eng, _, clock = engine(tmp_path, iterations=4,
                           panel={"rotate": 0, "brightness": 100, "fps": 2,
                                  "jpeg_quality": 82})
    start = clock.now
    eng.run()
    assert 1.4 <= clock.now - start <= 1.6      # 3 esperas de 0.5 s


def test_sensors_are_sampled_once_per_period_not_once_per_frame(tmp_path):
    path = profile(tmp_path, panel={"rotate": 0, "brightness": 100, "fps": 4,
                                    "jpeg_quality": 82})
    store = loader.ProfileStore(path)
    store.load_now()
    cpu = FakeCpu()
    eng = Engine(store, Registry([cpu]),
                 EngineConfig(profile_path=path, sample_period=1.0, max_iterations=8),
                 link_factory=lambda: PanelLink(FakeTransport()), clock=FakeClock())
    eng.run()
    assert cpu.reads <= 4          # 8 frames a 4 fps = 2 s => 2-3 muestras, no 8


def test_layout_change_is_picked_up_without_restarting(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original_run = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text(json.dumps(dict(MINIMAL, name="Recargado")),
                            encoding="utf-8")
        return original_run()

    eng._render_once = patched
    eng.run()
    assert eng.state()["profile"] == "Recargado"


def test_broken_layout_on_reload_keeps_rendering_the_previous_one(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original_run = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text("{roto", encoding="utf-8")
        return original_run()

    eng._render_once = patched
    eng.run()
    st = eng.state()
    assert st["profile"] == "Test"           # sigue el bueno
    assert st["frames"] == 4                 # y no dejo de dibujar
    assert any("JSON" in w for w in st["warnings"])


def test_serial_failure_reconnects_with_backoff(tmp_path):
    dead = FakeTransport(fail_on_write=OSError("puerto tomado"))
    alive = FakeTransport()
    eng, made, clock = engine(tmp_path, transports=[dead, alive], iterations=2)
    start = clock.now
    eng.run()
    assert len(made) == 2
    assert clock.now > start                  # durmio el backoff
    # el transporte cuyo handshake fallo tiene que quedar cerrado: si
    # nada lo cierra, el handle recien abierto queda filtrado en cada
    # intento de reconexion en vez de liberarse antes del siguiente intento.
    assert dead.closed is True
    # "desconectado", no "ok": run() ya termino (se agoto max_iterations
    # DESPUES de reconectar con exito), asi que nada esta escribiendo en el
    # panel en este instante. Es el contrato, no un descuido -- el campo es
    # binario ("ok" | "desconectado") y no existe un tercer estado de
    # "estuvo conectado pero ya no". Que no se revierta a "ok" creyendo que
    # es "se conecto bien" cuando en realidad describe el estado ACTUAL.
    assert eng.state()["panel"] == "desconectado"


def test_clean_exit_closes_the_link(tmp_path):
    eng, made, _ = engine(tmp_path, iterations=2)
    eng.run()
    # run() termino sin excepcion (se agoto max_iterations): el transporte
    # que quedo abierto tiene que cerrarse igual, no solo cuando hay una
    # reconexion de por medio.
    assert made[0].closed is True
    # "desconectado", no "ok": el mismo contrato binario que documenta el
    # test de arriba. Un link cerrado reportando "ok" seria la misma clase
    # de status mintiendo que este proyecto entero existe para evitar (LCD
    # Control mostrando una carga de CPU que no era la real) -- aca aplicado
    # al campo de conexion en vez de a una metrica. No revertir esto a "ok"
    # pensando que "el ultimo intento salio bien": state() describe el
    # presente, no el historial.
    assert eng.state()["panel"] == "desconectado"


def test_stop_ends_the_loop(tmp_path):
    eng, _, _ = engine(tmp_path, iterations=None)
    original = eng._render_once

    def patched():
        original()
        if eng.stats["frames"] >= 2:
            eng.stop()

    eng._render_once = patched
    eng.run()
    assert eng.stats["frames"] == 2


def _one_frame(tmp_path, **panel):
    eng, made, _ = engine(tmp_path, iterations=1,
                          panel={"brightness": 100, "fps": 1, **panel})
    eng.run()
    return [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"][0]


def test_jpeg_quality_comes_from_the_profile(tmp_path):
    peor = _one_frame(tmp_path, rotate=0, jpeg_quality=40)
    mejor = _one_frame(tmp_path, rotate=0, jpeg_quality=95)
    assert len(peor) < len(mejor)


def test_rotation_comes_from_the_profile(tmp_path):
    """Esto se probaba con rotate 90, afirmando que el frame saliera
    1480x320. Es justo el frame deformado que un panel 320x1480 no puede
    mostrar y que la revision final marco como defecto: el engine ahora se
    niega a mandarlo (ver test_a_rotation_that_does_not_fit_the_panel...).
    La intencion original -- que el rotate salga del perfil y no de una
    constante -- se prueba igual con 180, la rotacion real de este gabinete,
    comparando CONTENIDO en vez de tamano: 0 y 180 dan los dos 320x1480, asi
    que el tamano no distinguia nada de todos modos.
    """
    import io
    from PIL import Image, ImageChops

    def frame_at(rotate):
        data = _one_frame(tmp_path, rotate=rotate, jpeg_quality=95)
        return Image.open(io.BytesIO(data)).convert("RGB")

    derecho, cabeza = frame_at(0), frame_at(180)
    assert derecho.size == cabeza.size == (320, 1480)
    assert ImageChops.difference(derecho, cabeza).getbbox() is not None

    # Con tolerancia, no exacto: el JPEG es con perdida, asi que rotar
    # despues de decodificar no reproduce byte a byte lo que salio de
    # codificar la imagen ya rotada. Mismo criterio que el test del golden.
    girado = cabeza.transpose(Image.Transpose.ROTATE_180)
    diff = ImageChops.difference(derecho, girado)
    peor = max(max(band.getextrema()) for band in diff.split())
    assert peor <= 40, f"180 no es la misma imagen dada vuelta (delta {peor})"


def test_an_invalid_profile_at_startup_is_picked_up_once_the_user_fixes_it(tmp_path):
    """_connect() tira OSError cuando no hay layout, y reload_if_changed()
    solo se llamaba desde _serve(), o sea despues de conectar: un engine
    arrancado con un perfil roto giraba en el backoff para siempre y nunca
    levantaba el archivo corregido. En fase 3 el servicio arranca antes de
    que el perfil exista, asi que ese es el caso normal, no el raro.

    El contador de sleeps acota la corrida: sin el arreglo esto es un loop
    infinito con reloj virtual, y un test que cuelga no reporta nada.
    """
    path = tmp_path / "vitals.json"
    path.write_text("{roto", encoding="utf-8")
    store = loader.ProfileStore(path)
    assert store.load_now()                      # arranca sin layout valido
    assert store.current is None

    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    eng = Engine(store, Registry([FakeCpu()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=factory, clock=clock)

    sleeps = []
    real_sleep = clock.sleep

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 1:
            # El usuario corrige el archivo mientras el engine espera.
            path.write_text(json.dumps(MINIMAL), encoding="utf-8")
        if len(sleeps) > 5:
            eng.stop()                           # cortamos: no se recupero
        real_sleep(s)

    clock.sleep = sleep
    eng.run()

    assert eng.stats["frames"] == 1, f"nunca releyo el perfil ({len(sleeps)} esperas)"
    assert store.current is not None
    assert [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"]


def test_a_rotation_that_does_not_fit_the_panel_is_refused_instead_of_sent(tmp_path):
    """rotate 90 sobre un panel 320x1480 produce un frame 1480x320 que el
    panel escribe sin chistar: basura en pantalla y cero errores en ningun
    lado. El validador de layouts no puede atajarlo -- no conoce la
    geometria del panel, y un layout disenado 1480x320 con rotate 90 SI es
    valido para este panel -- asi que se chequea aca, donde se conocen las
    dos cosas.
    """
    path = profile(tmp_path, panel={"rotate": 90, "brightness": 100, "fps": 1,
                                    "jpeg_quality": 82})
    store = loader.ProfileStore(path)
    assert store.load_now() == []            # el layout es valido; la rotacion no encaja

    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    clock = FakeClock()
    eng = Engine(store, Registry([FakeCpu()]),
                 EngineConfig(profile_path=path, max_iterations=1),
                 link_factory=factory, clock=clock)

    sleeps = []
    real_sleep = clock.sleep

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) > 3:
            eng.stop()
        real_sleep(s)

    clock.sleep = sleep
    eng.run()

    assert [w for w in made[0].writes if w[:3] == b"\xff\xd8\xff"] == []
    assert eng.stats["frames"] == 0
    assert "rotate" in (eng.state()["last_error"] or "")


def test_a_rejected_hot_reload_is_reported_instead_of_silent(tmp_path, capsys):
    """El invariante "un JSON roto no apaga el panel" hacia que un perfil
    rechazado fuera COMPLETAMENTE silencioso: el motor seguia dibujando el
    layout viejo y nada avisaba. Paso dos veces con el usuario mirando el panel
    y preguntando por que no cambiaba nada, y las dos veces la causa fue la
    misma -- una metrica nueva que el proceso vivo no conoce.
    """
    eng, made, _ = engine(tmp_path, iterations=4)
    path = tmp_path / "vitals.json"

    original = eng._render_once

    def patched():
        if eng.stats["frames"] == 1:
            path.write_text(json.dumps(dict(MINIMAL, widgets=[
                {"id": "x", "type": "text", "metric": "no.existe", "x": 1, "y": 1,
                 "font": "mono-14", "color": "#FFFFFF", "format": "{}"}])),
                encoding="utf-8")
        return original()

    eng._render_once = patched
    eng.run()

    # Una sola llamada: readouterr() DRENA el buffer, asi que una segunda
    # devuelve vacio y concatenar las dos pierde justo el stream que importa.
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err
    assert "rechaz" in salida.lower(), salida
    assert "no.existe" in salida
    assert eng.state()["profile"] == "Test"      # sigue con el bueno
    assert eng.stats["frames"] == 4              # y sin dejar de dibujar


def test_the_rejection_is_not_logged_once_per_frame(tmp_path, capsys):
    """A 30 fps, un aviso por cuadro son 1800 lineas por minuto en el log."""
    eng, made, _ = engine(tmp_path, iterations=6)
    path = tmp_path / "vitals.json"
    path.write_text("{roto", encoding="utf-8")
    eng.store.reload_if_changed()
    eng.run()
    capturado = capsys.readouterr()
    salida = capturado.out + capturado.err
    assert salida.lower().count("rechaz") <= 1, salida


def test_dropping_the_link_closes_the_renderer(tmp_path):
    """El renderer es el dueno del fondo, y un fondo de video tiene un ffmpeg
    atras. El engine descarta el renderer cada vez que se cae el link (y al
    terminar run()), asi que si no lo cierra, cada reconexion deja un decoder
    huerfano -- el mismo patron que este proyecto ya tuvo con el sidecar."""
    eng, made, _ = engine(tmp_path, iterations=1)
    eng.run()
    assert made[0].closed
    cerrados = []
    eng._renderer = type("R", (), {"close": lambda self: cerrados.append(True)})()
    eng._drop_link()
    assert cerrados == [True]
    assert eng._renderer is None
