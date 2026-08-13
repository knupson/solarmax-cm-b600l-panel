"""El instalador de primera vez: diagnostico y tarea programada.

Todo lo que toca el sistema pasa por `runner`, que se inyecta: los tests
verifican QUE comando se manda, sin registrar nada de verdad en la maquina que
corre la suite.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vmaxpanel import install

from tests.test_schema import MINIMAL


class FakeRunner:
    """Junta los comandos y devuelve el codigo que le digan."""

    def __init__(self, code=0, salida=""):
        self.calls = []
        self.code = code
        self.salida = salida

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.code, self.salida


@pytest.fixture
def perfil(tmp_path):
    p = tmp_path / "vitals.json"
    p.write_text(json.dumps(MINIMAL), encoding="utf-8")
    return p


# --- diagnostico ---


def test_a_missing_ffmpeg_is_a_warning_not_a_failure(perfil, monkeypatch):
    """ffmpeg solo hace falta para los fondos de video. Marcarlo como falla
    dejaria al instalador negandose a instalar por algo que la enorme mayoria de
    los perfiles no usa."""
    from vmaxpanel.render import video
    monkeypatch.setattr(video, "buscar_ffmpeg", lambda: None)
    ch = {c.nombre: c for c in install.diagnosticar(perfil)}
    assert ch["ffmpeg"].ok is None                 # None = opcional, no falla
    assert not install.bloquea(install.diagnosticar(perfil))


def test_an_invalid_profile_blocks_the_install(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{ no es json", encoding="utf-8")
    checks = install.diagnosticar(roto)
    perfil_check = next(c for c in checks if c.nombre == "profile")
    assert perfil_check.ok is False
    assert install.bloquea(checks)


def test_a_panel_that_is_not_plugged_in_does_not_block(perfil, monkeypatch):
    """El panel puede estar desenchufado en el momento de instalar: la tarea
    corre al logon y el motor reintenta la conexion solo. Bloquear por esto
    obligaria a instalar en un orden particular por nada."""
    def sin_panel(port=None):
        raise install.PanelNotFound("no hay panel")
    monkeypatch.setattr(install, "_detectar_panel", sin_panel)
    checks = install.diagnosticar(perfil)
    panel = next(c for c in checks if c.nombre == "panel")
    assert panel.ok is None
    assert not install.bloquea(checks)


def test_the_diagnosis_reports_the_dependencies_it_found(perfil):
    nombres = [c.nombre for c in install.diagnosticar(perfil)]
    for esperado in ("python", "Pillow", "pyserial", "psutil", "sensors",
                     "ffmpeg", "profile", "panel"):
        assert esperado in nombres


# --- XML de la tarea ---


def test_the_task_xml_runs_the_tray_with_the_profile(perfil):
    xml = install.xml_tarea(perfil, log=Path("C:/x/vmaxpanel.log"),
                            python=Path("C:/py/pythonw.exe"), usuario="DOM\\u")
    root = ET.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:Exec/t:Command", ns).text == r"C:\py\pythonw.exe"
    args = root.find(".//t:Exec/t:Arguments", ns).text
    assert "-m vmaxpanel.tray" in args
    assert str(perfil) in args
    assert "--log" in args


def test_the_task_survives_running_on_battery(perfil):
    """Los defaults de schtasks no arrancan la tarea con la maquina a bateria y
    la matan si se desenchufa. En una notebook eso es el panel apagandose solo
    justo cuando el usuario la desconecta."""
    xml = install.xml_tarea(perfil, log=None, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\u")
    root = ET.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:DisallowStartIfOnBatteries", ns).text == "false"
    assert root.find(".//t:StopIfGoingOnBatteries", ns).text == "false"


def test_the_task_has_no_time_limit(perfil):
    """Sin esto Windows mata la tarea a las 72 horas: el panel se apagaria solo
    a los tres dias de encendida la maquina."""
    xml = install.xml_tarea(perfil, log=None, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\u")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert ET.fromstring(xml).find(".//t:ExecutionTimeLimit", ns).text == "PT0S"


def test_paths_with_ampersands_do_not_break_the_xml(tmp_path):
    """Una ruta con & rompe un XML armado con format(). Es raro pero un
    'C:\\Juegos & Cosas\\panel' no tiene por que dejar de andar."""
    raro = tmp_path / "a & b.json"
    raro.write_text(json.dumps(MINIMAL), encoding="utf-8")
    xml = install.xml_tarea(raro, log=None, python=Path("C:/py/pythonw.exe"),
                           usuario="DOM\\u")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    args = ET.fromstring(xml).find(".//t:Exec/t:Arguments", ns).text
    assert "a & b.json" in args


# --- instalar / desinstalar ---


def test_installing_registers_the_task_by_xml(perfil, tmp_path):
    r = FakeRunner()
    code, lineas = install.instalar(perfil, runner=r, log=tmp_path / "l.log")
    assert code == 0
    argv = r.calls[-1]
    assert argv[0].lower().endswith("schtasks.exe") or argv[0] == "schtasks"
    assert "/Create" in argv and "/XML" in argv
    assert install.TAREA in argv
    assert "/F" in argv                       # idempotente: reinstalar reemplaza
    assert any(install.TAREA in l for l in lineas)


def test_the_xml_file_is_utf16_and_gets_cleaned_up(perfil, tmp_path):
    """schtasks /XML solo lee Unicode: un archivo UTF-8 lo rechaza con
    'The task XML is malformed'. Y el temporal no queda tirado."""
    vistos = {}

    def runner(argv):
        ruta = Path(argv[argv.index("/XML") + 1])
        vistos["ruta"] = ruta
        vistos["bytes"] = ruta.read_bytes()
        return 0, ""

    install.instalar(perfil, runner=runner, log=tmp_path / "l.log")
    assert vistos["bytes"][:2] in (b"\xff\xfe", b"\xfe\xff")   # BOM UTF-16
    assert "vmaxpanel.tray" in vistos["bytes"].decode("utf-16")
    assert not vistos["ruta"].exists()          # el temporal no queda tirado


def test_installing_stops_before_touching_the_system_if_a_check_blocks(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{", encoding="utf-8")
    r = FakeRunner()
    code, lineas = install.instalar(roto, runner=r)
    assert code == 2
    assert r.calls == []                      # no se registro nada
    assert any("profile" in l for l in lineas)


def test_a_failing_schtasks_is_reported_not_swallowed(perfil):
    r = FakeRunner(code=1, salida="Access is denied.")
    code, lineas = install.instalar(perfil, runner=r)
    assert code == 1
    assert any("Access is denied." in l for l in lineas)


def test_uninstalling_deletes_the_task(perfil):
    r = FakeRunner()
    code, lineas = install.desinstalar(runner=r)
    assert code == 0
    assert r.calls[-1][1:] == ["/Delete", "/TN", install.TAREA, "/F"]


def test_uninstalling_a_task_that_is_not_there_is_not_an_error():
    """Desinstalar dos veces, o desinstalar sin haber instalado, no es una falla:
    el estado final es el que el usuario pidio."""
    r = FakeRunner(code=1, salida="ERROR: The system cannot find the file specified.")
    code, lineas = install.desinstalar(runner=r)
    assert code == 0
    assert any("was not" in l for l in lineas)


# --- entrada por linea de comandos ---


def test_the_cli_diagnoses_without_touching_anything(perfil, capsys, monkeypatch):
    """--diagnostico solo mira: no registra la tarea ni arranca el motor. Es lo
    que se le pide correr a alguien que dice "lo instale y no anda"."""
    from vmaxpanel import cli
    llamadas = []
    monkeypatch.setattr(install, "_correr", lambda argv: llamadas.append(argv) or (0, ""))
    code = cli.main(["--profile", str(perfil), "--diagnostico"])
    salida = capsys.readouterr().out
    assert code == 0
    assert llamadas == []
    assert "profile" in salida


def test_the_cli_install_flag_reaches_the_installer(perfil, capsys, monkeypatch):
    from vmaxpanel import cli
    vistos = {}

    def falso_instalar(p, **kw):
        vistos["p"] = p
        return 0, ["listo"]

    monkeypatch.setattr(install, "instalar", falso_instalar)
    code = cli.main(["--profile", str(perfil), "--instalar"])
    assert code == 0
    assert Path(vistos["p"]) == perfil
    assert "listo" in capsys.readouterr().out


def test_the_cli_uninstall_flag_reaches_the_installer(capsys, monkeypatch):
    from vmaxpanel import cli
    monkeypatch.setattr(install, "desinstalar", lambda **kw: (0, ["borrada"]))
    assert cli.main(["--desinstalar"]) == 0
    assert "borrada" in capsys.readouterr().out


def test_a_port_already_in_use_says_who_probably_has_it(perfil, monkeypatch):
    """Con la bandeja ya corriendo, el puerto esta tomado y pyserial contesta
    "Acceso denegado" -- que se lee como un problema de permisos y manda al
    usuario a buscar el UAC. El caso real es que el panel ya lo esta usando otra
    cosa, y eso hay que decirlo."""
    def tomado(port=None):
        raise PermissionError(13, "Acceso denegado.")
    monkeypatch.setattr(install, "_detectar_panel", tomado)
    panel = next(c for c in install.diagnosticar(perfil) if c.nombre == "panel")
    assert panel.ok is None
    assert "in use" in panel.detalle
    assert "LCD Control" in panel.detalle


def test_a_serial_exception_wrapping_the_permission_error_is_recognized(perfil,
                                                                       monkeypatch):
    """El caso real de esta maquina: pyserial no propaga PermissionError, lo
    envuelve en SerialException -- un OSError cualquiera -- con el original
    metido en el texto. Por tipo no se distingue de "no hay panel"."""
    def tomado(port=None):
        raise OSError("could not open port 'COM3': PermissionError(13, "
                      "'Acceso denegado.', None, 5)")
    monkeypatch.setattr(install, "_detectar_panel", tomado)
    panel = next(c for c in install.diagnosticar(perfil) if c.nombre == "panel")
    assert "in use" in panel.detalle


def test_the_task_runs_elevated(perfil):
    """GSA1 (temperaturas, voltajes, RPM) y el SMART de los SSD piden elevación:
    sin esto el panel arranca pero le faltan justo los sensores que no se pueden
    sacar de ningún otro lado. Es lo que tenía la tarea registrada a mano."""
    xml = install.xml_tarea(perfil, log=None, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\u")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert ET.fromstring(xml).find(".//t:RunLevel", ns).text == "HighestAvailable"


def test_a_denied_registration_says_it_needs_admin(perfil):
    """Registrar una tarea elevada necesita una consola elevada. El mensaje de
    schtasks solo dice "Access is denied", que no dice qué hacer."""
    r = FakeRunner(code=1, salida="ERROR: Access is denied.")
    code, lineas = install.instalar(perfil, runner=r)
    assert code == 1
    assert any("administrator" in l.lower() for l in lineas)


# --- bajar el panel ---


def test_stopping_stops_the_task_and_kills_the_processes():
    """`daemon/stop.ps1` no conoce al motor nuevo y no se puede tocar (es la vuelta
    atras byte-identica de toda la fase), y ademas la tarea vuelve a levantar la
    bandeja en el siguiente logon. Bajar el panel de verdad son tres cosas: parar la
    tarea, matar el proceso y matar el sidecar que se queda con el DLL tomado."""
    r = FakeRunner()
    matados = []
    code, lineas = install.parar(runner=r, matar=lambda p: matados.append(p) or True,
                                 listar=lambda: [(111, "pythonw.exe -m vmaxpanel.tray"),
                                                 (222, "powershell.exe -File sensors.ps1"),
                                                 (333, "notepad.exe")])
    assert code == 0
    argv = r.calls[0]
    assert "/End" in argv and install.TAREA in argv
    assert matados == [111, 222], "mato lo que no era, o dejo el sidecar vivo"
    assert any("111" in l for l in lineas)


def test_stopping_when_nothing_is_running_is_not_an_error():
    """Bajar algo que ya esta bajado no es una falla: el estado final es el pedido."""
    r = FakeRunner(code=1, salida="ERROR: The system cannot find the task specified.")
    code, lineas = install.parar(runner=r, matar=lambda p: True, listar=lambda: [])
    assert code == 0
    assert any("no panel processes" in l.lower() for l in lineas)
    assert any("was not registered" in l for l in lineas)


def test_stopping_does_not_touch_an_unrelated_python():
    """Un `python.exe` que no es el panel -- un script del usuario, un pytest -- no se
    puede matar por venir en la misma lista. Ya hubo un susto con eso: 14 GB de RAM de
    un proceso que era del usuario, no mio."""
    r = FakeRunner()
    matados = []
    install.parar(runner=r, matar=lambda p: matados.append(p) or True,
                  listar=lambda: [(1, "python.exe otra_cosa.py"),
                                  (2, "python.exe -m pytest"),
                                  (3, "pythonw.exe -m vmaxpanel --profile x.json")])
    assert matados == [3]


def test_the_cli_has_a_stop_flag(monkeypatch, capsys):
    from vmaxpanel import cli
    monkeypatch.setattr(install, "parar", lambda **kw: (0, ["listo"]))
    assert cli.main(["--parar"]) == 0
    assert "listo" in capsys.readouterr().out


def test_a_process_it_cannot_inspect_is_reported_not_ignored():
    """La bandeja corre ELEVADA (RunLevel HighestAvailable). Desde una consola sin
    elevar, psutil no puede leer su linea de comandos ni matarla -- y saltearla en
    silencio hacia que --parar dijera "no habia procesos" con el panel andando. Es la
    misma mentira de status que este proyecto persigue: mejor decir "hay algo que no
    puedo ver, corrleo como administrador"."""
    r = FakeRunner()
    code, lineas = install.parar(runner=r, matar=lambda p: True,
                                 listar=lambda: [(999, None)])
    assert code == 0
    texto = " ".join(lineas).lower()
    assert "administrator" in texto
    assert "999" in texto


