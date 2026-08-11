"""Smoke tests del CLI que no tocan el panel ni lanzan el sidecar.

El brief de la tarea 12 no pedia un test file para cli.py -- la verificacion
de --save/--once/hot-reload queda en los pasos manuales del brief (9 y 10).
Pero cli.py es codigo de produccion nuevo sin ninguna cobertura automatica, y
--no-sensors permite probar el camino feliz y el de un perfil roto sin abrir
el puerto serie ni levantar powershell.
"""
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
