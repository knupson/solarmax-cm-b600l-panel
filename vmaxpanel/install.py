"""Instalador de primera vez: diagnostico y autostart.

Dos cosas, y ninguna necesita administrador:

1. **Diagnostico.** Dice si estan las dependencias, la DLL de sensores, el
   perfil y el panel, y separa lo que impide funcionar de lo que solo limita
   (ffmpeg falta -> no hay fondos de video; el panel desenchufado -> el motor
   reintenta solo). Es la respuesta a "lo instale y no anda", contestada antes
   de que la pregunta exista.
2. **Autostart.** Registra la tarea programada que levanta la bandeja al
   iniciar sesion.

**Por que una tarea programada y no un servicio.** Un servicio corre en la
sesion 0: desde ahi no se puede mostrar un icono en la bandeja ni abrir el
editor. **Por que XML y no `schtasks /Create /SC ONLOGON`.** Los defaults de
schtasks no arrancan la tarea con la maquina a bateria, la matan si se
desenchufa, y la cortan a las 72 horas. En una notebook eso es el panel
apagandose solo; el XML es la unica forma de desactivar esas tres cosas.

Todo lo que toca el sistema pasa por `runner`, que se inyecta: asi el instalador
entero se prueba sin registrar nada.
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
    """Un item del diagnostico.

    `ok` tiene tres estados a proposito: True anda, False impide funcionar y
    None es opcional -- falta algo que limita pero no rompe. Un booleano
    obligaria a elegir entre bloquear la instalacion por ffmpeg o no mencionarlo,
    y las dos opciones son peores.
    """
    nombre: str
    ok: bool | None
    detalle: str

    @property
    def marca(self) -> str:
        return {True: "ok", False: "FALTA", None: "opcional"}[self.ok]


def bloquea(checks) -> bool:
    """Hay algo que impide funcionar. Los opcionales (ok None) no cuentan."""
    return any(c.ok is False for c in checks)


def _modulo(paquete, importable, para) -> Chequeo:
    """El nombre que se muestra es el de pip, no el de import.

    Son distintos justo en los dos que mas confunden: `pip install Pillow` da
    `import PIL`, y `pip install pyserial` da `import serial`. Un diagnostico que
    dijera "falta PIL" manda al usuario a buscar un paquete que no existe.
    """
    try:
        mod = __import__(importable)
    except ImportError:
        return Chequeo(paquete, False, f"falta: pip install {paquete} ({para})")
    v = getattr(mod, "__version__", "?")
    return Chequeo(paquete, True, f"{v} - {para}")


EN_USO = ("el puerto esta en uso: ya lo tiene otro proceso (la bandeja "
          "corriendo, o LCD Control). Cerra el que sobre; el panel lo maneja uno "
          "a la vez.")

# Por texto y no por tipo: pyserial envuelve el error en SerialException -- que es
# un OSError cualquiera -- y mete el PermissionError original adentro del mensaje.
# Asi que el errno no llega y el tipo no distingue nada.
_TOMADO = ("permissionerror", "acceso denegado", "access is denied", "errno 13")


def _puerto_tomado(e) -> bool:
    """El puerto existe pero lo tiene otro proceso.

    Vale la pena distinguirlo: "Acceso denegado" se lee como un problema de
    permisos y manda al usuario a pelear con el UAC, cuando el caso real -- todas
    las veces -- es que la bandeja ya esta corriendo o quedo abierto LCD Control.
    """
    if isinstance(e, PermissionError):
        return True
    texto = str(e).lower()
    return any(t in texto for t in _TOMADO)


def _detectar_panel(port=None):
    """Abre y cierra el panel. Separado para poder sustituirlo en los tests:
    la maquina que corre la suite no tiene el panel enchufado."""
    link = PanelLink.autodetect(port)
    try:
        link.open()
        return link.serial_number or "sin numero de serie"
    finally:
        link.close()


def diagnosticar(profile_path, port=None) -> list:
    """Todo lo que hace falta para que el panel funcione, en una lista."""
    checks = [
        Chequeo("python", sys.version_info >= (3, 11),
                f"{sys.version.split()[0]} en {sys.executable}"),
        _modulo("Pillow", "PIL", "render de los frames"),
        _modulo("pyserial", "serial", "puerto del panel"),
        _modulo("psutil", "psutil", "CPU, RAM, red y discos"),
    ]
    checks.append(
        Chequeo("sensores", DLL_SENSORES.exists(),
                f"{DLL_SENSORES}" if DLL_SENSORES.exists() else
                f"falta {DLL_SENSORES.name} en {DLL_SENSORES.parent}: sin eso no "
                f"hay temperaturas, voltajes ni RPM"))

    from .render import video
    ffmpeg = video.buscar_ffmpeg()
    checks.append(Chequeo("ffmpeg", True if ffmpeg else None,
                          ffmpeg or f"solo para fondos de video. {video.COMO_INSTALAR}"))

    ruta = Path(profile_path)
    try:
        lay = loader.load(ruta)
        checks.append(Chequeo("perfil", True,
                              f"{ruta.name}: {lay.name!r}, {len(lay.widgets)} widgets"))
    except loader.LayoutError as e:
        checks.append(Chequeo("perfil", False, f"{ruta.name}: {'; '.join(e.errors)}"))
    except OSError as e:
        checks.append(Chequeo("perfil", False, f"no se pudo leer {ruta}: {e}"))

    try:
        checks.append(Chequeo("panel", True, _detectar_panel(port)))
    except (PanelNotFound, OSError) as e:
        # ok None y no False: la tarea corre al logon y el motor reintenta la
        # conexion solo, asi que instalar con el panel desenchufado es legitimo.
        detalle = (EN_USO if _puerto_tomado(e) else
                   f"{e}. La bandeja reintenta sola cuando aparezca.")
        checks.append(Chequeo("panel", None, detalle))
    return checks


# --- tarea programada ---


def _pythonw() -> Path:
    """El interprete sin consola. Con python.exe la tarea abriria una ventana
    negra en cada logon; si no esta, se usa el actual y se ve la ventana."""
    candidato = Path(sys.executable).with_name("pythonw.exe")
    return candidato if candidato.exists() else Path(sys.executable)


def _usuario() -> str:
    dominio = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or "."
    return f"{dominio}\\{os.environ.get('USERNAME', '')}"


def xml_tarea(profile_path, log=None, python=None, usuario=None,
              working_dir=None) -> str:
    """El XML de la tarea, listo para `schtasks /Create /XML`.

    Funcion pura: no lee el entorno si le pasan los valores, asi que el XML se
    verifica entero sin depender de la maquina donde corran los tests.
    """
    python = Path(python) if python else _pythonw()
    usuario = usuario if usuario is not None else _usuario()
    cwd = Path(working_dir) if working_dir else HERE.parent
    args = ["-u", "-m", "vmaxpanel.tray", "--profile", str(Path(profile_path))]
    if log:
        args += ["--log", str(Path(log))]
    # escape() y no format() a mano: una ruta con & o < rompe el XML, y
    # "C:\Juegos & Cosas" es una ruta perfectamente legal.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="{NS}">
  <RegistrationInfo>
    <Description>VMax Panel: maneja el panel del gabinete Solarmax CM-B600L.</Description>
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
      <RunLevel>LeastPrivilege</RunLevel>
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

    Absoluta y no el nombre suelto: el PATH puede tener otro schtasks adelante,
    y esto registra algo que va a correr en cada logon.
    """
    raiz = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(raiz) / "System32" / "schtasks.exe")


