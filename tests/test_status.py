"""The status file: how you tell from outside whether the panel is alive.

There used to be no way. The tray has the status in its menu, but from a console
-- or a script, or another session -- the only observable things were the log and
the process's CPU usage, and going from there to "it is drawing" is a leap of
faith. This file closes that: the process publishes its status every few seconds
and `--status` reads it.
"""
import os

import pytest

from vmaxpanel import status


class Reloj:
    def __init__(self, t=1000.0):
        self.now = t

    def __call__(self):
        return self.now


@pytest.fixture
def archivo(tmp_path):
    return status.StatusFile(tmp_path / "estado.json", clock=Reloj())


def test_writing_and_reading_roundtrips(archivo):
    archivo.write({"running": True, "frames": 12, "panel": "ok"})
    leido = archivo.read()
    assert leido["frames"] == 12
    assert leido["pid"] == os.getpid()
    assert leido["ts"] == 1000.0


def test_reading_a_missing_file_is_none_not_an_exception(tmp_path):
    assert status.StatusFile(tmp_path / "no-existe.json").read() is None


def test_reading_a_half_written_file_is_none_not_a_crash(tmp_path):
    """It is written with an atomic replace, so this should not happen -- but a file
    truncated by a power cut, or overwritten by hand, must not bring the reader
    down."""
    p = tmp_path / "estado.json"
    p.write_text('{"running": tru', encoding="utf-8")
    assert status.StatusFile(p).read() is None


def test_writing_leaves_no_temporary_files(archivo):
    for i in range(3):
        archivo.write({"frames": i})
    carpeta = archivo.path.parent
    assert [p.name for p in carpeta.iterdir()] == [archivo.path.name]


def test_a_write_that_fails_does_not_propagate(tmp_path):
    """Publishing the status is diagnostics, not functionality: a full disk or a
    denied permission must not kill the engine thread or the tray."""
    s = status.StatusFile(tmp_path / "sub" / "no" / "existe" / "e.json")
    assert s.write({"frames": 1}) is False        # no levanta
    assert s.read() is None


def test_a_fresh_state_is_reported_as_alive(archivo):
    archivo.write({"running": True, "frames": 30, "panel": "ok", "profile": "Apex"})
    archivo._clock.now += 3.0
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda pid: True)
    assert "3 s ago" in texto
    assert "Apex" in texto
    assert "colgado" not in texto


def test_a_stale_state_says_the_process_may_be_stuck(archivo):
    """The process can be alive and not publishing: an engine wedged in a write to
    the port still exists. The file's age is the only signal of that, which is why
    it is stated explicitly rather than showing the old numbers as if
    fueran de ahora."""
    archivo.write({"running": True, "frames": 30, "panel": "ok"})
    archivo._clock.now += 90.0
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda pid: True)
    assert "90 s ago" in texto
    assert "stuck" in texto


def test_a_dead_process_is_reported_as_dead(archivo):
    archivo.write({"running": True, "frames": 30})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda pid: False)
    assert "NO LONGER EXISTS" in texto


def test_describe_without_a_file_says_it_is_not_running():
    assert "is not running" in status.describe(None)


def test_problems_are_listed(archivo):
    archivo.write({"running": True, "frames": 1,
                   "problems": ["no data: cpu.fan", "profile rejected"]})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "cpu.fan" in texto
    assert "profile rejected" in texto


def test_reconnections_are_reported_because_the_panel_restarts_on_each_one(archivo):
    """Every reconnection re-sends the handshake and the panel shows that as a
    restart. The engine counted them from the start and never published the
    number, so "the panel restarted several times" had no observable to check
    against."""
    archivo.write({"running": True, "frames": 300, "panel": "ok", "reconnects": 4})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "4 reconnection" in texto


def test_no_reconnections_says_nothing_at_all(archivo):
    """A "0 reconnections" on every healthy run is noise, and this output is only
    useful while everything printed in it is worth reading."""
    archivo.write({"running": True, "frames": 300, "panel": "ok", "reconnects": 0})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "reconnection" not in texto
    # and the same with an old status file, written before the field existed
    archivo.write({"running": True, "frames": 300, "panel": "ok"})
    assert "reconnection" not in status.describe(archivo.read(),
                                                 ahora=archivo._clock(),
                                                 vivo=lambda p: True)


def test_a_paused_panel_is_not_reported_as_drawing(archivo):
    """Paused releases the port, which is how the panel is lent to LCD Control.
    Confusing it with stopped sends the user to restart something that is fine."""
    archivo.write({"running": False, "paused": True, "frames": 5})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "PAUSED" in texto
    assert "STOPPED" not in texto


# --- integration with the app ---


