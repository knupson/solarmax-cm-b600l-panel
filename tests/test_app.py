"""PanelApp: el motor manejado como un servicio de la sesion del usuario.

Toda la logica de la app de bandeja vive aca y se prueba sin ventanas ni
panel: la bandeja (vmaxpanel/tray.py) es solo el menu de Win32 que llama a
estos metodos.
"""
import json
import time
from pathlib import Path

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


def app_for(tmp_path, link_factory=None, **kw):
    """PanelApp con transporte y registry falsos. `kw` va derecho al constructor
    (status_path, status_period, port)."""
    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    return PanelApp(profile(tmp_path),
                    link_factory=link_factory or factory,
                    registry_factory=lambda: (Registry([FakeCpu()]), None),
                    **kw), made


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


# --- fps elegible desde la bandeja ---

def test_set_fps_writes_the_profile_and_the_engine_picks_it_up(tmp_path):
    """El fps vive en el perfil, asi que cambiarlo es editarlo: el motor lo
    recarga en caliente sin reiniciar nada."""
    app, _ = app_for(tmp_path)
    assert app.fps() == 1
    assert app.set_fps(30) == []
    assert app.fps() == 30
    assert json.loads(app.profile_path.read_text(encoding="utf-8"))["panel"]["fps"] == 30

    app.start()
    try:
        assert wait_until(lambda: app.state()["fps"] == 30)
    finally:
        app.stop()


def test_set_fps_rejects_what_the_panel_cannot_show(tmp_path):
    """Por encima del refresco del panel los frames se descartan: es CPU
    quemada al vacio."""
    app, _ = app_for(tmp_path)
    errores = app.set_fps(120)
    assert errores and any("fps" in e for e in errores)
    assert app.fps() == 1                      # no lo escribio
    assert json.loads(app.profile_path.read_text(encoding="utf-8"))["panel"]["fps"] == 1


def test_set_fps_keeps_the_rest_of_the_profile_intact(tmp_path):
    app, _ = app_for(tmp_path)
    antes = json.loads(app.profile_path.read_text(encoding="utf-8"))
    app.set_fps(60)
    despues = json.loads(app.profile_path.read_text(encoding="utf-8"))
    assert despues["widgets"] == antes["widgets"]
    assert despues["panel"]["brightness"] == antes["panel"]["brightness"]
    assert list(despues) == list(antes)


def test_set_fps_on_a_broken_profile_reports_instead_of_overwriting(tmp_path):
    """Si el perfil no se puede leer, escribir un fps encima lo destruiria."""
    app, _ = app_for(tmp_path)
    app.profile_path.write_text("{roto", encoding="utf-8")
    errores = app.set_fps(30)
    assert errores
    assert app.profile_path.read_text(encoding="utf-8") == "{roto"


def test_the_cpu_cost_of_each_option_is_published(tmp_path):
    """La bandeja muestra el costo al lado de cada opcion: elegir 60 fps sin
    saber que son 37% de un nucleo no es elegir."""
    app, _ = app_for(tmp_path)
    opciones = app.fps_options()
    assert [v for v, _ in opciones] == [1, 10, 30, 60]
    for valor, etiqueta in opciones:
        assert str(valor) in etiqueta
        assert "%" in etiqueta


# --- cambio de perfil ---

def test_profiles_lists_the_json_files_next_to_the_current_one(tmp_path):
    app, _ = app_for(tmp_path)
    (app.profile_path.parent / "otro.json").write_text(
        json.dumps(MINIMAL), encoding="utf-8")
    (app.profile_path.parent / "notas.txt").write_text("x", encoding="utf-8")
    nombres = [p.name for p in app.profiles()]
    assert app.profile_path.name in nombres
    assert "otro.json" in nombres
    assert "notas.txt" not in nombres


def test_switching_profile_restarts_the_engine_on_the_new_one(tmp_path):
    app, _ = app_for(tmp_path)
    otro = app.profile_path.parent / "otro.json"
    otro.write_text(json.dumps(dict(MINIMAL, name="Otro")), encoding="utf-8")
    app.start()
    try:
        assert wait_until(lambda: app.state()["frames"] >= 1)
        assert app.set_profile(otro) == []
        assert app.profile_path == otro
        assert wait_until(lambda: app.state()["profile"] == "Otro")
    finally:
        app.stop()