def test_a_process_it_cannot_kill_is_reported(monkeypatch):
    r = FakeRunner()
    code, lineas = install.parar(runner=r, matar=lambda p: False,
                                 listar=lambda: [(7, "pythonw.exe -m vmaxpanel.tray")])
    texto = " ".join(lineas).lower()
    assert "could not kill" in texto or "cannot inspect" in texto
    assert "administrator" in texto


def test_missing_sensors_dll_limits_but_does_not_block(perfil, monkeypatch):
    """Verificado en un clon limpio del repo (los DLL estan gitignoreados, asi que es
    exactamente lo que recibe otro dueno del panel): SIN el DLL el panel dibuja igual
    -- reloj, carga de CPU, temp y VCORE por GSA1, RAM, discos, procesos -- y solo se
    pierden GPU, por-nucleo, temperaturas de disco y RPM.

    Marcarlo FALTA hacia que `--instalar` se negara a instalar, o sea que el que
    recibia el repo no podia arrancar NADA por unos sensores opcionales."""
    monkeypatch.setattr(install, "DLL_SENSORES", Path("no-existe/LibreHardwareMonitorLib.dll"))
    checks = install.diagnosticar(perfil)
    sensores = next(c for c in checks if c.nombre == "sensors")
    assert sensores.ok is None, "bloquea la instalacion por algo opcional"
    assert not install.bloquea(checks)