def _correr(argv):
    p = subprocess.run(argv, capture_output=True, text=True,
                       creationflags=SIN_VENTANA)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def instalar(profile_path, runner=None, log=None, port=None) -> tuple:
    """Diagnostica y, si nada bloquea, registra la tarea. -> (codigo, lineas)."""
    runner = runner or _correr
    lineas = []
    checks = diagnosticar(profile_path, port)
    for c in checks:
        lineas.append(f"  [{c.marca:>8}] {c.nombre}: {c.detalle}")
    if bloquea(checks):
        lineas.append("")
        lineas.append("No se registro nada: primero hay que resolver lo que dice "
                      "FALTA arriba.")
        return 2, lineas

    xml = xml_tarea(profile_path, log=log)
    # UTF-16: schtasks /XML rechaza un archivo UTF-8 con "The task XML is
    # malformed" -- sin decir que el problema es la codificacion.
    fd, tmp = tempfile.mkstemp(suffix=".xml", prefix="vmaxpanel-tarea-")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        # /F: reinstalar reemplaza la tarea en vez de fallar con "already
        # exists", asi que correr --instalar dos veces deja una sola tarea.
        code, salida = runner([_schtasks(), "/Create", "/TN", TAREA,
                               "/XML", tmp, "/F"])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    lineas.append("")
    if code == 0:
        lineas.append(f"Tarea {TAREA} registrada: la bandeja arranca en cada "
                      f"inicio de sesion.")
        lineas.append(f"Para arrancarla ahora sin reiniciar sesion: "
                      f"schtasks /Run /TN {TAREA}")
    else:
        lineas.append(f"schtasks fallo (codigo {code}): {salida.strip()}")
    return code, lineas


def desinstalar(runner=None) -> tuple:
    """Borra la tarea. Idempotente: que no exista no es una falla."""
    runner = runner or _correr
    code, salida = runner([_schtasks(), "/Delete", "/TN", TAREA, "/F"])
    if code == 0:
        return 0, [f"Tarea {TAREA} borrada. El panel ya no arranca solo."]
    if "cannot find" in salida.lower() or "no existe" in salida.lower():
        # El estado final es el que el usuario pidio, asi que no es un error:
        # desinstalar dos veces tiene que salir 0 las dos.
        return 0, [f"La tarea {TAREA} no estaba registrada; no habia nada que borrar."]
    return code, [f"schtasks fallo (codigo {code}): {salida.strip()}"]
