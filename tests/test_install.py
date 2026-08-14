"""El instalador de primera vez: diagnostico y tarea programada.

Everything touching the system goes through `runner`, which is injected: the
tests verify WHICH command is sent, without registering anything for real on the
machine running the suite.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vmaxpanel import install

from tests.test_schema import MINIMAL


class FakeRunner:
    """Collects the commands and returns whatever code it is told to."""

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
    """ffmpeg is only needed for video backgrounds. Marking it a failure would leave
    the installer refusing to install over something the vast majority of profiles
    do not use."""
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
    """The panel can be unplugged at install time: the task runs at logon and the
    engine retries the connection on its own. Blocking over this would force a
    particular installation order for no reason."""
    def sin_panel(port=None):
        raise install.PanelNotFound("no panel")
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


# --- the task XML ---


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
    """The schtasks defaults refuse to start the task on battery and kill it when
    the machine is unplugged. On a laptop that is the panel switching itself off
    exactly when the user unplugs it."""
    xml = install.xml_tarea(perfil, log=None, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\u")
    root = ET.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:DisallowStartIfOnBatteries", ns).text == "false"
    assert root.find(".//t:StopIfGoingOnBatteries", ns).text == "false"


def test_the_task_has_no_time_limit(perfil):
    """Without this Windows kills the task after 72 hours: the panel would switch
    itself off three days after the machine was turned on."""
    xml = install.xml_tarea(perfil, log=None, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\u")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert ET.fromstring(xml).find(".//t:ExecutionTimeLimit", ns).text == "PT0S"


def test_paths_with_ampersands_do_not_break_the_xml(tmp_path):
    """A path with & breaks an XML built with format(). It is unusual, but a
    'C:\\Games & Things\\panel' has no reason to stop working."""
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
    """schtasks /XML only reads Unicode: it rejects a UTF-8 file with 'The task XML
    is malformed'. And the temp file is not left behind."""
    vistos = {}

    def runner(argv):
        ruta = Path(argv[argv.index("/XML") + 1])
        vistos["ruta"] = ruta
        vistos["bytes"] = ruta.read_bytes()
        return 0, ""

    install.instalar(perfil, runner=runner, log=tmp_path / "l.log")
    assert vistos["bytes"][:2] in (b"\xff\xfe", b"\xfe\xff")   # BOM UTF-16
    assert "vmaxpanel.tray" in vistos["bytes"].decode("utf-16")
    assert not vistos["ruta"].exists()          # the temp file is not left behind


def test_installing_stops_before_touching_the_system_if_a_check_blocks(tmp_path):
    roto = tmp_path / "roto.json"
    roto.write_text("{", encoding="utf-8")
    r = FakeRunner()
    code, lineas = install.instalar(roto, runner=r)
    assert code == 2
    assert r.calls == []                      # nothing was registered
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
    """Uninstalling twice, or uninstalling without having installed, is not a
    failure: the final state is the one the user asked for."""
    r = FakeRunner(code=1, salida="ERROR: The system cannot find the file specified.")
    code, lineas = install.desinstalar(runner=r)
    assert code == 0
    assert any("was not" in l for l in lineas)


# --- entrada por linea de comandos ---


def test_the_cli_diagnoses_without_touching_anything(perfil, capsys, monkeypatch):
    """--diagnose only looks: it neither registers the task nor starts the engine.
    It is what you ask somebody to run when they say "I installed it and it does not
    work"."""
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
    """With the tray already running, the port is taken and pyserial answers "Access
    denied" -- which reads as a permissions problem and sends the user looking for
    UAC. The real case is that something else is already using the panel, and that
    is what has to be said."""
    def tomado(port=None):
        raise PermissionError(13, "Acceso denegado.")
    monkeypatch.setattr(install, "_detectar_panel", tomado)
    panel = next(c for c in install.diagnosticar(perfil) if c.nombre == "panel")
    assert panel.ok is None
    assert "in use" in panel.detalle
    assert "LCD Control" in panel.detalle


