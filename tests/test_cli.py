"""Smoke tests del CLI que no tocan el panel ni lanzan el sidecar.

El brief de la tarea 12 no pedia un test file para cli.py -- la verificacion
de --save/--once/hot-reload queda en los pasos manuales del brief (9 y 10).
Pero cli.py es codigo de produccion nuevo sin ninguna cobertura automatica, y
--no-sensors permite probar el camino feliz y el de un perfil roto sin abrir
el puerto serie ni levantar powershell.
"""
import pytest
from PIL import Image

from vmaxpanel import cli


def test_default_profile_path_points_at_an_existing_file():
    path = cli.default_profile_path()
    assert path.name == "vitals.json"
    assert path.is_file()


def test_save_with_no_sensors_writes_a_frame_and_exits_zero(tmp_path):
    out = tmp_path / "out.png"
    rc = cli.main(["--save", str(out), "--no-sensors"])
    assert rc == 0
    im = Image.open(out)
    assert im.size == (320, 1480)


def test_a_missing_profile_exits_with_an_error_instead_of_a_traceback(tmp_path):
    missing = tmp_path / "no-existe.json"
    rc = cli.main(["--profile", str(missing), "--save", str(tmp_path / "out.png"),
                   "--no-sensors"])
    assert rc == 2


def test_log_redirects_stdout_and_stderr_to_the_file(tmp_path):
    """La tarea programada corre con pythonw.exe, que no tiene consola: sin
    --log, un motor que muere al logon deja el panel negro y ningun rastro
    de por que."""
    log = tmp_path / "panel.log"
    out = tmp_path / "out.png"
    rc = cli.main(["--save", str(out), "--no-sensors", "--log", str(log)])
    assert rc == 0
    assert "saved" in log.read_text(encoding="utf-8")


def test_log_appends_instead_of_truncating(tmp_path):
    """Un reinicio no puede borrar la evidencia de la corrida anterior."""
    log = tmp_path / "panel.log"
    log.write_text("corrida previa\n", encoding="utf-8")
    cli.main(["--save", str(tmp_path / "a.png"), "--no-sensors", "--log", str(log)])
    assert "corrida previa" in log.read_text(encoding="utf-8")


def test_log_captures_the_error_of_a_broken_profile(tmp_path):
    log = tmp_path / "panel.log"
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json", encoding="utf-8")
    rc = cli.main(["--profile", str(broken), "--save", str(tmp_path / "o.png"),
                   "--no-sensors", "--log", str(log)])
    assert rc == 2
    assert "layout:" in log.read_text(encoding="utf-8")


def test_log_captures_a_traceback_from_an_unexpected_crash(tmp_path, monkeypatch):
    """Un TypeError adentro del render -- la clase de fallo que la revision
    final encontro -- se escapa hasta matar el proceso. Con pythonw el
    traceback no tiene donde imprimirse, asi que tiene que quedar en el log
    antes de que se restaure stderr."""
    log = tmp_path / "panel.log"

    def boom(*args, **kw):
        raise RuntimeError("explosion de prueba")

    monkeypatch.setattr(cli, "Renderer", boom)
    with pytest.raises(RuntimeError):
        cli.main(["--save", str(tmp_path / "o.png"), "--no-sensors", "--log", str(log)])
    text = log.read_text(encoding="utf-8")
    assert "Traceback" in text and "explosion de prueba" in text