def test_the_app_publishes_its_state_while_it_runs(tmp_path):
    from tests.test_app import app_for, wait_until
    ruta = tmp_path / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    try:
        archivo = status.StatusFile(ruta)
        assert wait_until(lambda: (archivo.read() or {}).get("frames", 0) > 0), \
            "the app never published a status with frames"
        leido = archivo.read()
        assert leido["running"] is True
        assert leido["pid"] == os.getpid()
        assert leido["profile"]
    finally:
        app.stop()


def test_stopping_publishes_the_final_state(tmp_path):
    """If the file were left with running=True after shutting down, `--status` would
    lie in exactly the case that matters: the panel that switched itself off."""
    from tests.test_app import app_for
    ruta = tmp_path / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    app.stop()
    leido = status.StatusFile(ruta).read()
    assert leido is not None
    assert leido["running"] is False


def test_the_app_without_a_status_path_writes_nothing(tmp_path):
    """The file is opt-in: the engine tests and a single-pass `--once` have no reason
    to litter the directory."""
    from tests.test_app import app_for
    app, _ = app_for(tmp_path)
    app.start()
    app.stop()
    assert not any(p.name.startswith("estado") for p in tmp_path.iterdir())


def test_pausing_publishes_that_it_is_paused_not_that_it_stopped(tmp_path):
    """pause() calls stop(), which publishes with paused still False. If the status
    were left that way, `--status` would say STOPPED and send the user to restart
    something that is paused at their own request."""
    from tests.test_app import app_for
    ruta = tmp_path / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    app.pause()
    leido = status.StatusFile(ruta).read()
    assert leido["paused"] is True
    assert leido["running"] is False


def test_the_published_state_carries_the_problems(tmp_path):
    from tests.test_app import app_for, wait_until
    ruta = tmp_path / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    try:
        # wait_until and not an immediate read: the heartbeat runs on its own
        # thread, so right after start() the file may not exist yet.
        archivo = status.StatusFile(ruta)
        assert wait_until(lambda: archivo.read() is not None)
        assert "problems" in archivo.read()
    finally:
        app.stop()


# --- entrada por linea de comandos ---


def test_the_cli_reports_a_running_panel_with_code_zero(tmp_path, capsys, monkeypatch):
    from vmaxpanel import cli
    ruta = tmp_path / "estado.json"
    status.StatusFile(ruta).write({"running": True, "profile": "Apex", "frames": 9,
                                   "panel": "ok"})
    monkeypatch.setattr(cli, "status_path", lambda: ruta)
    monkeypatch.setattr(status, "_vivo", lambda pid: True)
    assert cli.main(["--estado"]) == 0
    assert "Apex" in capsys.readouterr().out


def test_the_cli_returns_one_when_the_panel_is_not_running(tmp_path, capsys,
                                                          monkeypatch):
    """Code 1 and not 2: this is not a usage error, it is the answer "it is not
    running". A script has to be able to tell the two apart."""
    from vmaxpanel import cli
    monkeypatch.setattr(cli, "status_path", lambda: tmp_path / "no-existe.json")
    assert cli.main(["--estado"]) == 1
    assert "is not running" in capsys.readouterr().out


def test_a_status_file_that_cannot_be_written_is_reported_once(tmp_path, capsys):
    """If the file cannot be written -- a read-only folder, as would happen installed
    in Program Files -- `--status` would say "is not running" for a panel that IS
    drawing. That is a silent lie, and the only place a warning can go is the log.
    Once only: the heartbeat runs every 5 s, forever."""
    from tests.test_app import app_for, wait_until
    ruta = tmp_path / "no" / "existe" / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    try:
        # It waits for the engine to have gone round several times: at a 0.02 s
        # period, if the warning repeated there would be dozens by 5 frames.
        assert wait_until(lambda: app.state()["frames"] >= 5)
    finally:
        app.stop()
    salida = capsys.readouterr().err
    assert "could not publish" in salida, "it did not warn that it could not write"
    assert salida.count("could not publish") == 1, "it repeated on every heartbeat"


def test_the_fps_is_shown_without_a_pointless_decimal(archivo):
    """The model stores fps as a float on purpose (0.5 = one frame every two seconds
    is a valid cadence), but "30.0 fps" on screen is the internal type showing
    through. It is formatted at display time, which is where it belongs -- not by
    changing the contract over something cosmetic."""
    archivo.write({"running": True, "profile": "Apex", "frames": 9, "fps": 30.0})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "30 fps" in texto
    assert "30.0" not in texto


def test_a_fractional_fps_keeps_its_decimal(archivo):
    """And the other way round: if it really is 0.5, it has to show."""
    archivo.write({"running": True, "frames": 1, "fps": 0.5})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "0.5 fps" in texto