def test_a_serial_exception_wrapping_the_permission_error_is_recognized(perfil,
                                                                       monkeypatch):
    """The real case: pyserial does not propagate PermissionError, it wraps it in a
    SerialException -- just an OSError -- with the original tucked into the text. By
    type it is indistinguishable from "no panel"."""
    def tomado(port=None):
        raise OSError("could not open port 'COM3': PermissionError(13, "
                      "'Acceso denegado.', None, 5)")
    monkeypatch.setattr(install, "_detectar_panel", tomado)
    panel = next(c for c in install.diagnosticar(perfil) if c.nombre == "panel")
    assert "in use" in panel.detalle


def test_the_task_runs_elevated(perfil):
    """GSA1 (temperatures, voltages, RPM) and the SSDs' SMART data need elevation:
    without this the panel starts but is missing exactly the sensors that cannot be
    got from anywhere else."""
    xml = install.xml_tarea(perfil, log=None, python=Path("C:/py/pythonw.exe"),
                            usuario="DOM\\u")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert ET.fromstring(xml).find(".//t:RunLevel", ns).text == "HighestAvailable"


def test_a_denied_registration_says_it_needs_admin(perfil):
    """Registering an elevated task needs an elevated console. The schtasks message
    only says "Access is denied", which does not say what to do."""
    r = FakeRunner(code=1, salida="ERROR: Access is denied.")
    code, lineas = install.instalar(perfil, runner=r)
    assert code == 1
    assert any("administrator" in l.lower() for l in lineas)


# --- bringing the panel down ---


def test_stopping_stops_the_task_and_kills_the_processes():
    """`daemon/stop.ps1` does not know the new engine and cannot be touched (it is
    the byte-identical rollback path), and on top of that the task starts the tray
    again at the next logon. Really bringing the panel down is three things: stop the
    task, kill the process, and kill the sidecar that holds the DLL."""
    r = FakeRunner()
    matados = []
    code, lineas = install.parar(runner=r, matar=lambda p: matados.append(p) or True,
                                 listar=lambda: [(111, "pythonw.exe -m vmaxpanel.tray"),
                                                 (222, "powershell.exe -File sensors.ps1"),
                                                 (333, "notepad.exe")])
    assert code == 0
    argv = r.calls[0]
    assert "/End" in argv and install.TAREA in argv
    assert matados == [111, 222], "it killed the wrong thing, or left the sidecar alive"
    assert any("111" in l for l in lineas)


def test_stopping_when_nothing_is_running_is_not_an_error():
    """Bringing down something already down is not a failure: the final state is the
    one asked for."""
    r = FakeRunner(code=1, salida="ERROR: The system cannot find the task specified.")
    code, lineas = install.parar(runner=r, matar=lambda p: True, listar=lambda: [])
    assert code == 0
    assert any("no panel processes" in l.lower() for l in lineas)
    assert any("was not registered" in l for l in lineas)


def test_stopping_does_not_touch_an_unrelated_python():
    """A `python.exe` that is not the panel -- one of the user's scripts, a pytest --
    must not be killed just for appearing in the same list. There was already a scare
    over that: 14 GB of RAM belonging to a process that was the user's."""
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
    """The tray runs ELEVATED (RunLevel HighestAvailable). From an unelevated
    console, psutil can neither read its command line nor kill it -- and skipping it
    silently made --stop say "there were no processes" with the panel running. It is
    the same lying status this project chases: better to say "there is something I
    cannot see, run this as administrator"."""
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
    """Verified on a clean clone of the repo (the DLLs are gitignored, so it is
    exactly what another owner of the panel receives): WITHOUT the DLL the panel
    draws anyway -- clock, CPU load, temperature and VCORE via GSA1, RAM, disks,
    processes -- and all that is
    pierden GPU, por-nucleo, temperaturas de disco y RPM.

    Marking it MISSING made `--install` refuse to install, which meant whoever
    received the repo could not start ANYTHING because of some optional sensors."""
    monkeypatch.setattr(install, "DLL_SENSORES", Path("no-existe/LibreHardwareMonitorLib.dll"))
    checks = install.diagnosticar(perfil)
    sensores = next(c for c in checks if c.nombre == "sensors")
    assert sensores.ok is None, "it blocks the install over something optional"
    assert not install.bloquea(checks)


