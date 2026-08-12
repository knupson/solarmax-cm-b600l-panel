"""El archivo de estado: como se sabe desde afuera si el panel esta vivo.

Hasta ahora no habia forma. La bandeja tiene el estado en su menu, pero desde una
consola -- o desde un script, o desde otra sesion -- lo unico observable era el log
y el CPU del proceso, y de ahi a "esta dibujando" hay un salto de fe. Este archivo
lo cierra: el proceso publica su estado cada pocos segundos y `--estado` lo lee.
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
    """Se escribe con reemplazo atomico, asi que esto no deberia pasar -- pero un
    archivo truncado por un apagon, o pisado a mano, no puede tumbar al lector."""
    p = tmp_path / "estado.json"
    p.write_text('{"running": tru', encoding="utf-8")
    assert status.StatusFile(p).read() is None


def test_writing_leaves_no_temporary_files(archivo):
    for i in range(3):
        archivo.write({"frames": i})
    carpeta = archivo.path.parent
    assert [p.name for p in carpeta.iterdir()] == [archivo.path.name]


def test_a_write_that_fails_does_not_propagate(tmp_path):
    """Publicar el estado es diagnostico, no funcionalidad: un disco lleno o un
    permiso denegado no puede matar el hilo del motor ni la bandeja."""
    s = status.StatusFile(tmp_path / "sub" / "no" / "existe" / "e.json")
    assert s.write({"frames": 1}) is False        # no levanta
    assert s.read() is None


def test_a_fresh_state_is_reported_as_alive(archivo):
    archivo.write({"running": True, "frames": 30, "panel": "ok", "profile": "Apex"})
    archivo._clock.now += 3.0
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda pid: True)
    assert "hace 3 s" in texto
    assert "Apex" in texto
    assert "colgado" not in texto


def test_a_stale_state_says_the_process_may_be_stuck(archivo):
    """El proceso puede estar vivo y no publicar: un motor colgado en una escritura
    al puerto sigue existiendo. La antiguedad del archivo es la unica senal de eso,
    y por eso se dice explicitamente en vez de mostrar los numeros viejos como si
    fueran de ahora."""
    archivo.write({"running": True, "frames": 30, "panel": "ok"})
    archivo._clock.now += 90.0
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda pid: True)
    assert "hace 90 s" in texto
    assert "colgado" in texto


def test_a_dead_process_is_reported_as_dead(archivo):
    archivo.write({"running": True, "frames": 30})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda pid: False)
    assert "YA NO EXISTE" in texto


def test_describe_without_a_file_says_it_is_not_running():
    assert "no esta corriendo" in status.describe(None)


def test_problems_are_listed(archivo):
    archivo.write({"running": True, "frames": 1,
                   "problems": ["sin datos: cpu.fan", "perfil rechazado"]})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "cpu.fan" in texto
    assert "perfil rechazado" in texto


def test_a_paused_panel_is_not_reported_as_drawing(archivo):
    """Pausado suelta el puerto, que es como se le presta el panel a LCD Control.
    Confundirlo con detenido manda a reiniciar algo que no hace falta."""
    archivo.write({"running": False, "paused": True, "frames": 5})
    texto = status.describe(archivo.read(), ahora=archivo._clock(),
                            vivo=lambda p: True)
    assert "PAUSA" in texto
    assert "DETENIDO" not in texto


# --- integracion con la app ---


def test_the_app_publishes_its_state_while_it_runs(tmp_path):
    from tests.test_app import app_for, wait_until
    ruta = tmp_path / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    try:
        archivo = status.StatusFile(ruta)
        assert wait_until(lambda: (archivo.read() or {}).get("frames", 0) > 0), \
            "la app nunca publico un estado con frames"
        leido = archivo.read()
        assert leido["running"] is True
        assert leido["pid"] == os.getpid()
        assert leido["profile"]
    finally:
        app.stop()


def test_stopping_publishes_the_final_state(tmp_path):
    """Si el archivo se quedara con running=True despues de bajar, `--estado`
    mentiria justo en el caso que interesa: el panel que se apago solo."""
    from tests.test_app import app_for
    ruta = tmp_path / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    app.stop()
    leido = status.StatusFile(ruta).read()
    assert leido is not None
    assert leido["running"] is False


def test_the_app_without_a_status_path_writes_nothing(tmp_path):
    """El archivo es opt-in: los tests del motor y un `--once` de una sola pasada no
    tienen por que ensuciar el directorio."""
    from tests.test_app import app_for
    app, _ = app_for(tmp_path)
    app.start()
    app.stop()
    assert not any(p.name.startswith("estado") for p in tmp_path.iterdir())


def test_pausing_publishes_that_it_is_paused_not_that_it_stopped(tmp_path):
    """pause() llama a stop(), que publica con paused todavia en False. Si el estado
    se quedara asi, `--estado` diria DETENIDO y mandaria a reiniciar algo que esta en
    pausa a pedido del usuario."""
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
        # wait_until y no leer de una: el latido corre en su propio hilo, asi que
        # justo despues de start() el archivo puede no existir todavia.
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
    """Codigo 1 y no 2: no es un error de uso, es la respuesta 'no esta corriendo'.
    Un script tiene que poder distinguir las dos cosas."""
    from vmaxpanel import cli
    monkeypatch.setattr(cli, "status_path", lambda: tmp_path / "no-existe.json")
    assert cli.main(["--estado"]) == 1
    assert "no esta corriendo" in capsys.readouterr().out


def test_a_status_file_that_cannot_be_written_is_reported_once(tmp_path, capsys):
    """Si el archivo no se puede escribir -- carpeta de solo lectura, como pasaria
    instalado en Program Files -- `--estado` diria "no esta corriendo" para un panel
    que SI esta dibujando. Es una mentira silenciosa, y el unico lugar donde se puede
    avisar es el log. Una sola vez: el latido corre cada 5 s, para siempre."""
    from tests.test_app import app_for, wait_until
    ruta = tmp_path / "no" / "existe" / "estado.json"
    app, _ = app_for(tmp_path, status_path=ruta, status_period=0.02)
    app.start()
    try:
        # Se espera a que el motor haya dado varias vueltas: con periodo 0.02 s, si
        # el aviso se repitiera habria decenas cuando lleguemos a 5 cuadros.
        assert wait_until(lambda: app.state()["frames"] >= 5)
    finally:
        app.stop()
    salida = capsys.readouterr().err
    assert "no se pudo publicar" in salida, "no aviso que no podia escribir"
    assert salida.count("no se pudo publicar") == 1, "lo repitio en cada latido"