def test_the_sensors_check_says_where_to_get_the_dll(perfil, monkeypatch):
    """Decir "falta X" sin decir de donde se saca deja al que recibe el repo en el
    mismo lugar que estaba. Es el unico paso de la instalacion que no se puede
    automatizar (son DLL de terceros que no redistribuimos)."""
    monkeypatch.setattr(install, "DLL_SENSORES", Path("no-existe/LibreHardwareMonitorLib.dll"))
    detalle = next(c for c in install.diagnosticar(perfil)
                   if c.nombre == "sensors").detalle
    assert "LibreHardwareMonitor" in detalle
    assert "github" in detalle.lower()
    assert "HidSharp" in detalle, "sin HidSharp al lado, LHM.Open() falla"


def test_the_sensors_check_lists_what_is_lost_without_it(perfil, monkeypatch):
    monkeypatch.setattr(install, "DLL_SENSORES", Path("no-existe/x.dll"))
    detalle = next(c for c in install.diagnosticar(perfil)
                   if c.nombre == "sensors").detalle.lower()
    for perdido in ("gpu", "per-core", "rpm"):
        assert perdido in detalle, perdido


def test_the_task_gets_an_absolute_profile_path(tmp_path, monkeypatch):
    r"""La tarea guardaba la ruta tal cual la escribio el usuario. Con
    `--instalar --profile vmaxpanel\profiles\apex.json` eso queda RELATIVO en el XML, y
    funciona solo porque el WorkingDirectory de la tarea coincide de casualidad. Al
    logon, Windows la resuelve contra ese directorio: instalar desde otra carpeta -- o
    mover el repo -- deja la tarea apuntando a un perfil que no es. Un recien llegado
    cae en esto sin hacer nada raro."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "perfiles").mkdir()
    relativo = Path("perfiles") / "mio.json"
    (tmp_path / relativo).write_text(json.dumps(MINIMAL), encoding="utf-8")

    xml = install.xml_tarea(relativo, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\usuario")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    args = ET.fromstring(xml).find(".//t:Exec/t:Arguments", ns).text
    ruta = args.split("--profile ", 1)[1].split(" --log")[0]
    assert Path(ruta).is_absolute(), f"quedo relativa: {ruta!r}"
    assert Path(ruta) == (tmp_path / relativo).resolve()
