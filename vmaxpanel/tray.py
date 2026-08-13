"""Icono de bandeja: la cara visible de la app.

Win32 crudo por ctypes, sin `pystray` ni `pywin32`. Dos razones: la app se
reparte a otros duenos del panel y una dependencia menos es una instalacion
menos que puede fallar, y pystray es LGPL-3.0 -- este proyecto ya cuida que
nada no-redistribuible entre al paquete.

Aca no hay logica de negocio: todo lo que el menu hace se lo pide a
`PanelApp` (vmaxpanel/app.py), que se prueba entero sin ventanas. Este modulo
es la unica parte del proyecto que no tiene tests automaticos, precisamente
porque es solo pegamento contra la API de Windows.
"""
import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .app import PanelApp
from .cli import status_path
from .logsetup import run_with_log

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_TRAY = 0x0400 + 1            # WM_APP: mensaje propio del icono

# Esperas entre intentos de agregar el icono. La primera es 0 -- el caso normal entra
# de una -- y el resto cubre la carrera de arranque al logon, donde la bandeja de
# Windows puede no estar lista todavia. Suman ~7 s y despues se rinde avisando.
ESPERAS_ICONO = (0, 0.5, 1, 1.5, 2, 2)

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x0040
LR_LOADFROMFILE = 0x0010
SM_CXSMICON, SM_CYSMICON = 49, 50

ICONO = Path(__file__).resolve().parent / "assets" / "vmaxpanel.ico"

MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED = 0x0000, 0x0800, 0x0001, 0x0008
MF_POPUP = 0x0010
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100          # el id vuelve por retorno, no por WM_COMMAND

CMD_STATE = 1001
CMD_TOGGLE = 1002
CMD_EDITOR = 1003
CMD_PROFILE = 1004
CMD_LOG = 1005
CMD_RESTART = 1006
CMD_QUIT = 1007
CMD_EXPORT = 1008
# Los fps van en un rango aparte, CMD_FPS_BASE + indice de la opcion: si se
# solaparan con los ids fijos, elegir un fps ejecutaria otra cosa.
CMD_FPS_BASE = 1100
CMD_PROFILE_BASE = 1200
CMD_BRIGHT_BASE = 1300

# LRESULT es del tamano de un puntero: en 64 bits, c_long (32) TRUNCA el valor
# de retorno. Y sin argtypes declarados, ctypes asume int de 32 bits para cada
# argumento, asi que un LPARAM real de 64 bits explota con "OverflowError: int
# too long to convert" adentro del callback -- donde Python se come la
# excepcion e imprime "Exception ignored", o sea que la ventana deja de
# responder mensajes sin que nada falle a la vista. Lo encontro el log de la
# tarea programada en su primera corrida.
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_void_p,
                               wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
# Sin restype declarado, ctypes asume int de 32 bits y TRUNCA el handle: el
# modulo base queda con un valor que no es el real, y la clase se registra
# contra una instancia inexistente.
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


# El id del mensaje "TaskbarCreated" no es una constante fija: lo asigna Windows y hay
# que pedirlo. Se registra al importar, una sola vez.
WM_TASKBARCREATED = user32.RegisterWindowMessageW("TaskbarCreated")


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


CLAVE_ICONOS = r"Control Panel\NotifyIconSettings"


def _leer_entradas_icono() -> dict:
    """{nombre de subclave: {valor: dato}} de HKCU\\Control Panel\\NotifyIconSettings."""
    import winreg
    fuera = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE_ICONOS) as raiz:
        i = 0
        while True:
            try:
                nombre = winreg.EnumKey(raiz, i)
            except OSError:
                break
            i += 1
            datos = {}
            try:
                with winreg.OpenKey(raiz, nombre) as sub:
                    j = 0
                    while True:
                        try:
                            v, dato, _ = winreg.EnumValue(sub, j)
                        except OSError:
                            break
                        datos[v] = dato
                        j += 1
            except OSError:
                continue
            fuera[nombre] = datos
    return fuera


def _escribir_promovido(subclave, valor):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{CLAVE_ICONOS}\\{subclave}", 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, valor)


