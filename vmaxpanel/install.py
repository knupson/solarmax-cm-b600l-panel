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


# El unico paso de la instalacion que no se puede automatizar: son DLL de terceros
# (MPL-2.0 y MIT) que este repo no redistribuye. Decir "falta X" sin decir de donde se
# saca deja al que recibe el repo en el mismo lugar que estaba.
COMO_SENSORES = (
    "opcional. Sin esto el panel anda igual (reloj, carga de CPU, temp y VCORE por "
    "GSA1, RAM, discos, procesos) pero NO hay GPU, temperatura por nucleo, potencia "
    "del paquete, temperatura de discos ni RPM de fans. Para tenerlos: baja "
    "LibreHardwareMonitor de https://github.com/LibreHardwareMonitor/"
    "LibreHardwareMonitor/releases y copia LibreHardwareMonitorLib.dll Y HidSharp.dll "
    "(las dos: sin HidSharp al lado, Open() falla) a vmaxpanel/lib/")

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
        # ok None y no False: verificado en un clon limpio del repo -- los DLL estan
        # gitignoreados, asi que es lo que recibe otro dueno del panel -- SIN el DLL el
        # panel dibuja igual: reloj, carga de CPU, temp y VCORE (esos salen de GSA1, no
        # del DLL), RAM, discos con tamanos reales, procesos. Marcarlo FALTA hacia que
        # --instalar se negara a instalar, o sea que el que recibia el repo no podia
        # arrancar NADA por unos sensores opcionales.
        Chequeo("sensores", True if DLL_SENSORES.exists() else None,
                f"{DLL_SENSORES}" if DLL_SENSORES.exists() else COMO_SENSORES))

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
      <!-- Elevada, y no por comodidad: GSA1 (temperaturas, voltajes, RPM) y el
           SMART de los SSD no se leen sin elevacion. Sin esto el panel arranca
           igual pero le faltan justo los sensores que no salen de ningun otro
           lado. Es tambien lo que necesita el registro: una consola elevada. -->
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
        if "denied" in salida.lower() or "denegado" in salida.lower():
            # schtasks dice "Access is denied" y nada mas. La causa es siempre la
            # misma: la tarea corre elevada (ver RunLevel en el XML) y registrar
            # eso pide una consola elevada.
            lineas.append("La tarea corre elevada, asi que registrarla necesita "
                          "una consola de administrador: abri PowerShell como "
                          "administrador y corre lo mismo de nuevo.")
    return code, lineas


# Como se reconoce un proceso del panel por su linea de comandos. Por linea y no por
# nombre de imagen: son todos python.exe/pythonw.exe/powershell.exe, y matar por
# nombre se lleva puesto cualquier script del usuario. Ya hubo un susto con eso.
_MIOS = ("vmaxpanel.tray", "-m vmaxpanel", "vmaxpanel\\sensors.ps1",
         "vmaxpanel/sensors.ps1", "sensors.ps1")


def _procesos_windows():
    """[(pid, linea de comandos)] de los candidatos. Solo interpretes y PowerShell."""
    try:
        import psutil
    except ImportError:
        return []
    fuera = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        nombre = (p.info.get("name") or "").lower()
        if not nombre.startswith(("python", "pythonw", "powershell", "pwsh")):
            continue
        # None y no "" cuando no se pudo leer: quien llama tiene que poder
        # distinguir "no es del panel" de "no pude ver que es".
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
    """True si esa linea de comandos es un proceso del panel.

    Excluye el proceso que hace la pregunta: `python -m vmaxpanel --parar` matchea
    "-m vmaxpanel" y se suicidaria antes de terminar de bajar el resto.
    """
    baja = linea.lower()
    if "--parar" in baja:
        return False
    return any(m.lower() in baja for m in _MIOS)


def parar(runner=None, matar=None, listar=None) -> tuple:
    """Baja el panel de verdad: para la tarea y mata los procesos. -> (codigo, lineas).

    Existe porque `daemon/stop.ps1` **no conoce al motor nuevo** -- barre por linea de
    comandos contra `panel\\.py|sensors\\.ps1` -- y no se puede tocar: `daemon/` es la
    vuelta atras byte-identica de toda la fase. Ademas la tarea programada vuelve a
    levantar la bandeja en el siguiente logon, asi que matar el proceso solo no alcanza.

    El sidecar se mata aparte y a proposito: un `powershell.exe` corriendo sensors.ps1
    que sobrevive se queda con LibreHardwareMonitorLib.dll tomado y bloquea mover o
    borrar el directorio. Es la trampa recurrente de este proyecto.
    """
    runner = runner or _correr
    matar = matar or _matar
    listar = listar or _procesos_windows
    lineas = []

    code, salida = runner([_schtasks(), "/End", "/TN", TAREA])
    if code == 0:
        lineas.append(f"tarea {TAREA} detenida")
    elif "cannot find" in salida.lower() or "no existe" in salida.lower():
        lineas.append(f"la tarea {TAREA} no estaba registrada")
    else:
        # /End con la tarea registrada pero no corriendo tambien devuelve != 0. No es
        # una falla: el estado final es el que se pidio.
        lineas.append(f"la tarea {TAREA} no estaba corriendo")

    muertos, opacos, tercos = [], [], []
    for pid, linea in listar():
        if linea is None:
            # Sin linea de comandos: psutil no la pudo leer. Pasa con un proceso de
            # mayor integridad -- la bandeja corre elevada -- y saltearlo en silencio
            # es lo que hacia decir "no habia procesos" con el panel andando.
            opacos.append(pid)
            continue
        if not es_mio(linea):
            continue
        if matar(pid):
            muertos.append(pid)
            lineas.append(f"  matado {pid}: {linea[:70]}")
        else:
            tercos.append(pid)
    if muertos:
        lineas.append(f"{len(muertos)} proceso(s) del panel bajados")
    else:
        # "no quedaron" y no "no habia": /End ya se lleva el arbol de la tarea, asi
        # que para cuando se enumera puede no quedar nada. Decir "no habia" suena a
        # que nunca estuvo.
        lineas.append("no quedaron procesos del panel corriendo "
                      "(la tarea se lleva los suyos al detenerse)")
    if tercos:
        lineas.append(f"no pude matar {', '.join(map(str, tercos))}: corrlo desde una "
                      f"consola de administrador (la bandeja corre elevada)")
    if opacos:
        lineas.append(f"hay {len(opacos)} proceso(s) que no puedo inspeccionar "
                      f"({', '.join(map(str, opacos))}): corrlo desde una consola de "
                      f"administrador para ver si son del panel")
    lineas.append(f"para volver a levantarlo: schtasks /Run /TN {TAREA}")
    return 0, lineas


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
