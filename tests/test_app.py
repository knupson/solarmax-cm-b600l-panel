"""PanelApp: el motor manejado como un servicio de la sesion del usuario.

Toda la logica de la app de bandeja vive aca y se prueba sin ventanas ni
panel: la bandeja (vmaxpanel/tray.py) es solo el menu de Win32 que llama a
estos metodos.
"""
import json
import time

from vmaxpanel.app import PanelApp
from vmaxpanel.providers.base import Provider
from vmaxpanel.providers.registry import Registry
from vmaxpanel.transport.panel_link import FakeTransport, PanelLink
from tests.test_schema import MINIMAL


class FakeCpu(Provider):
    id = "psutil"

    def probe(self):
        return True

    def metrics(self):
        return {"cpu.load"}

    def read(self):
        return {"cpu.load": 42.0}


def profile(tmp_path):
    path = tmp_path / "vitals.json"
    path.write_text(json.dumps(MINIMAL), encoding="utf-8")
    return path


def app_for(tmp_path, link_factory=None):
    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    return PanelApp(profile(tmp_path),
                    link_factory=link_factory or factory,
                    registry_factory=lambda: (Registry([FakeCpu()]), None)), made


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_start_renders_frames_in_the_background(tmp_path):
    app, made = app_for(tmp_path)
    app.start()
    try:
        assert wait_until(lambda: app.state()["frames"] >= 2)
        assert app.state()["running"] is True
    finally:
        app.stop()


def test_stop_ends_the_thread_and_releases_the_panel(tmp_path):
    app, made = app_for(tmp_path)
    app.start()
    assert wait_until(lambda: app.state()["frames"] >= 1)
    app.stop()
    assert app.state()["running"] is False
    assert made[0].closed is True


def test_pause_stops_drawing_and_frees_the_port_then_resume_comes_back(tmp_path):
    """Pausar tiene que soltar COM3, no solo dejar de dibujar: es como el
    usuario le deja el panel a LCD Control sin cerrar la app."""
    app, made = app_for(tmp_path)
    app.start()
    assert wait_until(lambda: app.state()["frames"] >= 1)

    app.pause()
    assert app.paused is True
    assert made[0].closed is True
    quietos = app.state()["frames"]
    time.sleep(0.3)
    assert app.state()["frames"] == quietos       # no dibuja mas

    app.resume()
    try:
        assert app.paused is False
        assert wait_until(lambda: app.state()["frames"] > quietos)
        assert len(made) == 2                      # reabrio el link
    finally:
        app.stop()


def test_start_is_idempotent(tmp_path):
    app, _ = app_for(tmp_path)
    app.start()
    app.start()
    try:
        assert wait_until(lambda: app.state()["frames"] >= 1)
    finally:
        app.stop()
    assert app.state()["running"] is False


def test_stop_does_not_wait_out_the_reconnect_backoff(tmp_path):
    """Sin un sleep interrumpible, salir desde la bandeja mientras el motor
    espera para reconectar tarda hasta 10 s con la ventana ya cerrada."""
    def failing():
        return PanelLink(FakeTransport(fail_on_write=OSError("puerto tomado")))

    app, _ = app_for(tmp_path, link_factory=failing)
    app.start()
    assert wait_until(lambda: app.state()["last_error"] is not None)

    t0 = time.time()
    app.stop()
    assert time.time() - t0 < 1.5, "se quedo esperando el backoff"
    assert app.state()["running"] is False


def test_state_survives_a_broken_profile_at_startup(tmp_path):
    path = tmp_path / "vitals.json"
    path.write_text("{roto", encoding="utf-8")
    app = PanelApp(path, link_factory=lambda: PanelLink(FakeTransport()),
                   registry_factory=lambda: (Registry([FakeCpu()]), None))
    app.start()
    try:
        assert wait_until(lambda: app.state()["warnings"])
        assert app.state()["profile"] is None
    finally:
        app.stop()
