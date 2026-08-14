"""Instalador de primera vez: diagnostico y autostart.

Dos cosas, y ninguna necesita administrador:

1. **Diagnostics.** It says whether the dependencies, the sensor DLL, the profile
   and the panel are there, and separates what prevents operation from what merely
   limits it (no ffmpeg -> no video backgrounds; the panel unplugged -> the engine
   retries on its own). It is the answer to "I installed it and it does not work",
   answered before the question exists.
2. **Autostart.** It registers the scheduled task that starts the tray at
   iniciar sesion.

**Why a scheduled task and not a service.** A service runs in session 0: from
there it cannot show a tray icon or open the editor. **Why XML and not `schtasks
/Create /SC ONLOGON`.** The schtasks defaults refuse to start the task on battery,
kill it when the machine is unplugged, and stop it after 72 hours. On a laptop
that is the panel switching itself off; XML is the only way to disable all three.

Everything touching the system goes through `runner`, which is injected: that way
the whole installer is tested without registering anything.
"""
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .layout import loader
from .transport.panel_link import PanelLink, PanelNotFound

HERE = Path(__file__).resolve().parent
TAREA = "PanelVitals"
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DLL_SENSORES = HERE / "lib" / "LibreHardwareMonitorLib.dll"
SIN_VENTANA = 0x08000000


@dataclass
class Chequeo:
    """One diagnostic item.

    `ok` has three states on purpose: True works, False prevents operation and None
    is optional -- something is missing that limits but does not break. A boolean
    would force a choice between blocking the install over ffmpeg and not
    mentioning it at all, and both options are worse.
    """
    nombre: str
    ok: bool | None
    detalle: str

    @property
    def marca(self) -> str:
        return {True: "ok", False: "MISSING", None: "optional"}[self.ok]


def bloquea(checks) -> bool:
    """Something prevents operation. The optional ones (ok None) do not count."""
    return any(c.ok is False for c in checks)


def _modulo(paquete, importable, para) -> Chequeo:
    """The name shown is the pip one, not the import one.

    They differ in exactly the two most confusing cases: `pip install Pillow` gives
    `import PIL`, and `pip install pyserial` gives `import serial`. A diagnostic
    saying "PIL is missing" sends the user looking for a package that does not
    exist.
    """
    try:
        mod = __import__(importable)
    except ImportError:
        return Chequeo(paquete, False, f"missing: pip install {paquete} ({para})")
    v = getattr(mod, "__version__", "?")
    return Chequeo(paquete, True, f"{v} - {para}")


# The one installation step that cannot be automated: they are third-party DLLs
# (MPL-2.0 and MIT) that this repo does not redistribute. Saying "X is missing"
# without saying where to get it leaves whoever received the repo exactly where
# they were.
COMO_SENSORES = (
    "optional. Without it the panel still works (clock, CPU load, temperature and "
    "VCORE via GSA1, RAM, disks, processes) but there is NO GPU, no per-core "
    "temperature, no package power, no disk temperatures and no fan RPM. To get "
    "them: download LibreHardwareMonitor from https://github.com/LibreHardwareMonitor/"
    "LibreHardwareMonitor/releases and copy LibreHardwareMonitorLib.dll AND "
    "HidSharp.dll (both: without HidSharp beside it, Open() fails) into vmaxpanel/lib/")

EN_USO = ("the port is in use: another process already has it (the tray running, "
          "or LCD Control). Close whichever is spare; only one at a time can "
          "drive the panel.")

# By text and not by type: pyserial wraps the error in a SerialException -- which
# is just an OSError -- and puts the original PermissionError inside the message.
# So the errno never arrives and the type distinguishes nothing.
_TOMADO = ("permissionerror", "acceso denegado", "access is denied", "errno 13")


def _puerto_tomado(e) -> bool:
    """The port exists but another process has it.

    Worth distinguishing: "Access denied" reads as a permissions problem and sends
    the user off to fight UAC, when the real case -- every single time -- is that
    the tray is already running or LCD Control was left open.
    """
    if isinstance(e, PermissionError):
        return True
    texto = str(e).lower()
    return any(t in texto for t in _TOMADO)