def test_switching_to_a_broken_profile_is_refused(tmp_path):
    """Cambiar a un perfil invalido dejaria el panel sin nada que dibujar. Se
    valida ANTES de tocar el motor que esta andando."""
    app, _ = app_for(tmp_path)
    roto = app.profile_path.parent / "roto.json"
    roto.write_text("{no es json", encoding="utf-8")
    original = app.profile_path
    errores = app.set_profile(roto)
    assert errores
    assert app.profile_path == original


def test_switching_to_the_same_profile_is_a_no_op(tmp_path):
    app, _ = app_for(tmp_path)
    assert app.set_profile(app.profile_path) == []


# --- brillo ---

def test_brightness_options_and_writing(tmp_path):
    """El brillo vive en el perfil y el motor lo aplica en cada recarga, asi que
    cambiarlo NO necesita reiniciar: es el mejor candidato para el menu."""
    app, _ = app_for(tmp_path)
    assert [v for v, _ in app.brightness_options()] == [25, 50, 75, 100]
    assert app.brightness() == 100
    assert app.set_brightness(50) == []
    assert app.brightness() == 50
    assert json.loads(app.profile_path.read_text(encoding="utf-8"))["panel"]["brightness"] == 50


def test_brightness_out_of_range_is_refused(tmp_path):
    app, _ = app_for(tmp_path)
    assert app.set_brightness(150)
    assert app.brightness() == 100


def test_problems_lists_what_is_wrong_right_now(tmp_path):
    """La bandeja necesita UNA lista de problemas para mostrar. Hoy el estado
    tiene warnings, unavailable y last_error en tres campos distintos y el menu
    solo miraba dos."""
    app, _ = app_for(tmp_path)
    app.profile_path.write_text("{roto", encoding="utf-8")
    app.start()
    try:
        assert wait_until(lambda: app.problems())
        assert any("perfil" in p.lower() or "json" in p.lower()
                   for p in app.problems())
    finally:
        app.stop()


def test_problems_is_empty_when_everything_is_fine(tmp_path):
    app, _ = app_for(tmp_path)
    app.start()
    try:
        assert wait_until(lambda: app.state()["frames"] >= 1)
        assert app.problems() == []
    finally:
        app.stop()


# --- exportar desde la bandeja ---


def test_export_writes_a_bundle_next_to_the_project(tmp_path):
    """La bandeja no puede abrir un dialogo de archivo -- es ctypes puro -- asi que
    exporta sola a una carpeta fija y dice donde quedo. El nombre lleva fecha para
    que exportar dos veces no pise nada."""
    app, _ = app_for(tmp_path)
    destino, mensaje = app.export_profile(carpeta=tmp_path / "salidas",
                                          assets_dir=tmp_path, fecha="2026-08-12")
    assert destino is not None
    assert destino.name == "vitals-2026-08-12.vmaxpanel"
    assert destino.exists()
    assert destino.name in mensaje


def test_exporting_twice_does_not_overwrite(tmp_path):
    app, _ = app_for(tmp_path)
    kw = dict(carpeta=tmp_path / "salidas", assets_dir=tmp_path, fecha="2026-08-12")
    primero, _ = app.export_profile(**kw)
    segundo, _ = app.export_profile(**kw)
    assert segundo != primero
    assert primero.exists() and segundo.exists()


def test_exporting_a_broken_profile_reports_instead_of_raising(tmp_path):
    """Esto lo llama el bombeo de mensajes de Win32: una excepcion ahi no la ve
    nadie (Tkinter no esta, y pythonw no tiene consola) y deja la bandeja muda."""
    app, _ = app_for(tmp_path)
    Path(app.profile_path).write_text("{ roto", encoding="utf-8")
    destino, mensaje = app.export_profile(carpeta=tmp_path / "s", assets_dir=tmp_path)
    assert destino is None
    assert "no" in mensaje.lower()