def test_the_sensors_check_says_where_to_get_the_dll(perfil, monkeypatch):
    """Saying "X is missing" without saying where to get it leaves whoever received
    the repo exactly where they were. It is the one installation step that cannot be
    automated (they are third-party DLLs that are not redistributed here)."""
    monkeypatch.setattr(install, "DLL_SENSORES", Path("no-existe/LibreHardwareMonitorLib.dll"))
    detalle = next(c for c in install.diagnosticar(perfil)
                   if c.nombre == "sensors").detalle
    assert "LibreHardwareMonitor" in detalle
    assert "github" in detalle.lower()
    assert "HidSharp" in detalle, "without HidSharp beside it, LHM.Open() fails"


def test_the_sensors_check_lists_what_is_lost_without_it(perfil, monkeypatch):
    monkeypatch.setattr(install, "DLL_SENSORES", Path("no-existe/x.dll"))
    detalle = next(c for c in install.diagnosticar(perfil)
                   if c.nombre == "sensors").detalle.lower()
    for perdido in ("gpu", "per-core", "rpm"):
        assert perdido in detalle, perdido


def test_the_task_gets_an_absolute_profile_path(tmp_path, monkeypatch):
    r"""The task used to store the path exactly as the user typed it. With
    `--install --profile vmaxpanel\profiles\apex.json` that stays RELATIVE in the
    XML, and works only because the task's WorkingDirectory happens to match. At
    logon, Windows resolves it against that directory: installing from another folder
    -- or moving the repo -- leaves the task pointing at a profile that is not the
    right one. A newcomer hits this without doing anything odd."""
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


def test_stop_does_not_kill_the_process_that_is_running_it():
    """The sweep matched its own command line and terminated itself mid-run.

    es_mio() excluded the caller by looking for the literal "--parar". The English
    alias --stop shipped later, so `python -m vmaxpanel --stop` matched "-m
    vmaxpanel", killed its own pid, and died at exit 15 before printing the report
    -- and before it got to the sidecar, which is the whole reason the command
    exists: a surviving sensors.ps1 holds LibreHardwareMonitorLib.dll and blocks
    the directory.

    A flag string is a proxy for "this is me". The pid is the fact.
    """
    import os
    yo = os.getpid()
    muertos = []
    procesos = [(yo, f"python.exe -m vmaxpanel --stop"),
                (4321, "pythonw.exe -u -m vmaxpanel.tray --profile apex.json")]

    code, lineas = install.parar(
        runner=lambda cmd: (0, ""),
        matar=lambda pid: (muertos.append(pid), True)[1],
        listar=lambda: procesos)

    assert yo not in muertos, "--stop killed the process running it"
    assert 4321 in muertos, "it must still kill the real panel processes"


def test_stop_recognises_its_own_process_by_either_spelling():
    """Both flag spellings are live aliases, so neither may be the thing that
    identifies the caller."""
    import os
    for grafia in ("--stop", "--parar"):
        muertos = []
        install.parar(runner=lambda cmd: (0, ""),
                      matar=lambda pid: (muertos.append(pid), True)[1],
                      listar=lambda: [(os.getpid(), f"python.exe -m vmaxpanel {grafia}")])
        assert os.getpid() not in muertos, grafia


def test_uninstalling_a_task_that_is_not_there_is_idempotent_in_any_language():
    """It decided "the task does not exist" by looking for "cannot find" or "no
    existe" in schtasks' output. On a German, French or Japanese Windows neither
    matches, so removing an unregistered task reported a failure and exited
    non-zero -- breaking the idempotence the command documents, and any script
    that runs uninstall before install.

    The exit code of `schtasks /Query /TN` says the same thing in every language.
    """
    llamadas = []

    def runner_aleman(cmd):
        llamadas.append(cmd)
        if "/Delete" in cmd:
            return 1, "FEHLER: Der angegebene Task existiert nicht."
        if "/Query" in cmd:
            return 1, "FEHLER: Der angegebene Task existiert nicht."
        return 0, ""

    code, lineas = install.desinstalar(runner=runner_aleman)
    assert code == 0, lineas
    assert any("/Query" in c for c in llamadas), "it never asked whether it exists"