def _detectar_panel(port=None):
    """Opens and closes the panel. Separate so it can be substituted in the tests:
    the machine running the suite has no panel plugged in."""
    link = PanelLink.autodetect(port)
    try:
        link.open()
        return link.serial_number or "no serial number"
    finally:
        link.close()


def diagnosticar(profile_path, port=None) -> list:
    """Everything the panel needs in order to work, as a list."""
    checks = [
        Chequeo("python", sys.version_info >= (3, 11),
                f"{sys.version.split()[0]} at {sys.executable}"),
        _modulo("Pillow", "PIL", "renders the frames"),
        _modulo("pyserial", "serial", "the panel's port"),
        _modulo("psutil", "psutil", "CPU, RAM, network and disks"),
    ]
    checks.append(
        # ok None and not False: verified on a clean clone of the repo -- the DLLs are
        # gitignored, so that is what another owner of the panel receives -- WITHOUT
        # the DLL the
        # panel dibuja igual: reloj, carga de CPU, temp y VCORE (esos salen de GSA1, no
        # from the DLL), RAM, disks with real sizes, processes. Marking it MISSING
        # made --install refuse to install, which meant whoever received the repo
        # could not
        # arrancar NADA por unos sensores opcionales.
        Chequeo("sensors", True if DLL_SENSORES.exists() else None,
                f"{DLL_SENSORES}" if DLL_SENSORES.exists() else COMO_SENSORES))

    from .render import video
    ffmpeg = video.buscar_ffmpeg()
    checks.append(Chequeo("ffmpeg", True if ffmpeg else None,
                          ffmpeg or f"only for video backgrounds. {video.COMO_INSTALAR}"))

    ruta = Path(profile_path)
    try:
        lay = loader.load(ruta)
        checks.append(Chequeo("profile", True,
                              f"{ruta.name}: {lay.name!r}, {len(lay.widgets)} widgets"))
    except loader.LayoutError as e:
        checks.append(Chequeo("profile", False, f"{ruta.name}: {'; '.join(e.errors)}"))
    except OSError as e:
        checks.append(Chequeo("profile", False, f"could not read {ruta}: {e}"))

    try:
        checks.append(Chequeo("panel", True, _detectar_panel(port)))
    except (PanelNotFound, OSError) as e:
        # ok None and not False: the task runs at logon and the engine retries the
        # connection on its own, so installing with the panel unplugged is
        # legitimate.
        detalle = (EN_USO if _puerto_tomado(e) else
                   f"{e}. The tray keeps retrying until it shows up.")
        checks.append(Chequeo("panel", None, detalle))
    return checks


# --- tarea programada ---


def _pythonw() -> Path:
    """The console-less interpreter. With python.exe the task would open a black
    window at every logon; if it is absent, the current one is used and the window
    shows."""
    candidato = Path(sys.executable).with_name("pythonw.exe")
    return candidato if candidato.exists() else Path(sys.executable)


def _usuario() -> str:
    dominio = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or "."
    return f"{dominio}\\{os.environ.get('USERNAME', '')}"


