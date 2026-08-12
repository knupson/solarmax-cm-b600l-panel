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
from ctypes import wintypes
from pathlib import Path

from .app import PanelApp
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

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x0040

MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED = 0x0000, 0x0800, 0x0001, 0x0008
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100          # el id vuelve por retorno, no por WM_COMMAND

CMD_STATE = 1001
CMD_TOGGLE = 1002
CMD_EDITOR = 1003
CMD_PROFILE = 1004
CMD_LOG = 1005
CMD_RESTART = 1006
CMD_QUIT = 1007

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


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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
            raise OSError("no se pudo registrar la clase de ventana de la bandeja")
        self._hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "VMax Panel",
                                            0, 0, 0, 0, 0, None, None,
                                            cls.hInstance, None)
        if not self._hwnd:
            raise OSError("no se pudo crear la ventana oculta de la bandeja")

    def _icon(self):
        # IDI_APPLICATION es un id numerico donde la API espera un puntero a
        # nombre (MAKEINTRESOURCE): va como c_void_p, no como int.
        return user32.LoadImageW(None, ctypes.c_void_p(IDI_APPLICATION),
                                 IMAGE_ICON, 0, 0, LR_DEFAULTSIZE)

    def _add_icon(self):
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon()
        nid.szTip = self._tip()
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid

    def _tip(self) -> str:
        st = self.app.state()
        if st.get("paused"):
            return "VMax Panel — en pausa"
        if not st.get("running"):
            return "VMax Panel — detenido"
        return (f"VMax Panel — {st.get('profile') or 'sin perfil'}, "
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
            titulo = "En pausa"
        elif not st.get("running"):
            titulo = "Detenido"
        else:
            titulo = f"{panel} · {st.get('frames', 0)} frames"
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, CMD_STATE, titulo)

        error = st.get("last_error")
        if error:
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, f"⚠ {error[:60]}")
        faltan = st.get("unavailable") or {}
        if faltan:
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0,
                               f"sin datos: {', '.join(sorted(faltan))[:60]}")

        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_TOGGLE,
                           "Reanudar" if st.get("paused") else "Pausar (suelta el puerto)")
        user32.AppendMenuW(menu, MF_STRING, CMD_RESTART, "Reiniciar el motor")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_EDITOR, "Editor de layout…")
        user32.AppendMenuW(menu, MF_STRING, CMD_PROFILE, "Abrir el perfil (JSON)")
        flags = MF_STRING if (self.log_path and Path(self.log_path).exists()) else \
            MF_STRING | MF_GRAYED
        user32.AppendMenuW(menu, flags, CMD_LOG, "Ver el log")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "Salir")
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

    def _dispatch(self, cmd):
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
        elif cmd == CMD_LOG and self.log_path:
            _open_with_shell(self.log_path)
        elif cmd == CMD_QUIT:
            user32.DestroyWindow(self._hwnd)

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
        elif msg == WM_COMMAND:
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
        print(f"bandeja arriba (hwnd={self._hwnd}); perfil {st.get('profile')!r}; "
              f"metricas no disponibles: {sorted(st.get('unavailable') or {})}")
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
    ap.add_argument("--port", help="COM del panel; por defecto se autodetecta")
    ap.add_argument("--log", type=Path)
    a = ap.parse_args(argv)

    app = PanelApp(a.profile, port=a.port)
    tray = Tray(app, log_path=a.log)
    # run_with_log tambien y no solo el log_path del menu: la bandeja corre
    # bajo pythonw.exe, sin consola, asi que sin esto un error al arrancar --
    # incluido el traceback -- no queda escrito en ningun lado.
    run_with_log(a.log, tray.run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