def promover_icono(exe, tip_prefijo, leer=None, escribir=None) -> bool:
    """Hace visible el icono en la barra si Windows lo escondio. -> True si lo cambio.

    **Windows 11 esconde TODO icono nuevo**: lo manda al menu de iconos ocultos y no a
    la barra. Verificado en esta maquina: la entrada estaba con el tooltip "VMax Panel"
    y sin `IsPromoted`, y el usuario nunca vio el icono. Para una app cuya UNICA
    interfaz es ese icono, quedar escondido es quedar sin interfaz -- no hay pausa, ni
    cambio de perfil, ni editor.

    **Si el valor esta en 0 no se toca:** eso significa que el usuario lo apago a mano
    en Configuracion, y volver a prenderlo en cada arranque seria pelearle a su
    decision. Solo se arregla la ausencia, que es el default de Windows y no una
    eleccion de nadie.

    Nunca levanta: es cosmetico y no puede impedir que la bandeja arranque.
    """
    leer = leer or _leer_entradas_icono
    escribir = escribir or _escribir_promovido
    try:
        for subclave, datos in (leer() or {}).items():
            if str(datos.get("ExecutablePath", "")).lower() != str(exe).lower():
                continue
            if not str(datos.get("InitialTooltip", "")).startswith(tip_prefijo):
                continue
            if "IsPromoted" in datos:
                return False            # ya visible, o apagado a mano: se respeta
            escribir(subclave, 1)
            return True
    except Exception:
        return False
    return False


def _open_with_shell(path):
    """Abre un archivo con la app asociada, sin bloquear la bandeja."""
    try:
        os.startfile(str(path))                                # noqa: S606
    except Exception:
        subprocess.Popen(["cmd", "/c", "start", "", str(path)],
                         creationflags=0x08000000)             # sin ventana