def xml_tarea(profile_path, log=None, python=None, usuario=None,
              working_dir=None) -> str:
    """The task XML, ready for `schtasks /Create /XML`.

    A pure function: it does not read the environment when given the values, so the
    XML can be verified in full without depending on the machine the tests run on.
    """
    python = Path(python) if python else _pythonw()
    usuario = usuario if usuario is not None else _usuario()
    cwd = Path(working_dir) if working_dir else HERE.parent
    # resolve(): the path goes into the XML ABSOLUTE. Storing it as the user typed it
    # (`--profile vmaxpanel\profiles\apex.json`) leaves it relative, and Windows
    # resolves it at logon against the task's WorkingDirectory: it works by accident
    # when they coincide, and points somewhere else if it was installed from another
    # folder or if the repo moved. A newcomer hits that without doing anything odd.
    args = ["-u", "-m", "vmaxpanel.tray", "--profile",
            str(Path(profile_path).resolve())]
    if log:
        args += ["--log", str(Path(log))]
    # escape() and not hand-rolled format(): a path with & or < breaks the XML, and
    # "C:\Games & Things" is a perfectly legal path.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="{NS}">
  <RegistrationInfo>
    <Description>VMax Panel: drives the Solarmax CM-B600L case LCD panel.</Description>
    <URI>\\{TAREA}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(usuario)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(usuario)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <!-- Elevated, and not for convenience: GSA1 (temperatures, voltages, RPM)
           and the SSDs' SMART data cannot be read without elevation. Without this
           the panel still starts but is missing exactly the sensors that come from
           nowhere else. It is also what registering it requires: an elevated
           console. -->
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(python))}</Command>
      <Arguments>{escape(' '.join(args))}</Arguments>
      <WorkingDirectory>{escape(str(cwd))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks() -> str:
    """Ruta absoluta a schtasks.exe.

    Absolute and not the bare name: PATH may have another schtasks ahead of it, and
    this registers something that will run at every logon.
    """
    raiz = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(raiz) / "System32" / "schtasks.exe")


def _correr(argv):
    p = subprocess.run(argv, capture_output=True, text=True,
                       creationflags=SIN_VENTANA)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def instalar(profile_path, runner=None, log=None, port=None) -> tuple:
    """Diagnoses and, if nothing blocks, registers the task. -> (code, lines)."""
    runner = runner or _correr
    lineas = []
    checks = diagnosticar(profile_path, port)
    for c in checks:
        lineas.append(f"  [{c.marca:>8}] {c.nombre}: {c.detalle}")
    if bloquea(checks):
        lineas.append("")
        lineas.append("Nothing was registered: resolve whatever says MISSING "
                      "above first.")
        return 2, lineas

    xml = xml_tarea(profile_path, log=log)
    # UTF-16: schtasks /XML rejects a UTF-8 file with "The task XML is malformed"
    # -- without ever saying that the encoding is the problem.
    fd, tmp = tempfile.mkstemp(suffix=".xml", prefix="vmaxpanel-tarea-")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        # /F: reinstalling replaces the task instead of failing with "already
        # exists", so running --install twice leaves a single task.
        code, salida = runner([_schtasks(), "/Create", "/TN", TAREA,
                               "/XML", tmp, "/F"])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    lineas.append("")
    if code == 0:
        lineas.append(f"Task {TAREA} registered: the tray starts at every logon.")
        lineas.append(f"To start it now without logging out: "
                      f"schtasks /Run /TN {TAREA}")
    else:
        lineas.append(f"schtasks failed (code {code}): {salida.strip()}")
        if "denied" in salida.lower() or "denegado" in salida.lower():
            # schtasks says "Access is denied" and nothing else. The cause is always
            # the same: the task runs elevated (see RunLevel in the XML) and
            # registering that requires an elevated console.
            lineas.append("The task runs elevated, so registering it needs an "
                          "administrator console: open PowerShell as "
                          "administrator and run the same thing again.")
    return code, lineas


# How a panel process is recognised by its command line. By command line and not by
# nombre de imagen: son todos python.exe/pythonw.exe/powershell.exe, y matar por
# name would take out any of the user's own scripts. There was already a scare.
_MIOS = ("vmaxpanel.tray", "-m vmaxpanel", "vmaxpanel\\sensors.ps1",
         "vmaxpanel/sensors.ps1", "sensors.ps1")


def _procesos_windows():
    """[(pid, command line)] of the candidates. Interpreters and PowerShell only."""
    try:
        import psutil
    except ImportError:
        return []
    fuera = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        nombre = (p.info.get("name") or "").lower()
        if not nombre.startswith(("python", "pythonw", "powershell", "pwsh")):
            continue
        # None and not "" when it could not be read: the caller has to be able to
        # tell "not the panel's" from "I could not see what it is".
        cmd = p.info.get("cmdline")
        fuera.append((p.info["pid"], " ".join(cmd) if cmd else None))
    return fuera


def _matar(pid) -> bool:
    try:
        import psutil
        proc = psutil.Process(int(pid))
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        return True
    except Exception:
        return False