def test_a_delete_that_fails_for_a_real_reason_still_reports_failure():
    """The idempotence must not swallow a genuine error: if the task IS there and
    deleting it fails, that is a failure."""
    def runner(cmd):
        if "/Delete" in cmd:
            return 1, "ERROR: Access is denied."
        return 0, ""            # /Query succeeds: the task exists

    code, lineas = install.desinstalar(runner=runner)
    assert code != 0, lineas


def test_the_diagnostic_names_the_metrics_this_machine_cannot_serve(tmp_path):
    """Apex binds 22 of its 60 metric widgets to this author's hardware: six cores,
    three volumes, three SSDs, an Ethernet adapter. On a machine with one disk and
    four cores a third of the panel comes up blank, and nothing said why -- the
    diagnostic reported "116 widgets" and called it fine.

    Optional and never blocking: a profile with blanks still runs, and the answer
    is to pick another profile or edit this one, not to refuse to install.
    """
    perfil = tmp_path / "p.json"
    perfil.write_text(json.dumps({
        "version": 1, "name": "Test",
        "designed_for": {"width": 320, "height": 1480},
        "panel": {"rotate": 180, "brightness": 100, "fps": 1,
                  "jpeg_quality": 82},
        "background": {"type": "solid", "color": "#000000"},
        "fonts": {"f": {"family": "Consolas", "size": 12}},
        "widgets": [
            {"id": "a", "type": "text", "metric": "cpu.load", "x": 0, "y": 0,
             "font": "f", "color": "#FFFFFF", "format": "{:.0f}"},
            {"id": "b", "type": "text", "metric": "vol.Z.used", "x": 0, "y": 20,
             "font": "f", "color": "#FFFFFF", "format": "{:.0f}"},
            {"id": "c", "type": "text", "metric": "core.9.temp", "x": 0, "y": 40,
             "font": "f", "color": "#FFFFFF", "format": "{:.0f}"},
        ]}), encoding="utf-8")

    class RegistroFalso:
        def catalog(self):
            return {"cpu.load": object()}
        def close(self):
            pass

    checks = install.diagnosticar(perfil, port="NONE",
                                  registro=lambda: (RegistroFalso(), None))
    m = [c for c in checks if c.nombre == "metrics"]
    assert m, "the diagnostic says nothing about metrics"
    assert m[0].ok is None, "it must not block the install"
    assert "vol.Z.used" in m[0].detalle and "core.9.temp" in m[0].detalle
    assert "cpu.load" not in m[0].detalle


def test_the_diagnostic_is_quiet_when_every_metric_resolves(tmp_path):
    perfil = tmp_path / "p.json"
    perfil.write_text(json.dumps({
        "version": 1, "name": "Test",
        "designed_for": {"width": 320, "height": 1480},
        "panel": {"rotate": 180, "brightness": 100, "fps": 1,
                  "jpeg_quality": 82},
        "background": {"type": "solid", "color": "#000000"},
        "fonts": {"f": {"family": "Consolas", "size": 12}},
        "widgets": [{"id": "a", "type": "text", "metric": "cpu.load", "x": 0,
                     "y": 0, "font": "f", "color": "#FFFFFF", "format": "{:.0f}"}],
    }), encoding="utf-8")

    class RegistroFalso:
        def catalog(self):
            return {"cpu.load": object()}
        def close(self):
            pass

    checks = install.diagnosticar(perfil, port="NONE",
                                  registro=lambda: (RegistroFalso(), None))
    m = [c for c in checks if c.nombre == "metrics"][0]
    assert m.ok is True, m.detalle
