"""PanelApp: the engine run as a service inside the user's session.

All of the tray app's logic lives here and is tested without windows and without
the panel: the tray (vmaxpanel/tray.py) is only the Win32 menu that calls these
methods.
"""
import copy
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


def profile(tmp_path, fps=None):
    path = tmp_path / "vitals.json"
    raw = copy.deepcopy(MINIMAL)
    if fps is not None:
        raw["panel"]["fps"] = fps
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def app_for(tmp_path, link_factory=None, fps=None, **kw):
    """PanelApp with a fake transport and registry. `kw` goes straight to the constructor
    (status_path, status_period, port).

    `fps` raises the profile's cadence for the tests that COUNT frames. MINIMAL
    comes at 1 fps, so waiting for two frames is more than a thousand milliseconds
    of real clock on top of the cold start -- loading Consolas at 60 px, bringing up
    Pillow and encoding a 320x1480 JPEG. On a development machine that is plenty; on
    the CI runner it is not, and the test failed one run and passed the next with
    identical code. Counting frames should not depend on pacing.
    """
    made = []

    def factory():
        t = FakeTransport()
        made.append(t)
        return PanelLink(t)

    return PanelApp(profile(tmp_path, fps=fps),
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
    app, made = app_for(tmp_path, fps=30)
    app.start()
    try:
        assert wait_until(lambda: app.state()["frames"] >= 2, timeout=20)
        assert app.state()["running"] is True
    finally:
        app.stop()


def test_stop_ends_the_thread_and_releases_the_panel(tmp_path):
    app, made = app_for(tmp_path, fps=30)
    app.start()
    assert wait_until(lambda: app.state()["frames"] >= 1, timeout=20)
    app.stop()
    assert app.state()["running"] is False
    assert made[0].closed is True


def test_pause_stops_drawing_and_frees_the_port_then_resume_comes_back(tmp_path):
    """Pausing has to release the port, not just stop drawing: it is how the user
    hands the panel to LCD Control without closing the app."""
    app, made = app_for(tmp_path)
    app.start()
    assert wait_until(lambda: app.state()["frames"] >= 1)

    app.pause()
    assert app.paused is True
    assert made[0].closed is True
    quietos = app.state()["frames"]
    time.sleep(0.3)
    assert app.state()["frames"] == quietos       # it draws no more

    app.resume()
    try:
        assert app.paused is False
        assert wait_until(lambda: app.state()["frames"] > quietos)
        assert len(made) == 2                      # it reopened the link
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
    """Without an interruptible sleep, exiting from the tray while the engine waits
    to reconnect takes up to 10 s with the window already closed."""
    def failing():
        return PanelLink(FakeTransport(fail_on_write=OSError("port taken")))

    app, _ = app_for(tmp_path, link_factory=failing)
    app.start()
    assert wait_until(lambda: app.state()["last_error"] is not None)

    t0 = time.time()
    app.stop()
    assert time.time() - t0 < 1.5, "it sat waiting out the backoff"
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


# --- fps selectable from the tray ---

def test_set_fps_writes_the_profile_and_the_engine_picks_it_up(tmp_path):
    """The fps lives in the profile, so changing it means editing the profile: the
    engine hot-reloads it without restarting anything."""
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
    """Above the panel's refresh rate the frames are discarded: it is CPU burned
    into the void."""
    app, _ = app_for(tmp_path)
    errores = app.set_fps(120)
    assert errores and any("fps" in e for e in errores)
    assert app.fps() == 1                      # it did not write it
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
    """If the profile cannot be read, writing an fps over it would destroy it."""
    app, _ = app_for(tmp_path)
    app.profile_path.write_text("{roto", encoding="utf-8")
    errores = app.set_fps(30)
    assert errores
    assert app.profile_path.read_text(encoding="utf-8") == "{roto"


def test_the_cpu_cost_of_each_option_is_published(tmp_path):
    """The tray shows the cost beside each option: choosing 60 fps without knowing
    it is 37% of one core is not choosing."""
    app, _ = app_for(tmp_path)
    opciones = app.fps_options()
    assert [v for v, _ in opciones] == [1, 10, 30, 60]
    for valor, etiqueta in opciones:
        assert str(valor) in etiqueta
        assert "%" in etiqueta


# --- switching profile ---

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
    """Switching to an invalid profile would leave the panel with nothing to draw.
    It is validated BEFORE touching the running engine."""
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
    """Brightness lives in the profile and the engine applies it on every reload, so
    changing it needs NO restart: it is the best candidate for the menu."""
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
    """The tray needs ONE list of problems to show. The status has warnings,
    unavailable and last_error in three separate fields and the menu only looked at
    two."""
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


# --- exporting from the tray ---


def test_export_writes_a_bundle_next_to_the_project(tmp_path):
    """The tray cannot open a file dialog -- it is pure ctypes -- so it exports on
    its own to a fixed folder and says where it landed. The name carries the date so
    exporting twice overwrites nothing."""
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
    """This is called by the Win32 message pump: an exception there is seen by
    nobody (there is no Tkinter, and pythonw has no console) and leaves the tray
    mute."""
    app, _ = app_for(tmp_path)
    Path(app.profile_path).write_text("{ roto", encoding="utf-8")
    destino, mensaje = app.export_profile(carpeta=tmp_path / "s", assets_dir=tmp_path)
    assert destino is None
    assert "no" in mensaje.lower()


def test_the_export_folder_does_not_depend_on_where_the_profile_lives(tmp_path):
    """It used to be derived from the profile with parent.parent.parent, which is to
    say "three levels up". With a profile opened from the Desktop that landed in the
    users folder and the bundle appeared where nobody was going to look. Now it comes
    from where the
    paquete."""
    from vmaxpanel import cli
    app, _ = app_for(tmp_path)              # the profile lives in a flat tmp_path
    destino, _ = app.export_profile(assets_dir=tmp_path, fecha="2026-08-12")
    assert destino.parent == cli.HERE.parent / "perfiles-exportados"
    destino.unlink()                        # do not leave litter in the repo