def es_mio(linea, yo=None) -> bool:
    """True if that command line is one of the panel's processes.

    It does NOT try to recognise the caller here. That used to be done by looking
    for the literal "--parar", which stopped working the moment the English alias
    shipped: `python -m vmaxpanel --stop` matches "-m vmaxpanel", so the sweep
    killed its own process at exit 15 -- before printing anything, and before
    reaching the sidecar, which is the one thing the command exists to get rid of.

    A flag spelling is a proxy for "this is me"; the pid is the fact. parar()
    skips its own pid instead.
    """
    return any(m.lower() in linea.lower() for m in _MIOS)


def parar(runner=None, matar=None, listar=None) -> tuple:
    """Really brings the panel down: stops the task and kills the processes.
    -> (code, lines).

    It exists because `daemon/stop.ps1` **does not know the new engine** -- it
    sweeps by command line against `panel\\.py|sensors\\.ps1` -- and cannot be
    touched: `daemon/` is the byte-identical rollback path. On top of that the
    scheduled task starts the tray again at the next logon, so killing the process
    alone is not enough.

    El sidecar se mata aparte y a proposito: un `powershell.exe` corriendo sensors.ps1
    that outlives everything keeps LibreHardwareMonitorLib.dll locked and blocks
    moving or deleting the directory. It is this project's recurring trap.
    """
    runner = runner or _correr
    matar = matar or _matar
    listar = listar or _procesos_windows
    lineas = []

    code, salida = runner([_schtasks(), "/End", "/TN", TAREA])
    if code == 0:
        lineas.append(f"task {TAREA} stopped")
    elif "cannot find" in salida.lower() or "no existe" in salida.lower():
        lineas.append(f"task {TAREA} was not registered")
    else:
        # /End on a task that is registered but not running also returns != 0. That
        # is not a failure: the final state is the one that was asked for.
        lineas.append(f"task {TAREA} was not running")

    muertos, opacos, tercos = [], [], []
    yo = os.getpid()
    for pid, linea in listar():
        if pid == yo:
            continue                    # never the process running this sweep
        if linea is None:
            # No command line: psutil could not read it. That happens with a
            # higher-integrity process -- the tray runs elevated -- and skipping it
            # silently is what made it say "there were no processes" with the panel
            # running.
            opacos.append(pid)
            continue
        if not es_mio(linea):
            continue
        if matar(pid):
            muertos.append(pid)
            lineas.append(f"  killed {pid}: {linea[:70]}")
        else:
            tercos.append(pid)
    if muertos:
        lineas.append(f"{len(muertos)} panel process(es) brought down")
    else:
        # "none were left" and not "there were none": /End already takes the task's
        # process tree, so by the time we enumerate there may be nothing left.
        # Saying "there were none" sounds like it was never there.
        lineas.append("no panel processes were left running "
                      "(stopping the task takes its own with it)")
    if tercos:
        lineas.append(f"could not kill {', '.join(map(str, tercos))}: run this from an "
                      f"administrator console (the tray runs elevated)")
    if opacos:
        lineas.append(f"there are {len(opacos)} process(es) I cannot inspect "
                      f"({', '.join(map(str, opacos))}): run this from an administrator "
                      f"console to see whether they belong to the panel")
    lineas.append(f"to bring it back up: schtasks /Run /TN {TAREA}")
    return 0, lineas


def desinstalar(runner=None) -> tuple:
    """Deletes the task. Idempotent: its not existing is not a failure."""
    runner = runner or _correr
    code, salida = runner([_schtasks(), "/Delete", "/TN", TAREA, "/F"])
    if code == 0:
        return 0, [f"Task {TAREA} deleted. The panel no longer starts on its own."]
    if "cannot find" in salida.lower() or "no existe" in salida.lower():
        # The final state is the one the user asked for, so it is not an error:
        # uninstalling twice has to exit 0 both times.
        return 0, [f"Task {TAREA} was not registered; there was nothing to delete."]
    return code, [f"schtasks failed (code {code}): {salida.strip()}"]