class Tray:
    def __init__(self, app: PanelApp, log_path=None, editor_launcher=None):
        self.app = app
        self.log_path = log_path
        self._editor_launcher = editor_launcher or self._default_editor
        self._hwnd = None
        self._nid = None
        self._editor = None             # el proceso del editor, si hay uno vivo
        # La referencia al WNDPROC tiene que sobrevivir a __init__: si la
        # recolecta el GC, Windows llama a un puntero muerto en el primer
        # mensaje y el proceso se cae sin traceback.
        self._proc = WNDPROC(self._on_message)

    # --- ventana oculta que recibe los mensajes del icono ---

    def _register(self):
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.lpszClassName = "VMaxPanelTray"
        cls.hInstance = kernel32.GetModuleHandleW(None)
        if not user32.RegisterClassW(ctypes.byref(cls)):
            raise OSError("could not register the tray window class")
        self._hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "VMax Panel",
                                            0, 0, 0, 0, 0, None, None,
                                            cls.hInstance, None)
        if not self._hwnd:
            raise OSError("could not create the tray's hidden window")

    def _icon(self):
        """El icono propio, y si falta, el genérico de Windows.

        Se pide al tamano exacto de icono chico (SM_CXSMICON) en vez de
        LR_DEFAULTSIZE: el default carga la capa de 32 px y deja que Windows
        la reduzca, y a 16 px eso convierte las barras en un gris ilegible. El
        .ico trae una capa dibujada para cada resolucion justamente para esto.
        """
        if ICONO.exists():
            cx = user32.GetSystemMetrics(SM_CXSMICON) or 16
            cy = user32.GetSystemMetrics(SM_CYSMICON) or 16
            ruta = ctypes.cast(ctypes.c_wchar_p(str(ICONO)), ctypes.c_void_p)
            h = user32.LoadImageW(None, ruta, IMAGE_ICON, cx, cy,
                                  LR_LOADFROMFILE)
            if h:
                return h
        # IDI_APPLICATION es un id numerico donde la API espera un puntero a
        # nombre (MAKEINTRESOURCE): va como c_void_p, no como int.
        return user32.LoadImageW(None, ctypes.c_void_p(IDI_APPLICATION),
                                 IMAGE_ICON, 0, 0, LR_DEFAULTSIZE)

    def _add_icon(self):
        """Agrega el icono, VERIFICANDO que Windows lo haya aceptado.

        El retorno se ignoraba, y Windows rechaza el NIM_ADD de verdad cuando la
        bandeja todavia no esta lista -- que es exactamente el momento en que la tarea
        arranca esto, al logon. Sin icono no hay menu: ni pausa, ni cambio de perfil,
        ni editor. La app seguiria dibujando y sin ninguna forma de manejarla, y nada
        lo diria.

        Reintenta con esperas cortas porque el caso normal es una carrera de arranque
        que se resuelve en uno o dos segundos.
        """
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon()
        nid.szTip = self._tip()
        self._nid = nid

        self._icono_puesto = False
        for intento, espera in enumerate(ESPERAS_ICONO):
            if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                self._icono_puesto = True
                if intento:
                    print(f"tray icon added on attempt {intento + 1}")
                # Recien despues del NIM_ADD existe la entrada en el registro que hay
                # que promover: Windows la crea al aceptar el icono, con un nombre de
                # subclave que calcula el. Por eso no se puede hacer en --instalar.
                if promover_icono(sys.executable, "VMax Panel"):
                    print("the icon was hidden (the Windows 11 default): "
                          "promoted it to the taskbar")
                    # Windows decide barra vs menu oculto EN EL MOMENTO del NIM_ADD:
                    # promoverlo despues no lo mueve solo. Se borra y se agrega de
                    # nuevo para que la barra lo re-evalue, o el cambio recien se
                    # veria al proximo arranque.
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                return
            if espera:
                time.sleep(espera)
        print("could not add the icon to the tray: Windows rejected NIM_ADD. The "
              "panel keeps drawing, but with no menu. The editor still opens with "
              "'python -m vmaxpanel.editor', and it comes down with "
              "'python -m vmaxpanel --stop'.", file=sys.stderr)

    def _tip(self) -> str:
        st = self.app.state()
        if st.get("paused"):
            return "VMax Panel — paused"
        if not st.get("running"):
            return "VMax Panel — stopped"
        return (f"VMax Panel — {st.get('profile') or 'no profile'}, "
                f"{st.get('frames', 0)} frames")[:127]

    def _refresh_tip(self):
        if self._nid is None:
            return
        self._nid.szTip = self._tip()
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    # --- menu ---

    def _menu(self):
        st = self.app.state()
        menu = user32.CreatePopupMenu()

        panel = st.get("panel", "desconectado")
        if st.get("paused"):
            titulo = "Paused"
        elif not st.get("running"):
            titulo = "Stopped"
        else:
            titulo = f"{panel} · {st.get('frames', 0)} frames"
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, CMD_STATE, titulo)

        # Los problemas van arriba, antes de cualquier accion: si algo anda mal,
        # es lo primero que el usuario tiene que leer al abrir el menu.
        for linea in self._problem_lines():
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, linea)

        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_TOGGLE,
                           "Resume" if st.get("paused") else "Pause (releases the port)")
        user32.AppendMenuW(menu, MF_STRING, CMD_RESTART, "Restart the engine")

        # Submenu de brillo: el motor lo reaplica en cada recarga, asi que no
        # necesita reiniciar nada.
        subb = user32.CreatePopupMenu()
        for cmd, etiqueta, actual in self._brightness_entries():
            user32.AppendMenuW(subb, MF_STRING | (MF_CHECKED if actual else 0),
                               cmd, etiqueta)
        user32.AppendMenuW(menu, MF_STRING | MF_POPUP, subb, "Brightness")

        # Submenu de perfiles: es lo primero que alguien quiere cambiar.
        subp = user32.CreatePopupMenu()
        for cmd, nombre, actual in self._profile_entries():
            user32.AppendMenuW(subp, MF_STRING | (MF_CHECKED if actual else 0),
                               cmd, nombre)
        if self._editor_abierto():
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0,
                               "Profile (close the editor first)")
            user32.DestroyMenu(subp)
        else:
            user32.AppendMenuW(menu, MF_STRING | MF_POPUP, subp, "Profile")

        # Submenu de fps. El panel refresca a 60 Hz; por encima descarta, asi
        # que 60 es el tope y el costo de cada opcion va en su etiqueta.
        sub = user32.CreatePopupMenu()
        for cmd, etiqueta, marcado in self._fps_entries():
            banderas = MF_STRING | (MF_CHECKED if marcado else 0)
            user32.AppendMenuW(sub, banderas, cmd, etiqueta)
        # Con el editor abierto el submenu queda gris: los dos escriben el
        # mismo perfil y el ultimo en guardar se lleva puesto al otro.
        if self._editor_abierto():
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0,
                               "Frames per second (close the editor first)")
            user32.DestroyMenu(sub)
        else:
            user32.AppendMenuW(menu, MF_STRING | MF_POPUP, sub,
                               "Cuadros por segundo")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_EDITOR, "Layout editor…")
        user32.AppendMenuW(menu, MF_STRING, CMD_PROFILE, "Open the profile (JSON)")
        user32.AppendMenuW(menu, MF_STRING, CMD_EXPORT, "Export the profile…")
        flags = MF_STRING if (self.log_path and Path(self.log_path).exists()) else \
            MF_STRING | MF_GRAYED
        user32.AppendMenuW(menu, flags, CMD_LOG, "View the log")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "Exit")
        return menu

    def _show_menu(self):
        menu = self._menu()
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # SetForegroundWindow antes de TrackPopupMenu: sin esto el menu no se
        # cierra al hacer clic afuera, que es el bug clasico de los iconos de
        # bandeja hechos a mano.
        user32.SetForegroundWindow(self._hwnd)
        # TPM_RETURNCMD: el id elegido vuelve como valor de retorno. Sin el,
        # TrackPopupMenu devuelve un BOOL de exito y el codigo de abajo lo
        # trataba como si fuera un id de comando.
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self._hwnd, None)
        user32.DestroyMenu(menu)
        if cmd:
            self._dispatch(cmd)

    def _editor_abierto(self) -> bool:
        return self._editor is not None and self._editor.poll() is None

    MAX_PROBLEMAS = 4
    ANCHO_LINEA = 70

    def _problem_lines(self):
        """Los problemas, recortados para que quepan como entradas de menu.

        Se topean en cuatro mas un contador: veinte avisos convierten el menu en
        un muro ilegible, y el que necesita los veinte abre el log.
        """
        problemas = list(self.app.problems())
        lineas = [f"⚠ {p[:self.ANCHO_LINEA]}" for p in problemas[:self.MAX_PROBLEMAS]]
        resto = len(problemas) - self.MAX_PROBLEMAS
        if resto > 0:
            lineas.append(f"… and {resto} more (see the log)")
        return lineas

    def _brightness_entries(self):
        """[(comando, etiqueta, es_el_actual)] del submenu de brillo."""
        actual = self.app.brightness()
        return [(CMD_BRIGHT_BASE + i, etiqueta, valor == actual)
                for i, (valor, etiqueta) in enumerate(self.app.brightness_options())]

    def _profile_entries(self):
        """[(comando, nombre, es_el_actual)] para el submenu de perfiles."""
        actual = Path(self.app.profile_path)
        salida = []
        for i, p in enumerate(self.app.profiles()):
            salida.append((CMD_PROFILE_BASE + i, Path(p).stem, Path(p) == actual))
        return salida

    def _fps_entries(self):
        """[(comando, etiqueta, marcado)] para el submenu de fps."""
        actual = self.app.fps()
        salida = []
        for i, (valor, etiqueta) in enumerate(self.app.fps_options()):
            salida.append((CMD_FPS_BASE + i, etiqueta, valor == actual))
        return salida

    def _dispatch(self, cmd):
        if CMD_BRIGHT_BASE <= cmd < CMD_BRIGHT_BASE + 16:
            opciones = self.app.brightness_options()
            i = cmd - CMD_BRIGHT_BASE
            if 0 <= i < len(opciones):
                threading.Thread(target=self.app.set_brightness,
                                 args=(opciones[i][0],), daemon=True).start()
            return
        if CMD_PROFILE_BASE <= cmd < CMD_PROFILE_BASE + 32:
            perfiles = self.app.profiles()
            i = cmd - CMD_PROFILE_BASE
            if 0 <= i < len(perfiles):
                # En un thread: set_profile baja el motor y lo vuelve a
                # levantar, lo que incluye esperar al sidecar.
                threading.Thread(target=self.app.set_profile,
                                 args=(perfiles[i],), daemon=True).start()
            return
        if CMD_FPS_BASE <= cmd < CMD_FPS_BASE + 64:
            if self._editor_abierto():
                return              # el editor tiene el perfil en memoria
            opciones = self.app.fps_options()
            i = cmd - CMD_FPS_BASE
            if 0 <= i < len(opciones):
                # En un thread: set_fps() escribe el perfil y el motor lo
                # recarga; bloquear aca congelaria el bombeo de mensajes.
                valor = opciones[i][0]
                threading.Thread(target=self.app.set_fps, args=(valor,),
                                 daemon=True).start()
            return
        if cmd == CMD_TOGGLE:
            # En un thread: pause() hace join del motor y puede tardar lo que
            # tarde el frame en curso. Bloquear aca congela la bandeja entera,
            # porque este es el thread que bombea los mensajes de Windows.
            threading.Thread(target=self.app.toggle, daemon=True).start()
        elif cmd == CMD_RESTART:
            threading.Thread(target=self._restart, daemon=True).start()
        elif cmd == CMD_EDITOR:
            self._editor_launcher()
        elif cmd == CMD_PROFILE:
            _open_with_shell(self.app.profile_path)
        elif cmd == CMD_EXPORT:
            # En un thread: comprimir un fondo de video son megas, y esto corre en
            # el thread que bombea los mensajes de Windows.
            threading.Thread(target=self._exportar, daemon=True).start()
        elif cmd == CMD_LOG and self.log_path:
            _open_with_shell(self.log_path)
        elif cmd == CMD_QUIT:
            user32.DestroyWindow(self._hwnd)

    def _exportar(self):
        """Exporta y abre la carpeta donde quedo.

        Abrir la carpeta es la unica confirmacion posible: la bandeja no tiene
        ventana propia donde escribir un mensaje, y un export silencioso es
        indistinguible de un boton que no hace nada.
        """
        destino, mensaje = self.app.export_profile()
        print(mensaje)
        if destino is not None:
            _open_with_shell(destino.parent)

    def _restart(self):
        self.app.stop()
        self.app.start()

    def _default_editor(self):
        """El editor corre en su propio proceso, y solo uno a la vez.

        Tkinter quiere ser el thread principal y aca el thread principal esta
        bombeando mensajes de Win32: meter los dos en el mismo proceso es la
        receta para un cuelgue. Un proceso aparte tambien significa que si el
        editor se cae, el panel sigue dibujando.

        Uno solo porque dos editores sobre el mismo perfil se pisan los
        guardados -- gana el ultimo y el otro cree que guardo -- y porque el
        temporal de save_raw() tiene nombre fijo, o sea que asume un unico
        escritor. Paso de verdad: quedaron dos ventanas abiertas sobre
        vitals.json.
        """
        if self._editor is not None and self._editor.poll() is None:
            return                      # ya hay uno vivo
        self._editor = subprocess.Popen(
            [sys.executable, "-m", "vmaxpanel.editor",
             "--profile", str(self.app.profile_path)],
            creationflags=0x08000000)

    # --- bombeo de mensajes ---

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            evento = lparam & 0xFFFF
            if evento == WM_RBUTTONUP:
                self._show_menu()
            elif evento == WM_LBUTTONDBLCLK:
                self._editor_launcher()
            else:
                self._refresh_tip()
        elif msg == WM_TASKBARCREATED:
            # Explorer se reinicio (pasa, y no es raro: un cuelgue del shell, un
            # cambio de escala). Windows manda esto a todas las ventanas y CADA app
            # tiene que volver a agregar su icono; el anterior ya no existe. Sin esto
            # el panel sigue dibujando pero el icono no vuelve hasta el proximo logon,
            # o sea que el usuario se queda sin menu.
            self._add_icon()
            self._dispatch(wparam & 0xFFFF)
        elif msg == WM_DESTROY:
            if self._nid is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self):
        self._register()
        self._add_icon()
        self.app.start()
        # Se loguea el arranque igual que el CLI: bajo pythonw esto es lo unico
        # que confirma que la bandeja llego a levantar el motor, y con que
        # metricas se quedo sin servir.
        st = self.app.state()
        print(f"tray up (hwnd={self._hwnd}); profile {st.get('profile')!r}; "
              f"unavailable metrics: {sorted(st.get('unavailable') or {})}")
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            # El motor se baja SIEMPRE, incluso si el bombeo revienta: si no,
            # queda un thread daemon con COM3 tomado hasta que muera el
            # proceso, y el sidecar con el DLL de LHM agarrado.
            self.app.stop()


def main(argv=None) -> int:
    import argparse

    from .cli import default_profile_path

    ap = argparse.ArgumentParser(prog="vmaxpanel-tray")
    ap.add_argument("--profile", type=Path, default=default_profile_path())
    ap.add_argument("--port", help="the panel's COM port; autodetected by default")
    ap.add_argument("--log", type=Path)
    a = ap.parse_args(argv)

    # La bandeja publica su estado a un archivo: es el proceso que de verdad maneja
    # el panel, asi que es el unico que puede contestarle a `--estado`.
    app = PanelApp(a.profile, port=a.port, status_path=status_path())
    tray = Tray(app, log_path=a.log)
    # run_with_log tambien y no solo el log_path del menu: la bandeja corre
    # bajo pythonw.exe, sin consola, asi que sin esto un error al arrancar --
    # incluido el traceback -- no queda escrito en ningun lado.
    run_with_log(a.log, tray.run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
